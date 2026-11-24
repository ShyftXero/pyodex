"""Assemble a loadable Chrome extension from a pyodex project directory.

Project layout (input):

    myext/
    ├── pyodex.toml        # extension config
    ├── main.py           # Python entry point (runs inside Pyodide)
    ├── main.html         # optional: markup injected into the overlay <body>
    ├── overlay.css       # optional: styles for the overlay page
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
from typing import Any

from . import PYODIDE_VERSION
from .deps import (
    HTTP_SHIM_PACKAGES,
    PYODIDE_CORE_FILES,
    ensure_pyodide,
    ensure_url_asset,
)
from .packages import (
    Pin,
    abi_from_lock,
    ensure_in_store,
    link_cow,
    lock,
    pyodide_constraints,
    record_digests,
    script_dependencies,
    uv_platform,
    verify_against_lock,
)
from .runtimes import TierPlan, cache_patterns, choose, requirements_for

# Code that needs a manifest permission to work at all.
PERMISSION_HINTS = {
    "chrome.alarms": "alarms",
    "chrome.contextMenus": "contextMenus",
    "chrome.notifications": "notifications",
    "chrome.cookies": "cookies",
    "chrome.downloads": "downloads",
    "chrome.bookmarks": "bookmarks",
    "chrome.history": "history",
    "chrome.identity": "identity",
    "chrome.declarativeNetRequest": "declarativeNetRequest",
    "chrome.scripting": "scripting",
    "chrome.tabs.capture_visible_tab": "activeTab",
    "chrome.dnr": "declarativeNetRequest",
    "chrome.dnr.test": "declarativeNetRequestFeedback",   # testMatchOutcome lives here
    "webRequest.": "webRequest",
}



# ---- shared emitters ------------------------------------------------------ #
# Each artefact below has exactly one writer. Before this, three build paths rendered the
# same templates from hand-copied placeholder dicts, and one of them registered neither of
# the two relay files it wrote. The rule: if you write a file, append it to `resources` in
# the same function, and let the manifest read `resources` rather than re-deriving.


def _needs_bg(cfg: ExtConfig) -> bool:
    """Is a service worker needed? One predicate, read by the writer and the manifest."""
    return bool((cfg.page_cache and cfg.page_cache_hosts) or cfg.runtime_base != "bundled"
                or cfg.engine == "shared" or cfg.ui == "engine")


def _needs_relay(cfg: ExtConfig) -> bool:
    """The MAIN-world fetch shim + its isolated relay: only when page caching is on."""
    return bool(cfg.page_cache and cfg.page_cache_hosts)


def _app_ctx(cfg: ExtConfig, *, page: str = "overlay.html", scripts: str = "") -> dict:
    """Placeholders shared by injector.js, bg.js and the overlay page — one dictionary."""
    return {
        "NAME": cfg.name, "SLUG": cfg.slug, "LABEL": json.dumps(cfg.name),
        "URL_FILTER": json.dumps(cfg.url_filter), "BOOT_MODE": json.dumps(cfg.boot),
        "IDLE_TIMEOUT_MS": str(int(cfg.idle_timeout) * 1000),
        "REAP_HIDDEN_MS": str(int(cfg.reap_hidden_after) * 1000),
        "MAX_LIVE": str(int(cfg.max_live)), "TRIGGERS": json.dumps(cfg.triggers),
        "ESCAPE_HATCH": "true" if cfg.escape_hatch else "false",
        "URL_RULES": json.dumps([r for t in cfg.triggers for r in (t.get("url_rules") or [])]),
        "ENGINE_UI": "true" if cfg.ui == "engine" else "false",
        "OVERLAY_PAGE": page, "DEV": "true" if cfg.dev else "false", "SCRIPTS": scripts,
        "REMOTE_RUNTIME": "true" if cfg.runtime_base != "bundled" else "false",
        "BUNDLED_ASSETS": json.dumps(cfg.assets),
        "RUNTIME_PATTERNS": json.dumps(cache_patterns(cfg.runtimes)),
        "RUNTIME_PINS": json.dumps(cfg.runtime_pins),
    }


def _emit_injector(out: Path, cfg: ExtConfig, resources: list[str], *, page: str = "overlay.html") -> None:
    (out / "injector.js").write_text(
        _render((TEMPLATES / "injector.js").read_text(), _app_ctx(cfg, page=page)))
    resources.append("injector.js")


PAGE_BODY_NAMES = ("main.html", "overlay.body.html")


def read_page_body(project: Path, cfg: ExtConfig) -> str:
    """The overlay markup: `main.html`, else the historical `overlay.body.html`.

    Implicit config only works when it is inspectable, so the name pair
    (`main.py` + `main.html`) is the convention and the older name stays valid.
    """
    raw = cfg.overlay_body
    if not raw:
        for name in PAGE_BODY_NAMES:
            path = project / name
            if path.exists():
                raw = path.read_text()
                break
    if not raw:
        return ""

    from .pagecheck import prepare

    body, problems = prepare(raw, project, cfg.entry, cfg.python_files)
    if problems:
        detail = "\n".join(f"  {p}" for p in problems)
        raise ValueError(f"page check failed:\n{detail}")
    return body


def _emit_overlay_page(out: Path, project: Path, cfg: ExtConfig, resources: list[str],
                       *, page: str = "overlay.html", scripts: str = "") -> None:
    body = read_page_body(project, cfg)
    css = cfg.overlay_css or ((project / "overlay.css").read_text()
                              if (project / "overlay.css").exists() else "")
    (out / page).write_text(_render((TEMPLATES / "overlay.html").read_text(), {
        **_app_ctx(cfg, page=page, scripts=scripts), "BODY": body, "CSS": css}))
    resources.append(page)


def _emit_cache_surface(out: Path, cfg: ExtConfig, resources: list[str]) -> None:
    if not cfg.cache:
        return
    for name in ("cache.js", "options.html", "options.js", "popup.html", "popup.js"):
        (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
        resources.append(name)


def _emit_worker(out: Path, cfg: ExtConfig, project: Path, resources: list[str]) -> None:
    """bg.js: the event bridge and the runtime-blob cache relay."""
    if not _needs_bg(cfg):
        return
    ctx = {**_app_ctx(cfg), "EVENTS": json.dumps(_declared_events(project, cfg))}
    (out / "bg.js").write_text(_render((TEMPLATES / "bg.js").read_text(), ctx))
    resources.append("bg.js")


def _emit_relay(out: Path, cfg: ExtConfig, resources: list[str]) -> None:
    """The MAIN-world fetch shim and its isolated relay."""
    if not _needs_relay(cfg):
        return
    (out / "pagebridge.js").write_text((TEMPLATES / "pagebridge.js").read_text())
    (out / "pagemain.js").write_text(_render(
        (TEMPLATES / "pagemain.js").read_text(),
        {"PAGE_CACHE_HOSTS": json.dumps(cfg.page_cache_hosts)}))
    resources += ["pagebridge.js", "pagemain.js"]


def _emit_engine_host(out: Path, cfg: ExtConfig, resources: list[str]) -> None:
    """The SharedWorker host and its per-tab agent, or the shared-engine document."""
    names: tuple[str, ...] = ()
    if cfg.ui == "engine":
        names += ("engine_worker.js", "agent.html", "agent.js")
    if cfg.engine == "shared":
        names += ("engine.html", "engine.js")
    for name in names:
        (out / name).write_text(_render((TEMPLATES / name).read_text(), {"NAME": cfg.name}))
        resources.append(name)


def _patch_manifest(out: Path, **updates: Any) -> None:
    """The one other sanctioned manifest change — sandbox registration."""
    path = out / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(updates)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


_GLOB_CHARS = set("*[]")


def _finish(out: Path, project: Path, cfg: ExtConfig, resources: list[str]) -> None:
    """The single exit every build path passes through.

    Everything universal happens here exactly once: the store-required icon, the manifest,
    and a check that every file the manifest names was actually emitted.

    Four build paths used to each write their own manifest, and one of them shipped a
    manifest naming an icon it never wrote. Chrome rejects that silently — no content
    script, no overlay, no error — so the fix is not "add the icon in the fourth place",
    it is "have one place, and make it verify".
    """
    # 1. the icon the manifest will name (128px is required for a store listing)
    if cfg.icon:
        source = project / cfg.icon
        if not source.is_file():
            raise FileNotFoundError(f"icon = {cfg.icon!r} is declared but missing from {project}")
        shutil.copy2(source, out / cfg.icon)
        if cfg.icon not in resources:
            resources.append(cfg.icon)
    elif (Path(__file__).parent / "assets" / "icon-128.png").is_file():
        shutil.copy2(Path(__file__).parent / "assets" / "icon-128.png", out / "icon-128.png")
        if "icon-128.png" not in resources:
            resources.append("icon-128.png")

    # 2. the manifest
    manifest = _manifest(cfg, resources, _derived_permissions(project, cfg),
                         (project / "popup.py").is_file(), (project / "panel.py").is_file())
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # 3. prove the manifest is not lying
    missing = sorted(n for n in _referenced_files(manifest) if not (out / n).exists())
    if missing:
        raise RuntimeError(
            f"manifest names {len(missing)} file(s) this build did not emit: {missing} — "
            "Chrome rejects the whole extension when that happens, so it fails here instead"
        )


def _referenced_files(manifest: dict) -> set[str]:
    """Every concrete file path the manifest points at (globs and URLs excluded)."""
    refs: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value and value.endswith((".js", ".css", ".html", ".png",
                                                               ".svg", ".wasm", ".json")):
            refs.add(value)

    for cs in manifest.get("content_scripts", []):
        for key in ("js", "css"):
            for item in cs.get(key, []) or []:
                add(item)
    background = manifest.get("background") or {}
    for item in background.get("scripts", []) or []:
        add(item)
    add(background.get("service_worker"))
    add(manifest.get("action", {}).get("default_popup"))
    add((manifest.get("options_ui") or {}).get("page"))
    add((manifest.get("side_panel") or {}).get("default_path"))
    for icon in (manifest.get("icons") or {}).values():
        add(icon)
    return refs


def _derived_permissions(project: Path, cfg: ExtConfig) -> list[str]:
    """Add the manifest permissions the app's code actually needs.

    Hand-editing permissions is exactly the boilerplate this framework exists to
    remove: if the code says `chrome.alarms`, the manifest needs "alarms".
    """
    try:
        src = "\n".join(
            (project / f).read_text()
            for f in [cfg.entry, *cfg.python_files]
            if (project / f).is_file()
        )
    except OSError:
        return []
    found = []
    for needle, perm in PERMISSION_HINTS.items():
        if needle in src and perm not in found:
            found.append(perm)
    return found


def _uses_dom(project: Path, cfg: ExtConfig) -> bool:
    """Does the app touch the page or draw UI? Decides whether it needs a page host."""
    import ast

    DOM_NAMES = {"Overlay", "Page", "page", "ext_page", "document", "pyodex_mp"}
    files = [project / cfg.entry] + [project / f for f in cfg.python_files]
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in DOM_NAMES:
                return True
            if isinstance(node, ast.Attribute) and node.attr in {"query", "set_value", "set_text", "button", "card"}:
                return True
    return False


def _boot_plan(project: Path, cfg: ExtConfig) -> None:
    """Say what this app actually needs, instead of making the developer guess.

    An event-only app (chrome.* and listeners, no DOM) wants one interpreter and no
    per-tab injection. A DOM app needs a page host. Saying so at build time is
    cheaper than discovering it from a memory profile.
    """
    events = _declared_events(project, cfg)
    dom = _uses_dom(project, cfg)
    if not dom and events:
        hint = "event-only app: one interpreter is enough"
        if cfg.boot in ("auto", "trigger", "visible"):
            hint += (f" — boot = {cfg.boot!r} pays a per-tab interpreter for nothing; "
                     'consider boot = "user" or engine = "shared"')
        print(f"plan: {hint} ({len(events)} event(s): {', '.join(events)})")
    elif not dom:
        print("plan: no DOM and no events declared — this app may not need a page at all")
    elif events:
        print(f"plan: DOM + {len(events)} event(s) ({', '.join(events)})")
    else:
        print("plan: DOM only (no browser events declared)")


def _declared_events(project: Path, cfg: ExtConfig) -> list[str]:
    """Find `chrome.on("ns.event", fn)` calls so the worker can register them.

    MV3 only delivers events to listeners registered at top level, so this has to
    happen at build time rather than when Python asks.
    """
    import ast

    found: list[str] = []
    files = [project / cfg.entry] + [project / f for f in cfg.python_files]
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "on"):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "chrome"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                name = node.args[0].value
                if name not in found:
                    found.append(name)
    return found

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
    scripts: list[str] = field(default_factory=list)  # optional escape hatch: run app-authored JS
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
    sandbox: bool = True  # rustpython tier: host it in an MV3 sandbox page (needs eval)
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
    escape_hatch: bool = True  # show a launcher when a trigger declares no match
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
        sandbox=ext.get("sandbox", True),
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
        escape_hatch=bool(ext.get("escape_hatch", True)),
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
    if cfg.triggers and cfg.boot in ("user", "visible"):
        # Respect the ceiling, but say what it costs: the app declared where it belongs
        # and will still sit behind a click everywhere.
        print(f"note: {len(cfg.triggers)} trigger(s) declared but boot = {cfg.boot!r} — "
              'the app waits for a click on every page; "trigger" boots only where a '
              "declaration matches")
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


def _manifest(cfg: ExtConfig, resources: list[str], extra_perms: list[str] | None = None,
              python_popup: bool = False, python_panel: bool = False) -> dict:
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
    m["icons"] = {"128": cfg.icon or "icon-128.png"}
    if python_popup or cfg.cache and cfg.popup:
        m["action"] = {"default_popup": "popup.html", "default_title": cfg.name}
    perms: list[str] = []
    if cfg.cache:
        m["options_ui"] = {"page": "options.html", "open_in_tab": True}
        perms.append("storage")  # chrome.storage holds the cache opt-out list
    if cfg.engine == "shared" or cfg.ui == "engine":
        perms.append("offscreen")  # hosts the single shared interpreter
    if python_panel:
        # side_panel needs its permission before the permissions list is written.
        m["side_panel"] = {"default_path": "sidepanel.html"}
        perms.append("sidePanel")
    for perm in extra_perms or []:
        if perm not in perms:
            perms.append(perm)
    if ((cfg.page_cache and cfg.page_cache_hosts) or cfg.runtime_base != "bundled"
            or cfg.engine == "shared" or cfg.ui == "engine"):
        m["background"] = {"service_worker": "bg.js", "type": "module"}
    if perms:
        m["permissions"] = perms
    # Declare content scripts from what was actually emitted, not from a condition that
    # resembles the build's. These used to be added whenever `perms` was non-empty, so
    # `page_cache = false` shipped a manifest naming two files it never wrote — Chrome
    # rejects the whole extension silently in that case.
    if "pagebridge.js" in resources:
        # The shim needs the page's own fetch, so it runs in the MAIN world; the relay
        # stays isolated because MAIN-world code cannot use chrome.* APIs.
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagebridge.js"], "run_at": "document_start"})
    if "pagemain.js" in resources:
        m["content_scripts"].append(
            {"matches": cfg.matches, "js": ["pagemain.js"], "run_at": "document_start", "world": "MAIN"})
    return m


RUSTPYTHON_UNSUPPORTED = {
    "js": "RustPython's wasm build has no `js` module, so there is no JS bridge",
    "pyodex_runtime": "the Pyodide runtime is not available on the RustPython tier",
    "pyodex_natural": "the Pyodide shim layer is not available on the RustPython tier",
}


def _guard_rustpython_imports(project: Path, cfg: ExtConfig) -> None:
    """Fail the build, with guidance, when an app imports something this tier lacks.

    Discovering `import js` at runtime as a bare ModuleNotFoundError is technically
    loud but useless: the message says nothing about the tier or what to use instead.
    """
    import ast

    files = [project / cfg.entry] + [project / f for f in cfg.python_files]
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in RUSTPYTHON_UNSUPPORTED:
                    raise RuntimeError(
                        f"{f.name} imports `{name}`, which the RustPython tier cannot provide:\n"
                        f"  {RUSTPYTHON_UNSUPPORTED[name]}.\n"
                        "Use `pyodex_rp` (status, count, text_of, set_attr) for DOM access, "
                        "or choose runtime = \"pyodide\" for the full Python surface "
                        "(Page, Overlay, chrome.*, sockets)."
                    )


def _plan_runtime(cfg: ExtConfig, requirements: list[str], project: Path) -> TierPlan:
    """Pick the cheapest tier that can run this app, or refuse clearly."""
    needs = requirements_for(
        dependencies=requirements,
        packages=cfg.pyodide_packages,
        sockets=_imports_any(project, cfg, ("socket", "ssl")),
        crypto=_imports_any(project, cfg, ("hashlib", "hmac", "cryptography")),
        full_stdlib=bool(cfg.pyodide_packages),
    )
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
            if isinstance(node, ast.Import) and any(
                    a.name.split(".")[0] in modules for a in node.names):
                return True
            if isinstance(node, ast.ImportFrom) and node.module and (
                    node.module.split(".")[0] in modules):
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
        from .version import chrome_version
        from .version import datever as _datever

        cfg.version = chrome_version(_datever())

    # Only what is always emitted. Everything else registers itself in _emit_*(), so a
    # name here can never point at a file this build skipped — the engine files used to be
    # listed unconditionally, which lit up the invariant test as "registered but absent".
    resources: list[str] = ["bootstrap.js", "pyodide/*", "app/*", "wheels/*"]

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
        mp_resources = ["bootstrap.js", "pyodex_mp.py", "app/*", "micropython/*"]
        _emit_overlay_page(out, project, cfg, mp_resources, scripts="")
        _emit_injector(out, cfg, mp_resources)
        _emit_cache_surface(out, cfg, mp_resources)
        _emit_worker(out, cfg, project, mp_resources)
        _emit_relay(out, cfg, mp_resources)
        _finish(out, project, cfg, mp_resources)
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
        tier_resources = ["bootstrap.js", shim, "app/*", f"{dist_dir}/*"]
        _emit_overlay_page(out, project, cfg, tier_resources, page=page, scripts="")
        _emit_injector(out, cfg, tier_resources, page=page)
        _emit_cache_surface(out, cfg, tier_resources)
        _emit_worker(out, cfg, project, tier_resources)
        _emit_relay(out, cfg, tier_resources)
        _finish(out, project, cfg, tier_resources)
        print(f"built {runtime} tier ({cfg.slug})")

    if cfg.runtime == "rustpython":
        _guard_rustpython_imports(project, cfg)
    if cfg.runtime == "rustpython":
        # RustPython's glue calls eval, and MV3 extension pages cannot allow
        # unsafe-eval. A manifest sandbox page can — at the cost of an opaque
        # origin and no chrome.* APIs, so the widget UI lives in that page.
        _plan_runtime(cfg, [], project)
        page = "sandbox.html" if cfg.sandbox else "overlay.html"
        _tier_build("rustpython", "rustpython", ("rustpython_wasm.js", "rustpython_wasm_bg.wasm"),
                    "pyodex_rp.py", "rp_bootstrap.js", page=page)
        if not cfg.sandbox:
            # No sandbox means no eval: the glue's `new Function` must be stubbed.
            from .deps import stub_new_function

            n = stub_new_function(out / "rustpython" / "rustpython_wasm.js")
            print(f"rustpython: new Function stubbed ({n} site) — no sandbox")
        if cfg.sandbox:
            _patch_manifest(out, sandbox={"pages": ["sandbox.html"]})
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
    _boot_plan(project, cfg)
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
    python_popup = (project / "popup.py").is_file()
    if python_popup:
        _copy(project / "popup.py", app / "popup.py")
    python_panel = (project / "panel.py").is_file()
    if python_panel:
        _copy(project / "panel.py", app / "panel.py")
    for rel in cfg.python_files:
        _copy(project / rel, app / Path(rel).name)
    py_manifest = sorted(str(p.relative_to(app)) for p in app.rglob("*") if p.is_file())
    (app / "files.json").write_text(json.dumps(py_manifest))

    # 4. Extra assets
    for rel in cfg.assets:
        _copy(project / rel, out / rel)
        resources.append(rel)

    # 5. Runtime helpers (Python)
    for helper in ("pyodex.py", "pyodex_runtime.py", "pyodex_natural.py"):
        shutil.copy2(RUNTIME / helper, out / helper)
        resources.append(helper)
    _emit_cache_surface(out, cfg, resources)
    if python_popup:
        # popup.py wins over the static cache popup: same interpreter, own page.
        (out / "popup.html").write_text((TEMPLATES / "popup_py.html").read_text().replace("{{NAME}}", cfg.name))
        if "popup.html" not in resources:
            resources.append("popup.html")
    if python_panel:
        (out / "sidepanel.html").write_text((TEMPLATES / "popup_py.html").read_text().replace("{{NAME}}", cfg.name))
        if "sidepanel.html" not in resources:
            resources.append("sidepanel.html")
    _emit_worker(out, cfg, project, resources)
    _emit_engine_host(out, cfg, resources)
    _emit_relay(out, cfg, resources)

    # 6. Templates — one writer per file
    _emit_overlay_page(out, project, cfg, resources)
    (out / "bootstrap.js").write_text(
        _render((TEMPLATES / "bootstrap.js").read_text(), _app_ctx(cfg)))
    _emit_injector(out, cfg, resources)

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

    _finish(out, project, cfg, resources)
    return out


def zip_dir(built: Path, out: Path | None = None) -> Path:
    """Zip an already-built extension dir."""
    out = out or built.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in built.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(built))
    return out


def store_report(built: Path) -> list[tuple[bool, str, str]]:
    """What a Chrome Web Store reviewer would check, checked here instead.

    Returns (ok, check, detail). "Ready to submit" is a claim, so it gets a test rather
    than a sentence: the two hard blockers are remote code and a missing icon.
    """
    import json

    manifest_path = built / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as e:
        return [(False, "manifest.json", f"unreadable: {e}")]

    checks: list[tuple[bool, str, str]] = []
    version = str(manifest.get("version") or "")
    checks.append((bool(version) and version[0].isdigit(), "version",
                   version or "missing (the store rejects an empty version)"))
    checks.append((manifest.get("manifest_version") == 3, "manifest_version",
                   str(manifest.get("manifest_version"))))
    checks.append((bool(manifest.get("description")), "description",
                   (manifest.get("description") or "missing")[:60]))

    # Remote code: Pyodide fetched from a CDN is remote code by the store's definition.
    bundled = (built / "pyodide" / "pyodide.mjs").exists()
    checks.append((bundled, "runtime is bundled (no remote code)",
                   "pyodide/ present" if bundled else
                   "pyodide/ absent — a CDN runtime is remote code and will be rejected"))

    icon = manifest.get("icons", {}).get("128")
    checks.append((bool(icon) and (built / icon).exists(), "128x128 icon",
                   icon or "missing (required for a store listing)"))

    perms = manifest.get("permissions") or []
    broad = [p for p in perms if p in ("<all_urls>", "tabs", "webRequestBlocking")]
    checks.append((True, "permissions", ", ".join(perms) or "none"
                   + (f"  ← justify: {', '.join(broad)}" if broad else "")))

    total = sum(f.stat().st_size for f in built.rglob("*") if f.is_file())
    checks.append((total < 2 * 1024**3, "size", f"{total / 1024**2:.1f} MB uncompressed"))
    return checks


def zip_extension(target: Path, out: Path | None = None) -> Path:
    return zip_dir(build_extension(target), out)
