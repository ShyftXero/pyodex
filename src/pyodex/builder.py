"""Assemble a loadable Chrome extension from a pyodex project directory.

Project layout (input):

    myext/
    ├── pyodex.toml        # extension config
    ├── main.py           # Python entry point (runs inside Pyodide)
    ├── overlay.css       # optional: styles for the overlay page
    ├── overlay.body.html # optional: markup injected into overlay <body>
    └── <extra files / packages listed in pyodex.toml>

Output (dist/<slug>/) is a flat unpacked extension ready for "Load unpacked".
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import PYODIDE_VERSION
from .deps import HTTP_SHIM_PACKAGES, PYODIDE_CORE_FILES, ensure_pyodide, ensure_url_asset
from .packages import (Pin, abi_from_lock, ensure_in_store, link_cow, lock, pyodide_constraints,
                       record_digests, script_dependencies, uv_platform, verify_against_lock)
from .runtimes import TierPlan, cache_patterns, choose, requirements_for

TEMPLATES = Path(__file__).parent / "templates"
RUNTIME = Path(__file__).parent / "runtime"


@dataclass
class ExtConfig:
    name: str
    version: str = ""  # empty -> datever in Chrome form, stamped at build time
    description: str = ""
    matches: list[str] = field(default_factory=lambda: ["<all_urls>"])
    entry: str = "main.py"
    python_files: list[str] = field(default_factory=list)  # extra .py files / packages
    scripts: list[str] = field(default_factory=list)  # classic JS loaded in overlay before Python
    assets: list[str] = field(default_factory=list)  # extra files copied verbatim
    vendor: dict[str, str] = field(default_factory=dict)  # filename -> URL
    pyodide_version: str = PYODIDE_VERSION
    pyodide_packages: list[str] = field(default_factory=list)  # extra pure-Python PyPI packages to bundle
    http: bool = True  # bundle requests/urllib shims (pyodide-http, requests, urllib3, ...)
    host_permissions: list[str] = field(default_factory=list)  # cross-origin fetch; defaults to `matches`
    url_filter: str = ""  # optional substring the page URL must contain
    icon: str = ""
    overlay_css: str = ""  # inline alternative to overlay.css (single-script mode)
    overlay_body: str = ""  # inline alternative to overlay.body.html
    dev: bool = True  # show the traceback panel; false = console only
    natural: bool = True  # install pyodex_natural shims (socket/ssl/input/sleep/persistent fs/pip)
    cache: bool = True  # digest-verified blob cache + per-site opt-out options page
    # Ordered, cheapest first. The build picks the first tier that satisfies what
    # the app declared; `runtime` forces one.
    runtimes: list[str] | None = None
    runtime: str = ""
    # Extra blob URLs we can verify and therefore cache for other pages too.
    runtime_pins: dict[str, str] = field(default_factory=dict)
    # Serve *other pages'* Pyodide CDN requests from this extension's cache.
    # Restricted to these hosts, and disabled per site by the opt-out list.
    page_cache: bool = True
    page_cache_hosts: list[str] = field(default_factory=lambda: ["cdn.jsdelivr.net"])
    # "bundled" ships the whole runtime. A URL base (e.g. .../pyodide/v314.0.8/full)
    # ships only the JS loader and streams the large binaries through the cache.
    runtime_base: str = "bundled"
    popup: bool = True  # action popup showing cache status, linking to options
    # "local": one interpreter per tab (isolation, per-tab DOM, higher memory).
    # "shared": one interpreter for the browser in an offscreen document.
    engine: str = "local"
    # "local": UI in a per-tab overlay iframe (has its own interpreter).
    # "engine": tabs hold no interpreter at all; the shared engine drives them.
    ui: str = "local"
    # Boot policy: "user" (launcher click), "visible" (boot when the tab is
    # visible), "auto" (boot on every page load). Extensions that must act on
    # page load set "auto"; idle tabs should stay "user".
    boot: str = "user"  # "user" | "visible" | "auto" | "trigger"
    idle_timeout: int = 600  # "user" mode: seconds of inactivity before freeing the interpreter
    reap_hidden_after: int = 300  # "visible"/"auto": seconds hidden before freeing it (0 = never)
    max_live: int = 0  # cap for automatic boots (visible/auto) only; 0 = unlimited
    # Declarative triggers: boot when the page matches, without user interaction.
    # Each entry: {"url"?: glob, "selector"?: css}. Both present -> both must match.
    triggers: list[dict] = field(default_factory=list)
    declarations: dict = field(default_factory=dict)  # extracted @on_load/@watch_for/@on_event

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


def _boot_mode(ext: dict, entry_source: str = "") -> str:
    """Resolve the boot ceiling.

    Explicit `boot` wins. Otherwise the entry script can ask for load-time
    execution by using `@on_load`, which is a declaration rather than a
    config flag someone copies without thinking.
    """
    mode = ext.get("boot")
    if mode is None:
        if ext.get("lazy") is not None:
            return "user" if ext["lazy"] else "auto"
        if "@on_load" in entry_source or "on_load(" in entry_source:
            print("boot: entry uses @on_load -> defaulting to boot = \"visible\" "
                  "(bounded by reap_hidden_after)")
            return "visible"
        return "user"
    if mode not in ("user", "visible", "auto", "trigger"):
        raise ValueError(f"boot must be user|visible|auto|trigger, got {mode!r}")
    return mode


def _config_from_data(data: dict, *, default_name: str, default_entry: str) -> ExtConfig:
    ext = data.get("extension", {})
    return ExtConfig(
        name=ext.get("name", default_name),
        version=ext.get("version", ""),
        description=ext.get("description", ""),
        matches=ext.get("matches", ["<all_urls>"]),
        entry=ext.get("entry", default_entry),
        python_files=ext.get("python_files", []),
        scripts=ext.get("scripts", []),
        assets=ext.get("assets", []),
        vendor=data.get("vendor", {}),
        pyodide_version=ext.get("pyodide_version", PYODIDE_VERSION),
        pyodide_packages=ext.get("pyodide_packages", []),
        http=ext.get("http", True),
        host_permissions=ext.get("host_permissions", ext.get("matches", ["<all_urls>"])),
        url_filter=ext.get("url_filter", ""),
        icon=ext.get("icon", ""),
        overlay_css=ext.get("overlay_css", ""),
        overlay_body=ext.get("overlay_body", ""),
        dev=ext.get("dev", True),
        natural=ext.get("natural", True),
        cache=ext.get("cache", True),
        runtimes=ext.get("runtimes"),
        runtime=ext.get("runtime", ""),
        runtime_pins=data.get("runtime_pins", {}),
        page_cache=ext.get("page_cache", True),
        page_cache_hosts=ext.get("page_cache_hosts", ["cdn.jsdelivr.net"]),
        runtime_base=ext.get("runtime_base", "bundled"),
        popup=ext.get("popup", True),
        engine=ext.get("engine", "local"),
        ui=ext.get("ui", "local"),
        boot=_boot_mode(ext),
        idle_timeout=ext.get("idle_timeout", 600),
        reap_hidden_after=ext.get("reap_hidden_after", 300),
        max_live=ext.get("max_live", 0),
        triggers=ext.get("triggers", []),
    )


def _apply_declarations(project: Path, cfg: ExtConfig, ext: dict) -> ExtConfig:
    """Derive boot policy from the app's own decorators when config is silent.

    Python declares intent (``@on_load``, ``@watch_for``); the build turns that
    into how the page behaves. Config still wins when set explicitly.
    """
    from .declarations import extract, summarise

    paths = [project / cfg.entry] + [project / f for f in cfg.python_files]
    decls = extract([p for p in paths if p.is_file()])
    cfg.declarations = decls.to_dict()
    for problem in decls.problems:
        print(f"warning: {problem}")

    if not cfg.triggers and decls.triggers:
        cfg.triggers = decls.triggers

    if ext.get("boot") is None and ext.get("lazy") is None:
        if decls.triggers:
            cfg.boot = "trigger"
        elif decls.on_load:
            cfg.boot = "visible"
    print(f"declarations: {summarise(decls)} -> boot = {cfg.boot!r}"
          + (f", {len(cfg.triggers)} trigger(s)" if cfg.triggers else ""))
    return cfg


def resolve_project(target: Path) -> tuple[Path, ExtConfig]:
    """Accept a project dir (pyodex.toml) or a single script (PEP 723 `[tool.pyodex]`).

    Returns (project_root, config). For a single script, project_root is the
    script's directory and `entry` is the script filename.
    """
    if target.is_dir():
        cfg_path = target / "pyodex.toml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"no pyodex.toml in {target}")
        data = tomllib.loads(cfg_path.read_text())
        cfg = _config_from_data(data, default_name=target.name, default_entry="main.py")
        return target, _apply_declarations(target, cfg, data.get("extension", {}))
    if target.suffix == ".py":
        from .packages import read_pep723

        meta = read_pep723(target)
        tool = meta.get("tool", {}).get("pyodex", {})
        # [tool.pyodex] is flat; [tool.pyodex.vendor] holds URL assets.
        data = {"extension": {k: v for k, v in tool.items() if k != "vendor"}, "vendor": tool.get("vendor", {})}
        cfg = _config_from_data(data, default_name=target.stem, default_entry=target.name)
        return target.parent, _apply_declarations(target.parent, cfg, data["extension"])
    raise FileNotFoundError(f"{target}: not a project dir or .py script")


def load_config(project: Path) -> ExtConfig:
    return resolve_project(project)[1]


def _render(text: str, ctx: dict[str, str]) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _manifest(cfg: ExtConfig, resources: list[str]) -> dict:
    m = {
        "manifest_version": 3,
        "name": cfg.name,
        "version": cfg.version,
        "description": cfg.description,
        "content_scripts": [
            {"matches": cfg.matches, "js": ["injector.js"], "run_at": "document_idle"}
        ],
        "web_accessible_resources": [{"resources": sorted(set(resources)), "matches": cfg.matches}],
        "content_security_policy": {
            "extension_pages": "script-src 'self' 'wasm-unsafe-eval'; object-src 'self';"
        },
    }
    if cfg.host_permissions:
        m["host_permissions"] = cfg.host_permissions
    if cfg.icon:
        m["icons"] = {"128": cfg.icon}
    if cfg.cache and cfg.popup:
        m["action"] = {"default_popup": "popup.html", "default_title": cfg.name}
    perms: list[str] = []
    if cfg.cache:
        m["options_ui"] = {"page": "options.html", "open_in_tab": True}
        perms.append("storage")  # chrome.storage holds the cache opt-out list
    if cfg.engine == "shared" or cfg.ui == "engine":
        perms.append("offscreen")  # hosts the single shared interpreter
    if ((cfg.page_cache and cfg.page_cache_hosts) or cfg.runtime_base != "bundled"
            or cfg.engine == "shared" or cfg.ui == "engine"):
        m["background"] = {"service_worker": "bg.js", "type": "module"}
    if perms:
        m["permissions"] = perms
        # The shim needs the page's own fetch, so it runs in the MAIN world; the
        # relay stays isolated because MAIN-world code cannot use chrome.* APIs.
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagebridge.js"], "run_at": "document_start"})
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagemain.js"], "run_at": "document_start", "world": "MAIN"})
    return m


def _plan_runtime(cfg: ExtConfig, requirements: list[str], project: Path) -> TierPlan:
    """Pick the cheapest tier that can run this app, or refuse clearly."""
    needs = requirements_for(
        dependencies=requirements,
        packages=cfg.pyodide_packages,
        sockets=_imports_any(project, cfg, ("socket", "ssl")),
        crypto=_imports_any(project, cfg, ("hashlib", "hmac", "cryptography")),
        full_stdlib=bool(cfg.pyodide_packages),
    )
    names = [cfg.runtime] if cfg.runtime else cfg.runtimes
    from .runtimes import RUNTIMES

    considered = list(cfg.runtimes or ["micropython", "rustpython", "pyodide"])

    if cfg.runtime:
        forced = RUNTIMES.get(cfg.runtime)
        if forced is None:
            raise ValueError(f"unknown runtime {cfg.runtime!r}; known: {sorted(RUNTIMES)}")
        if not forced.implemented:
            raise RuntimeError(
                f"runtime {forced.name!r} is declared but not implemented yet ({forced.notes}). "
                f"Implemented: {[n for n, r in RUNTIMES.items() if r.implemented]}. "
                "Unset `runtime` to let the build pick the cheapest working tier."
            )
        if not needs <= forced.capabilities:
            raise RuntimeError(
                f"runtime {forced.name!r} cannot satisfy {sorted(needs - forced.capabilities)}"
            )
        return TierPlan(runtime=forced, needs=needs, considered=considered)

    # Default: warn about tiers we cannot run yet, and build with the cheapest
    # implemented one that fits. A declared-but-missing tier must not break a
    # project that never asked for it.
    if cfg.runtimes is None:
        # No explicit list: only a tier whose Python API we ship is safe to pick.
        considered = ["pyodide"]
    usable = [n for n in considered if RUNTIMES.get(n) and RUNTIMES[n].implemented]
    skipped = [n for n in considered if n not in usable]
    if skipped:
        print(f"runtime: skipping not-yet-implemented tier(s) {skipped}")
    if not usable:
        raise RuntimeError(f"none of {considered} is implemented yet")
    return TierPlan(runtime=choose(needs, usable), needs=needs, considered=considered)


def _imports_any(project: Path, cfg: ExtConfig, modules: tuple[str, ...]) -> bool:
    """Cheap AST scan for imports that force a capability."""
    import ast

    files = [project / cfg.entry] + [project / f for f in cfg.python_files]
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name.split(".")[0] in modules for a in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in modules:
                    return True
    return False


def build_extension(target: Path, out: Path | None = None, *, allow_bad: bool = False) -> Path:
    project, cfg = resolve_project(target)
    out = out or (project / "dist" / cfg.slug)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # Stamp the datever before anything can return early: an empty "version" makes
    # the manifest invalid and Chrome refuses to load the extension silently.
    if not cfg.version:
        from .version import chrome_version, datever as _datever

        cfg.version = chrome_version(_datever())

    resources: list[str] = ["overlay.html", "bootstrap.js", "pyodex_runtime.py", "pyodex_natural.py",
                            "pyodide/*", "app/*", "wheels/*", "agent.html", "agent.js",
                            "engine_worker.js", "engine.html", "engine.js"]

    plan_mp = None  # set when the micropython tier is chosen
    def _mp_build() -> None:
        """MicroPython tier: vendored wasm, its own bootstrap, tiny shim."""
        from .deps import ensure_micropython

        mp_src = ensure_micropython()
        mp_dst = out / "micropython"
        mp_dst.mkdir()
        for name in ("micropython.mjs", "micropython.wasm"):
            shutil.copy2(mp_src / name, mp_dst / name)
        (out / "pyodex_mp.py").write_text((RUNTIME / "pyodex_mp.py").read_text())
        (out / "bootstrap.js").write_text(
            _render((TEMPLATES / "mp_bootstrap.js").read_text(), {"NAME": cfg.name}))
        app = out / "app"
        app.mkdir()
        _copy(project / cfg.entry, app / "main.py")
        (app / "files.json").write_text(json.dumps(["main.py"]))
        body = cfg.overlay_body or ((project / "overlay.body.html").read_text()
                                    if (project / "overlay.body.html").exists() else "")
        css = cfg.overlay_css or ((project / "overlay.css").read_text()
                                  if (project / "overlay.css").exists() else "")
        (out / "overlay.html").write_text(_render((TEMPLATES / "overlay.html").read_text(), {
            "NAME": cfg.name, "BODY": body, "CSS": css, "SCRIPTS": "",
        }))
        shutil.copy2(TEMPLATES / "injector.js", out / "injector.js") if False else None
        (out / "injector.js").write_text(_render((TEMPLATES / "injector.js").read_text(), {
            "SLUG": cfg.slug, "URL_FILTER": json.dumps(cfg.url_filter), "BOOT_MODE": json.dumps(cfg.boot),
            "IDLE_TIMEOUT_MS": str(int(cfg.idle_timeout) * 1000),
            "REAP_HIDDEN_MS": str(int(cfg.reap_hidden_after) * 1000),
            "MAX_LIVE": str(int(cfg.max_live)), "TRIGGERS": json.dumps(cfg.triggers),
            "LABEL": json.dumps(cfg.name), "ENGINE_UI": "false", "OVERLAY_PAGE": "overlay.html", "DEV": "true",
        }))
        resources = ["overlay.html", "bootstrap.js", "pyodex_mp.py", "app/*", "micropython/*"]
        # The cache surface is runtime-agnostic; the manifest references these, so
        # a tier that omits them makes Chrome reject the whole extension.
        if cfg.cache:
            for name in ("cache.js", "options.html", "options.js", "popup.html", "popup.js"):
                (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
            resources += ["cache.js", "options.html", "options.js", "popup.html", "popup.js"]
        if (cfg.page_cache and cfg.page_cache_hosts) or cfg.ui == "engine":
            (out / "bg.js").write_text(_render((TEMPLATES / "bg.js").read_text(), {
                "RUNTIME_PATTERNS": json.dumps(cache_patterns(cfg.runtimes)),
                "RUNTIME_PINS": json.dumps(cfg.runtime_pins),
            }))
            resources.append("bg.js")
            (out / "pagebridge.js").write_text((TEMPLATES / "pagebridge.js").read_text())
            (out / "pagemain.js").write_text(_render((TEMPLATES / "pagemain.js").read_text(),
                                                     {"PAGE_CACHE_HOSTS": json.dumps(cfg.page_cache_hosts)}))
            resources += ["pagebridge.js", "pagemain.js"]
        (out / "manifest.json").write_text(json.dumps(_manifest(cfg, resources), indent=2) + "\n")
        print(f"built micropython tier ({cfg.slug})")

    def _tier_build(runtime: str, dist_dir: str, files: tuple[str, ...], shim: str,
                    bootstrap: str, page: str = "overlay.html") -> None:
        """Shared shape for a non-Pyodide tier: vendored wasm + its own bootstrap."""
        from . import deps as _deps

        src = _deps.ensure_micropython() if runtime == "micropython" else _deps.ensure_rustpython()
        dst = out / dist_dir
        dst.mkdir()
        for name in files:
            shutil.copy2(src / name, dst / name)
        shutil.copy2(RUNTIME / shim, out / shim)
        (out / "bootstrap.js").write_text(_render((TEMPLATES / bootstrap).read_text(), {"NAME": cfg.name}))
        app = out / "app"
        app.mkdir()
        _copy(project / cfg.entry, app / "main.py")
        (app / "files.json").write_text(json.dumps(["main.py"]))
        body = cfg.overlay_body or ((project / "overlay.body.html").read_text()
                                    if (project / "overlay.body.html").exists() else "")
        css = cfg.overlay_css or ((project / "overlay.css").read_text()
                                  if (project / "overlay.css").exists() else "")
        page_html = _render((TEMPLATES / "overlay.html").read_text(), {
            "NAME": cfg.name, "BODY": body, "CSS": css, "SCRIPTS": "",
        })
        (out / page).write_text(page_html)
        (out / "injector.js").write_text(_render((TEMPLATES / "injector.js").read_text(), {
            "SLUG": cfg.slug, "URL_FILTER": json.dumps(cfg.url_filter), "BOOT_MODE": json.dumps(cfg.boot),
            "IDLE_TIMEOUT_MS": str(int(cfg.idle_timeout) * 1000),
            "REAP_HIDDEN_MS": str(int(cfg.reap_hidden_after) * 1000),
            "MAX_LIVE": str(int(cfg.max_live)), "TRIGGERS": json.dumps(cfg.triggers),
            "LABEL": json.dumps(cfg.name), "ENGINE_UI": "false", "OVERLAY_PAGE": page,
        }))
        resources = [page, "bootstrap.js", shim, "app/*", f"{dist_dir}/*"]
        # The manifest references these whenever page caching is on; a tier that
        # omits them makes Chrome reject the whole extension (silently).
        if (cfg.page_cache and cfg.page_cache_hosts) or cfg.ui == "engine":
            (out / "bg.js").write_text(_render((TEMPLATES / "bg.js").read_text(), {
                "RUNTIME_PATTERNS": json.dumps(cache_patterns(cfg.runtimes)),
                "RUNTIME_PINS": json.dumps(cfg.runtime_pins),
            }))
            (out / "pagebridge.js").write_text((TEMPLATES / "pagebridge.js").read_text())
            (out / "pagemain.js").write_text(_render((TEMPLATES / "pagemain.js").read_text(),
                                                     {"PAGE_CACHE_HOSTS": json.dumps(cfg.page_cache_hosts)}))
            resources += ["bg.js", "pagebridge.js", "pagemain.js"]
        if cfg.cache:
            for name in ("cache.js", "options.html", "options.js", "popup.html", "popup.js"):
                (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
            resources += ["cache.js", "options.html", "options.js", "popup.html", "popup.js"]
        (out / "manifest.json").write_text(json.dumps(_manifest(cfg, resources), indent=2) + "\n")
        print(f"built {runtime} tier ({cfg.slug})")

    if cfg.runtime == "rustpython":
        # RustPython's glue calls eval, and MV3 extension pages cannot allow
        # unsafe-eval. A manifest sandbox page can — at the cost of an opaque
        # origin and no chrome.* APIs, so the widget UI lives in that page.
        _plan_runtime(cfg, [], project)
        _tier_build("rustpython", "rustpython", ("rustpython_wasm.js", "rustpython_wasm_bg.wasm"),
                    "pyodex_rp.py", "rp_bootstrap.js", page="sandbox.html")
        manifest = json.loads((out / "manifest.json").read_text())
        manifest["sandbox"] = {"pages": ["sandbox.html"]}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return out

    if cfg.runtime == "micropython":
        _plan_runtime(cfg, [], project)  # validates capabilities, raises if forced wrongly
        _mp_build()
        return out

    # 1. Runtime core
    pyo_src = ensure_pyodide(cfg.pyodide_version)
    pyo_dst = out / "pyodide"
    pyo_dst.mkdir()
    remote = cfg.runtime_base != "bundled"
    bundled = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide-lock.json") if remote else PYODIDE_CORE_FILES
    for name in bundled:
        p = pyo_src / name
        if p.exists():
            shutil.copy2(p, pyo_dst / name)

    # 1b. Dependencies: PEP 723 block in entry + pyodex.toml extras + HTTP shims.
    #     Resolved for wasm32-pyodide via uv, pinned in pyodex.lock, stored once
    #     read-only in ~/.cache/pyodex/store and CoW-linked into dist/wheels.
    wheels = out / "wheels"
    wheels.mkdir()
    reqs = list(HTTP_SHIM_PACKAGES) if cfg.http else []
    reqs += [r for r in cfg.pyodide_packages if r not in reqs]
    reqs += [r for r in script_dependencies(project / cfg.entry) if r not in reqs]
    pyodide_lock = json.loads((pyo_src / "pyodide-lock.json").read_text())
    py_ver = pyodide_lock["info"]["python"].rsplit(".", 1)[0]  # "3.14.1" -> "3.14"
    lock_path = project / f"{cfg.slug}.pyodex.lock" if cfg.entry != "main.py" else project / "pyodex.lock"
    platform = uv_platform(abi_from_lock(pyodide_lock))
    pins = lock(lock_path, reqs, py_ver, constraints=pyodide_constraints(pyodide_lock), platform=platform)
    plan = _plan_runtime(cfg, reqs, project)
    print(f"runtime: {plan.describe()}")
    (out / "runtime-plan.json").write_text(json.dumps({
        "runtime": plan.runtime.name,
        "needs": sorted(plan.needs),
        "considered": plan.considered,
        "approx_mb": plan.runtime.approx_mb,
        "implemented": plan.runtime.implemented,
    }, indent=2) + "\n")

    from .compat import check_pins

    check_pins(pins, allow_bad=allow_bad)
    wheel_names, methods, store_paths = [], set(), []
    for pin in pins:
        w = ensure_in_store(pin, pyodide_lock=pyodide_lock, pyodide_version=cfg.pyodide_version)
        store_paths.append(w)
        methods.add(link_cow(w, wheels / w.name))
        wheel_names.append(w.name)
    bad = verify_against_lock(lock_path, store_paths)
    if bad:
        raise RuntimeError("wheel digest mismatch vs lock (delete lock or run --refresh-lock):\n  " + "\n  ".join(bad))
    record_digests(lock_path, store_paths)
    if pins:
        print(f"packages: {len(pins)} wheels linked ({', '.join(sorted(methods))})")
    # micropip ships with Pyodide; bundle so pip_install() works offline-at-build.
    if cfg.natural:
        mp = pyodide_lock["packages"].get("micropip")
        if mp:
            w = ensure_in_store(Pin("micropip", mp["version"]), pyodide_lock=pyodide_lock, pyodide_version=cfg.pyodide_version)
            methods.add(link_cow(w, wheels / w.name))
            wheel_names.append(w.name)
    (wheels / "index.json").write_text(json.dumps({"wheels": wheel_names, "http": cfg.http, "natural": cfg.natural}))

    # 2. Vendored URL assets
    for filename, url in cfg.vendor.items():
        shutil.copy2(ensure_url_asset(url, filename), out / filename)
        resources.append(filename)

    # 3. Python: entry + extra files go under app/
    app = out / "app"
    app.mkdir()
    _copy(project / cfg.entry, app / "main.py")
    for rel in cfg.python_files:
        _copy(project / rel, app / Path(rel).name)
    py_manifest = sorted(str(p.relative_to(app)) for p in app.rglob("*") if p.is_file())
    (app / "files.json").write_text(json.dumps(py_manifest))

    # 4. Extra assets
    for rel in cfg.assets:
        _copy(project / rel, out / rel)
        resources.append(rel)
    if cfg.icon:
        resources.append(cfg.icon)

    # 5. Runtime helper (python)
    shutil.copy2(RUNTIME / "pyodex_runtime.py", out / "pyodex_runtime.py")
    shutil.copy2(RUNTIME / "pyodex_natural.py", out / "pyodex_natural.py")
    if cfg.cache:
        for name in ("cache.js", "options.html", "options.js", "popup.html", "popup.js"):
            (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
    if ((cfg.page_cache and cfg.page_cache_hosts) or cfg.runtime_base != "bundled"
            or cfg.engine == "shared" or cfg.ui == "engine"):
        # Rendered, not copied: the worker carries the runtime blob patterns and
        # any build-pinned digests (so caching covers tiers we do not bundle).
        (out / "bg.js").write_text(_render((TEMPLATES / "bg.js").read_text(), {
            "RUNTIME_PATTERNS": json.dumps(cache_patterns(cfg.runtimes)),
            "RUNTIME_PINS": json.dumps(cfg.runtime_pins),
        }))
    if cfg.ui == "engine":
        # SharedWorker host: one interpreter for the browser, one tiny agent iframe
        # per tab. No offscreen document (it is not even executed in our harness).
        for name in ("engine_worker.js", "agent.html", "agent.js"):
            (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
    if cfg.engine == "shared":
        for name in ("engine.html", "engine.js"):
            (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
    if cfg.page_cache and cfg.page_cache_hosts:
        (out / "pagebridge.js").write_text((TEMPLATES / "pagebridge.js").read_text())
        (out / "pagemain.js").write_text(
            _render((TEMPLATES / "pagemain.js").read_text(),
                    {"PAGE_CACHE_HOSTS": json.dumps(cfg.page_cache_hosts)}))

    # 6. Templates
    body = cfg.overlay_body or ((project / "overlay.body.html").read_text() if (project / "overlay.body.html").exists() else "")
    css = cfg.overlay_css or ((project / "overlay.css").read_text() if (project / "overlay.css").exists() else "")
    script_tags = "\n".join(f'  <script src="{s}"></script>' for s in cfg.scripts)
    ctx = {
        "NAME": cfg.name,
        "BODY": body,
        "CSS": css,
        "SCRIPTS": script_tags,
        "URL_FILTER": json.dumps(cfg.url_filter),
        "SLUG": cfg.slug,
        "LABEL": json.dumps(cfg.name),
        "BOOT_MODE": json.dumps(cfg.boot),
        "IDLE_TIMEOUT_MS": str(int(cfg.idle_timeout) * 1000),
        "REAP_HIDDEN_MS": str(int(cfg.reap_hidden_after) * 1000),
        "MAX_LIVE": str(int(cfg.max_live)),
        "REMOTE_RUNTIME": "true" if cfg.runtime_base != "bundled" else "false",
        "ENGINE_UI": "true" if cfg.ui == "engine" else "false",
        "OVERLAY_PAGE": "overlay.html",
        "DEV": "true" if cfg.dev else "false",
        "TRIGGERS": json.dumps(cfg.triggers),
        "RUNTIME_PATTERNS": json.dumps(cache_patterns(cfg.runtimes)),
        "RUNTIME_PINS": json.dumps(cfg.runtime_pins),
    }
    for t in ("overlay.html", "bootstrap.js", "injector.js"):
        (out / t).write_text(_render((TEMPLATES / t).read_text(), ctx))

    if not cfg.version:
        # datever, in the dotted form Chrome accepts (max 4 parts, <= 65535 each)
        from .version import chrome_version, datever as _datever

        cfg.version = chrome_version(_datever())
    if remote:
        # Pin the exact bytes we expect from the remote base. The lockfile does not
        # describe the core files, and a digest taken from our own verified copy is
        # a stronger claim than anything fetched at runtime.
        import hashlib

        streamed = [n for n in PYODIDE_CORE_FILES if n not in bundled and (pyo_src / n).exists()]
        runtime = {
            "base": cfg.runtime_base.rstrip("/"),
            "files": {n: hashlib.sha256((pyo_src / n).read_bytes()).hexdigest() for n in streamed},
        }
        (out / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        resources.append("runtime.json")
        print(f"runtime: {len(streamed)} file(s) streamed from {runtime['base']}")

    (out / "manifest.json").write_text(json.dumps(_manifest(cfg, resources), indent=2) + "\n")
    return out


def zip_dir(built: Path, out: Path | None = None) -> Path:
    """Zip an already-built extension dir."""
    out = out or built.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in built.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(built))
    return out


def zip_extension(target: Path, out: Path | None = None) -> Path:
    return zip_dir(build_extension(target), out)
