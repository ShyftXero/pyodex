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
    natural: bool = True  # install pyodex_natural shims (socket/ssl/input/sleep/persistent fs/pip)
    cache: bool = True  # digest-verified blob cache + per-site opt-out options page
    # Serve *other pages'* Pyodide CDN requests from this extension's cache.
    # Restricted to these hosts, and disabled per site by the opt-out list.
    page_cache: bool = True
    page_cache_hosts: list[str] = field(default_factory=lambda: ["cdn.jsdelivr.net"])
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
        natural=ext.get("natural", True),
        cache=ext.get("cache", True),
        page_cache=ext.get("page_cache", True),
        page_cache_hosts=ext.get("page_cache_hosts", ["cdn.jsdelivr.net"]),
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
    if cfg.cache:
        m["options_ui"] = {"page": "options.html", "open_in_tab": True}
        # Chrome requires this for chrome.storage, which holds the opt-out list.
        m["permissions"] = ["storage"]
    if cfg.page_cache and cfg.page_cache_hosts:
        m["background"] = {"service_worker": "bg.js", "type": "module"}
        # The shim needs the page's own fetch, so it runs in the MAIN world; the
        # relay stays isolated because MAIN-world code cannot use chrome.* APIs.
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagebridge.js"], "run_at": "document_start"})
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagemain.js"], "run_at": "document_start", "world": "MAIN"})
    return m


def build_extension(target: Path, out: Path | None = None, *, allow_bad: bool = False) -> Path:
    project, cfg = resolve_project(target)
    out = out or (project / "dist" / cfg.slug)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    resources: list[str] = ["overlay.html", "bootstrap.js", "pyodex_runtime.py", "pyodex_natural.py",
                            "pyodide/*", "app/*", "wheels/*"]

    # 1. Pyodide core
    pyo_src = ensure_pyodide(cfg.pyodide_version)
    pyo_dst = out / "pyodide"
    pyo_dst.mkdir()
    for name in PYODIDE_CORE_FILES:
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
        for name in ("cache.js", "options.html", "options.js"):
            (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
    if cfg.page_cache and cfg.page_cache_hosts:
        shutil.copy2(TEMPLATES / "bg.js", out / "bg.js")
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
        "TRIGGERS": json.dumps(cfg.triggers),
    }
    for t in ("overlay.html", "bootstrap.js", "injector.js"):
        (out / t).write_text(_render((TEMPLATES / t).read_text(), ctx))

    if not cfg.version:
        # datever, in the dotted form Chrome accepts (max 4 parts, <= 65535 each)
        from .version import chrome_version, datever as _datever

        cfg.version = chrome_version(_datever())
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
