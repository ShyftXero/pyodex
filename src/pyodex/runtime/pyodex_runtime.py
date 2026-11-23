"""pyodex_runtime: small helpers for Python code running inside the overlay iframe.

Importable from app code as `from pyodex_runtime import ...`. Only depends on
Pyodide's `js` and `pyodide.ffi`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import js
from pyodide.ffi import create_proxy, to_js

# Workers (including the shared engine) have no document/window: resolve these
# lazily so the module imports anywhere Python can run.
def _maybe(name):
    try:
        return getattr(js, name)
    except AttributeError:
        return None


# Re-exported so app code never has to know the runtime underneath. Reaching for
# `pyodide.ffi` from a demo is a framework leak, not an app concern.
try:
    from pyodide.ffi import create_proxy, jsnull, to_js  # noqa: F401
except ImportError:  # pragma: no cover - non-Pyodide tiers
    def create_proxy(fn):  # type: ignore[misc]
        return fn

    def to_js(value, **kw):  # type: ignore[misc]
        return value

    jsnull = None

document = _maybe("document")
window = _maybe("window")

_proxies: list[Any] = []  # keep event proxies alive


def jsobj(d: dict) -> Any:
    """Convert a Python dict to a plain JS object (recursively)."""
    return to_js(d, dict_converter=js.Object.fromEntries)


def js_callback(fn: Callable) -> Any:
    """Wrap a Python function for a JS callback and keep the proxy alive.

    Both forms work:

        req.onsuccess = js_callback(my_fn)

        @js_callback
        def onsuccess(event): ...
    """
    proxy = create_proxy(fn)
    _proxies.append(proxy)
    return proxy


def on(target: Any, event: str) -> Callable[[Callable], Callable]:
    """Decorator: attach `fn` as an event listener and keep the proxy alive."""

    def deco(fn: Callable) -> Callable:
        p = create_proxy(fn)
        _proxies.append(p)
        target.addEventListener(event, p)
        return fn

    return deco


def listen(target: Any, event: str, fn: Callable) -> Any:
    p = create_proxy(fn)
    _proxies.append(p)
    target.addEventListener(event, p)
    return p


def el(tag: str, *, id: str | None = None, text: str | None = None, cls: str | None = None, **style: str) -> Any:
    """Create an element with optional id/text/class and inline styles (snake_case -> camelCase)."""
    e = document.createElement(tag)
    if id:
        e.id = id
    if text is not None:
        e.innerText = text
    if cls:
        e.className = cls
    for k, v in style.items():
        setattr(e.style, _camel(k), v)
    return e


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.title() for w in rest)


def status(text: str | None, error: bool = False) -> None:
    """Show/hide the built-in bottom-right status pill. `None` hides it.

    In engine mode there is no local document (the interpreter is in a worker), so
    this is a no-op: use a badge or a card for per-tab state.
    """
    if document is None:
        return
    s = document.getElementById("pyodex-status")
    if not s:
        return
    if text is None:
        s.classList.add("hidden")
        return
    s.classList.remove("hidden")
    s.textContent = text
    if error:
        s.classList.add("error")
    else:
        s.classList.remove("error")


def save() -> None:
    """Flush the persistent data dir (IndexedDB). Re-exported from pyodex_natural."""
    import pyodex_natural

    pyodex_natural.save()


def pip_install(*packages: str) -> None:
    """Install packages at runtime via micropip. Re-exported from pyodex_natural."""
    import pyodex_natural

    pyodex_natural.pip_install(*packages)


def _engine() -> Any:
    """The shared-engine bridge, if this app is running inside it."""
    eng = getattr(js, "pyodexEngine", None)
    return eng


def engine_mode() -> bool:
    """True when Python runs in the shared engine rather than in a tab's overlay."""
    return _engine() is not None


_ATTACH_HANDLERS: list[Callable] = []
_UI_HANDLERS: dict[str, Callable] = {}


def on_attach(fn: Callable) -> Callable:
    """Called once per tab that attaches, with a Page bound to that tab.

    Engine mode only: one interpreter serves every tab, so per-tab work is
    addressed by tab id instead of by process.
    """
    _ATTACH_HANDLERS.append(fn)
    return fn


def _engine_attached(tab_id: int, url: str) -> None:
    """Called by the engine host when a tab attaches (engine mode)."""
    for fn in list(_ATTACH_HANDLERS):
        try:
            page = Page(tab_id=tab_id)
            result = fn(page, url)
            if asyncio.iscoroutine(result):
                task = asyncio.ensure_future(result)
                task.add_done_callback(_task_done)
        except Exception as e:  # noqa: BLE001
            import traceback as _tb

            _report_exception("".join(_tb.format_exception(type(e), e, e.__traceback__)))


def _engine_ui_event(element_id: str, event: str) -> None:
    fn = _UI_HANDLERS.get(element_id)
    if fn:
        _invoke(fn, event)


def _post(type_: str, payload: Any = None) -> None:
    window.parent.postMessage(jsobj({"pyodex": True, "type": type_, "payload": payload}), "*")


def on_load(fn: Callable) -> Callable:
    """Mark this app as needing load-time execution.

    Using this decorator lets the build infer `boot = "visible"` instead of
    someone hand-setting `boot = "auto"` and paying for it in every tab.
    The function runs immediately (we are already on load).
    """
    _LOAD_HOOKS.append(fn)
    fn()
    return fn


_LOAD_HOOKS: list[Any] = []


class events:
    """Names usable with @on_event. Values are stable strings, matched page-side."""

    PAGE_LOAD = "page.load"
    DOM_READY = "page.dom_ready"
    NAVIGATE = "page.navigate"
    CLICK = "page.click"
    INPUT = "page.input"
    SUBMIT = "page.submit"
    FORM_FOUND = "page.form_found"
    TAB_VISIBLE = "tab.visible"
    TAB_HIDDEN = "tab.hidden"


DECLARATIONS: dict[str, list] = {"watch_for": [], "on_event": [], "urlmatch": []}


def urlmatch(patterns: str | list[str]) -> Callable:
    """Gate this app to pages whose URL matches one of `patterns`.

    Write the pattern you mean; the build classifies it:

        127.0.0.1                  exact host
        *.mysite.com               domain and subdomains
        *                          any host
        https://some.site.com      URL prefix
        https?://.*\\.php          regex (`re:` prefix also works)

    Combine with @watch_for on the same function to require both.
    """
    plist = [patterns] if isinstance(patterns, str) else list(patterns)
    if not plist:
        raise ValueError("urlmatch needs at least one pattern")

    def deco(fn: Callable) -> Callable:
        DECLARATIONS["urlmatch"].append({"patterns": plist, "fn": fn.__name__, "callable": fn})
        return fn

    return deco


def watch_for(selector: str | None = None, *, id: str | None = None, url: str | None = None) -> Callable:
    """Declare that this app matters on pages containing `selector`.

    The page uses this to decide whether to involve the interpreter at all, so
    unrelated tabs stay free. The function runs when a match is first seen.
    """
    sel = selector or (f"#{id}" if id else None)
    if not sel:
        raise ValueError("watch_for needs a selector or id=")

    def deco(fn: Callable) -> Callable:
        DECLARATIONS["watch_for"].append({"selector": sel, "url": url, "fn": fn.__name__, "callable": fn})
        return fn

    return deco


def on_event(event: str, *, selector: str | None = None,
             prevent_default: bool = False) -> Callable:
    """Declare interest in a page event (`events.CLICK`, `events.NAVIGATE`, ...).

    `prevent_default=True` intercepts the event: the page-side listener calls
    `preventDefault()` **synchronously at capture time** and then hands the details to
    Python. That ordering is the whole trick — Python is asynchronous, so a handler that
    decided later could never cancel the navigation. Interception therefore means "the
    browser will not act; Python will", which is exactly why the payload carries the
    resolved `href` and text: the handler needs to know what it just took over.
    """

    def deco(fn: Callable) -> Callable:
        DECLARATIONS["on_event"].append(
            {"event": str(event), "selector": selector, "fn": fn.__name__,
             "prevent_default": bool(prevent_default), "callable": fn})
        return fn

    return deco


def _url_allows(patterns: list[str], url: str) -> bool:
    """Match a URL against raw @urlmatch patterns (mirrors build-time classification)."""
    import fnmatch
    import re as _re
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    for raw in patterns:
        pat = str(raw).strip()
        if pat.startswith("re:"):
            if _re.search(pat[3:], url):
                return True
            continue
        if any(ch in "?()[]|^$\\+" for ch in pat):
            try:
                if _re.search(pat, url):
                    return True
            except _re.error:
                pass
            continue
        if "://" in pat:
            if "*" in pat:
                rx = "^" + "".join(".*" if c == "*" else ("\\" + c if c in ".+?^${}()|[]\\" else c)
                                   for c in pat) + "$"
                if _re.match(rx, url, _re.I):
                    return True
            elif url.lower().startswith(pat.lower()):
                return True
            continue
        p = pat.lower()
        if p == "*":
            return True
        if p.startswith("*."):
            base = p[2:]
            if host == base or host.endswith("." + base):
                return True
        elif fnmatch.fnmatch(host, p):
            return True
    return False


_active_page: "Page | None" = None


def _report_exception(text: str) -> None:
    """Send a Python traceback to the overlay's error panel."""
    try:
        js.pyodexReportError(text)
    except Exception:  # noqa: BLE001
        try:
            js.console.error(text)
        except Exception:  # noqa: BLE001
            pass


def _task_done(task: Any) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    import traceback as _tb

    _report_exception("".join(_tb.format_exception(type(exc), exc, exc.__traceback__)))


def _invoke(fn: Callable, *args: Any) -> None:
    """Call a declared function, surfacing failures from async work.

    A coroutine scheduled with ensure_future dies silently otherwise: it is not a
    JS unhandled rejection and not a window error, so nothing reaches the panel.
    """
    try:
        result = fn(*args)
    except Exception as e:  # noqa: BLE001
        import traceback as _tb

        _report_exception("".join(_tb.format_exception(type(e), e, e.__traceback__)))
        return
    if asyncio.iscoroutine(result):
        task = asyncio.ensure_future(result)
        task.add_done_callback(_task_done)


async def dispatch() -> dict:
    """Call the functions whose declarations match this page.

    Declaring `@watch_for` boots the interpreter, but the function still has to
    be invoked — this is what closes that loop, and what turns `@on_event` into
    real page subscriptions.
    """
    global _active_page
    page = _active_page or Page()
    _active_page = page
    info = await page.info()
    fired: list[str] = []

    for decl in list(DECLARATIONS["watch_for"]):
        if decl.get("url") and not decl["url"]:
            continue
        rules = [d for d in DECLARATIONS["urlmatch"] if d["fn"] == decl["fn"]]
        if rules and not any(_url_allows(r["patterns"], info["url"]) for r in rules):
            continue
        try:
            present = await page.count(decl["selector"])
        except Exception:  # noqa: BLE001
            present = 1  # cannot check: do not silently skip the app
        if present:
            _invoke(decl["callable"])
            fired.append(decl["fn"])

    groups: dict[tuple[str | None, str, bool], list[Callable]] = {}
    for decl in DECLARATIONS["on_event"]:
        event = decl["event"].split(".")[-1]
        key = (decl.get("selector"), event, bool(decl.get("prevent_default")))
        groups.setdefault(key, []).append(decl["callable"])
    for (selector, event, prevent_default), fns in groups.items():
        page.subscribe(selector or "*", [event], throttle_ms=50, prevent_default=prevent_default)

        def handler(payload: Any, fns: list[Callable] = fns) -> None:
            for f in fns:
                _invoke(f, payload)

        page.on_page_event(handler)
        fired.extend(f.__name__ for f in fns)

    try:
        document.body.dataset.pyodexDispatch = ",".join(fired) or "none"
    except Exception:  # noqa: BLE001
        pass
    return {"fired": fired, "url": info["url"]}


def declarations() -> dict:
    """Snapshot of what this app declared, for the host to route on."""
    return {k: list(v) for k, v in DECLARATIONS.items()}


def want_boot(mode: str = "auto") -> None:
    """Ask the host page to boot (or stay) in `mode`.

    Only ever downgrades in practice: the content script refuses anything above
    the configured ceiling, so an app cannot escape a "user" build.
    """
    if mode not in ("user", "visible", "auto"):
        raise ValueError(f"mode must be user|visible|auto, got {mode!r}")
    _post("page.boot", mode)


def keep_alive(seconds: float = 300) -> None:
    """Ask the host to keep this interpreter resident for `seconds`.

    Use before long work (a big fetch, an open socket) so idle reaping cannot
    tear the interpreter down mid-flight. Re-call to extend.
    """
    _post("page.keepalive", int(max(1.0, float(seconds)) * 1000))


def release() -> None:
    """Free this tab's interpreter now (e.g. the work is done). Reload state is lost."""
    _post("page.release")


class Overlay:
    """Convenience wrapper around the overlay document body.

    The overlay iframe is click-through unless told otherwise, because a
    full-viewport iframe that captures pointers blocks the page underneath.
    Interactive UI therefore has to opt in — which `interactive=True` (the
    default) does, by asking the content script to capture pointers for this
    tab. Pass `interactive=False` for effects-only overlays (canvas, particles)
    that must let the page keep working.
    """

    def __init__(self, *, interactive: bool = True) -> None:
        self.interactive = interactive
        if engine_mode():
            # No document here: widgets are created in the tab by the agent.
            self.body = None
            return
        self.body = document.body
        if interactive:
            # Without this the iframe stays pointer-events: none and *no* real
            # click can reach anything we render.
            _post("page.pointer", True)

    def mount(self, element: Any) -> Any:
        self.body.appendChild(element)
        return element

    def _engine_op(self, op: str, args: dict) -> None:
        eng = _engine()
        raw = eng.currentTabId()
        if raw is None:
            # No tab has attached yet (this is import time). A widget created
            # before its tab exists is a no-op rather than a crash.
            return
        # Agent ids are opaque strings ("a1b2c3") — never coerce them.
        # Fire-and-forget: UI creation must not make every app's code async.
        eng.tabOp(raw, op, jsobj(args))

    def button(self, label: str, *, top: int | None = None, left: int | None = None,
               right: int | None = None, bottom: int | None = None, id: str | None = None) -> Any:
        if engine_mode():
            widget_id = id or f"pyodex-btn-{len(_UI_HANDLERS) + 1}"
            self._engine_op("ui_button", {"id": widget_id, "label": label, "top": top or 16, "left": left or 16})
            return _EngineWidget(widget_id)

        b = el("button", id=id, text=label, position="fixed", z_index="10", pointer_events="auto",
               padding="10px 16px", font="600 14px system-ui, sans-serif", color="#fff",
               background="#18181b", border="2px solid #f43f5e", border_radius="8px",
               box_shadow="0 4px 12px rgba(0,0,0,.25)", cursor="pointer")
        for k, v in (("top", top), ("left", left), ("right", right), ("bottom", bottom)):
            if v is not None:
                setattr(b.style, k, f"{v}px")
        return self.mount(b)

    _PANEL_DEFAULTS = dict(position="fixed", pointer_events="auto",
                           background="rgba(24,24,27,.95)", color="#fff", padding="12px",
                           border_radius="8px", font="14px system-ui, sans-serif")
    _POSITION_ONLY = ("position", "pointer_events", "top", "left", "right", "bottom", "width",
                      "height", "z_index", "inset", "display", "margin")

    def panel(self, *, id: str | None = None, cls: str | None = None, **style: str) -> Any:
        """A container.

        Passing `cls` means "style this from CSS", so the visual defaults are dropped —
        inline styles outrank stylesheet rules, and silently clobbering a class is the
        kind of thing that makes a framework's own styling feel unpredictable. Only the
        positioning defaults survive, since a CSS class rarely decides where it sits.
        """
        base = dict(self._PANEL_DEFAULTS) if not cls else {
            k: v for k, v in self._PANEL_DEFAULTS.items() if k in self._POSITION_ONLY
        }
        base.update(style)
        return self.mount(el("div", id=id, cls=cls, **base))

    def card(self, title: str = "", *, id: str | None = None, top: int = 16, left: int = 16,
             width: int = 300, **style: str) -> Any:
        """A titled panel: the shape every demo wants and nobody wants to re-style."""
        if engine_mode():
            widget_id = id or f"pyodex-card-{abs(hash(title)) % 10000}"
            self._engine_op("ui_card", {"id": widget_id, "title": title, "top": top, "left": left,
                                        "width": width})
            return _EngineCard(widget_id, self)
        wrap = self.panel(id=id, top=f"{top}px", left=f"{left}px",
                          width=f"{width}px", padding="0", overflow="hidden", **style)
        head = el("div", text=title, padding="10px 12px", font="600 13px system-ui, sans-serif",
                  background="rgba(244,63,94,.14)", border_bottom="1px solid rgba(255,255,255,.08)")
        body = el("div", padding="12px", font="13px/1.5 system-ui, sans-serif")
        wrap.appendChild(head)
        wrap.appendChild(body)
        wrap.head = head
        wrap.body = body
        return wrap

    def badge(self, text: str, *, color: str = "#f43f5e", top: int = 16, left: int | None = None,
              right: int | None = None) -> Any:
        """Tiny status pill, for counts and one-word state."""
        if engine_mode():
            self._engine_op("ui_badge", {"text": text, "color": color, "top": top,
                                         "right": right, "left": left})
            return _EngineWidget("pyodex-badge")
        pos = {"top": f"{top}px"}
        if right is not None:
            pos["right"] = f"{right}px"
        else:
            pos["left"] = f"{left if left is not None else 16}px"
        return self.mount(el("div", text=text, position="fixed", pointer_events="none",
                             z_index="10", padding="4px 10px", border_radius="999px",
                             font="600 12px system-ui, sans-serif", color="#fff",
                             background=color, box_shadow="0 2px 8px rgba(0,0,0,.3)", **pos))

    def input(self, placeholder: str = "", *, id: str | None = None, parent: Any = None,
              **style: str) -> Any:
        if engine_mode():
            widget_id = id or f"pyodex-input-{abs(hash(placeholder)) % 10000}"
            self._engine_op("ui_input", {"id": widget_id, "placeholder": placeholder})
            return _EngineWidget(widget_id)
        e = el("input", id=id, placeholder=placeholder, padding="8px 10px", width="100%",
               box_sizing="border-box", border_radius="6px", border="1px solid #3f3f46",
               background="#18181b", color="#fff", font="13px system-ui, sans-serif",
               pointer_events="auto", **style)
        (parent.body if parent is not None and hasattr(parent, "body") else self.body).appendChild(e)
        return e

    def layer(self, *, id: str | None = None) -> Any:
        """Full-screen non-interactive container (e.g. for a canvas)."""
        return self.mount(el("div", id=id, position="fixed", inset="0", pointer_events="none"))


class _EngineCard:
    """Card handle in engine mode: setting head/body text sends an op."""

    class _Part:
        def __init__(self, card: "_EngineCard", field: str) -> None:
            self._card, self._field = card, field

        @property
        def innerText(self) -> str:
            return ""

        @innerText.setter
        def innerText(self, value: str) -> None:
            payload = {"id": self._card.id}
            payload[self._field] = str(value)
            self._card.overlay._engine_op("ui_card", payload)

    def __init__(self, widget_id: str, overlay: Any) -> None:
        self.id = widget_id
        self.overlay = overlay
        self.head = _EngineCard._Part(self, "title")
        self.body = _EngineCard._Part(self, "text")


class _EngineWidget:
    """Handle for a widget that lives in the tab while Python lives in the engine."""

    def __init__(self, widget_id: str) -> None:
        self.id = widget_id

    def on_click(self, fn: Callable) -> Callable:
        _UI_HANDLERS[self.id] = fn
        return fn


class Element:
    """A handle on one element of the host page.

    Name the element once instead of repeating a selector at every call site. All
    methods are async because the DOM lives in another realm.
    """

    def __init__(self, page: "Page", selector: str, index: int = 0) -> None:
        self.page, self.selector, self.index = page, selector, index

    async def exists(self) -> bool:
        return bool(await self.page.count(self.selector))

    async def text(self) -> str:
        values = await self.page.text(self.selector)
        return values[self.index] if len(values) > self.index else ""

    async def html(self) -> str:
        values = await self.page.html(self.selector)
        return values[self.index] if len(values) > self.index else ""

    async def attr(self, name: str) -> Any:
        values = await self.page.attr(self.selector, name)
        return values[self.index] if len(values) > self.index else None

    async def value(self) -> Any:
        """The element's *property* value (what a user typed), not its attribute."""
        nodes = await self.page.query(self.selector)
        return nodes[self.index].get("value") if len(nodes) > self.index else None

    async def set_value(self, value: str) -> None:
        await self.page.set_value(self.selector, value, index=self.index)

    async def set_text(self, value: str) -> None:
        await self.page.set_text(self.selector, value, index=self.index)

    async def set_attr(self, name: str, value: Any) -> None:
        await self.page.dom("set_attr", self.selector, attr=name, value=value, index=self.index)

    async def click(self) -> None:
        await self.page.click(self.selector, index=self.index)

    async def scroll_to(self) -> None:
        await self.page.scroll_to(self.selector, index=self.index)

    def __repr__(self) -> str:
        return f"<Element {self.selector}[{self.index}]>"


class Page:
    """Talk to the host page via the content-script bridge (postMessage)."""

    def __init__(self, tab_id: int | None = None) -> None:
        self.tab_id = tab_id
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._event_handlers: list[Any] = []
        self._last_error: str | None = None
        if not engine_mode():
            listen(window, "message", self._on_message)

    @staticmethod
    def _payload_of(data: Any, default: Any = None) -> Any:
        """Decode a message payload, tolerating `undefined`.

        JS `undefined` becomes Python `None`, and `json.loads(None)` raises — which
        surfaced as a PythonError panel over a working page.
        """
        raw = getattr(data, "payload", None)
        if raw is None:
            return default
        text = js.JSON.stringify(raw)
        if text is None:
            return default
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return default

    def _on_message(self, ev: Any) -> None:
        data = ev.data
        try:
            if not data or not data.pyodex:
                return
        except AttributeError:
            return
        if getattr(data, "type", None) == "page.event":
            payload = self._payload_of(data, {})
            for h in list(self._event_handlers):
                h(payload)
            return
        err = getattr(data, "error", None)
        if err:
            self._last_error = str(err)
        fut = self._pending.pop(int(data.id), None) if hasattr(data, "id") else None
        if fut and not fut.done():
            fut.set_result(self._payload_of(data))

    def _send(self, type_: str, payload: Any = None, *, want_reply: bool = False) -> asyncio.Future | None:
        self._next += 1
        fut = asyncio.get_event_loop().create_future() if want_reply else None
        if fut is not None:
            self._pending[self._next] = fut
        window.parent.postMessage(jsobj({"pyodex": True, "type": type_, "id": self._next, "payload": payload}), "*")
        return fut

    async def info(self) -> dict:
        """{'url': ..., 'title': ...} of the host page."""
        return await self._send("page.info", want_reply=True)

    def capture_pointer(self, enabled: bool) -> None:
        """Make the whole overlay capture (True) or pass through (False) pointer events."""
        self._send("page.pointer", enabled)

    def remove_overlay(self) -> None:
        self._send("page.remove")

    # -- host DOM --------------------------------------------------------- #

    async def dom(self, op: str, sel: str = "", **kw: Any) -> Any:
        """Run a DOM op in the host page via the content-script bridge.

        ops: query, count, html, text, attr, set_text, set_html, set_attr,
             set_value, click, scroll_to. Raises PageError on failure.
        """
        if engine_mode():
            # Shared engine: ask the worker to run the op in this Page's tab.
            eng = _engine()
            tab = self.tab_id if self.tab_id is not None else eng.currentTabId()
            reply = await eng.tabOp(tab, op, jsobj({"sel": sel, **kw}))
            data = reply.to_py() if hasattr(reply, "to_py") else dict(reply)
            if not data.get("ok", False):
                raise PageError(str(data.get("error") or "tab op failed"))
            return data.get("payload")

        fut = self._send("page.dom", {"op": op, "sel": sel, **kw}, want_reply=True)
        res = await fut
        # error is carried out-of-band so a Python-side check stays simple
        return self._check(res)

    def _check(self, res: Any) -> Any:
        err = self._last_error
        self._last_error = None
        if err:
            raise PageError(err)
        return res

    async def query(self, sel: str) -> list[dict]:
        return await self.dom("query", sel)

    async def text(self, sel: str) -> list[str]:
        return (await self.dom("text", sel))["text"]

    async def count(self, sel: str) -> int:
        return int(await self.dom("count", sel))

    async def html(self, sel: str) -> list[str]:
        return (await self.dom("html", sel))["html"]

    async def attr(self, sel: str, name: str) -> list[Any]:
        return (await self.dom("attr", sel, attr=name))["values"]

    async def set_text(self, sel: str, value: str, index: int = 0) -> None:
        await self.dom("set_text", sel, value=value, index=index)

    async def set_html(self, sel: str, value: str, index: int = 0) -> None:
        await self.dom("set_html", sel, value=value, index=index)

    async def append_html(self, sel: str, value: str, index: int = 0) -> None:
        """Add nodes to the page without replacing its content.

        `set_html` is destructive; a demo that wants a canvas next to the page it is
        analysing needs this instead.
        """
        await self.dom("append_html", sel, value=value, index=index)

    async def set_value(self, sel: str, value: str, index: int = 0) -> None:
        await self.dom("set_value", sel, value=value, index=index)

    async def fill(self, values: dict[str, str]) -> dict[str, bool]:
        """Set several fields at once: {"#user": "me", "#pw": "secret"}.

        Returns {selector: whether it was found}, so a caller can log what a page
        actually offered instead of assuming its shape.
        """
        out: dict[str, bool] = {}
        for sel, value in values.items():
            try:
                await self.set_value(sel, value)
                out[sel] = True
            except PageError:
                out[sel] = False
        return out

    async def set_attr(self, sel: str, name: str, value: Any, index: int = 0) -> None:
        await self.dom("set_attr", sel, attr=name, value=value, index=index)

    async def click(self, sel: str, index: int = 0) -> None:
        await self.dom("click", sel, index=index)

    async def scroll_to(self, sel: str, index: int = 0) -> None:
        await self.dom("scroll_to", sel, index=index)

    def __getitem__(self, selector: str) -> "Element":
        """`page["#price"]` — an element handle from any CSS selector.

        `#id`, `.class`, `[name=user]`, `form > input` all work; there is no
        id-only path. `page.id("price")` is the getElementById spelling.
        """
        return Element(self, selector)

    def id(self, element_id: str, index: int = 0) -> "Element":
        return Element(self, f"#{element_id}", index)

    def sel(self, selector: str, index: int = 0) -> "Element":
        return Element(self, selector, index)

    async def wait_for(self, selector: str, *, timeout: float = 10.0) -> bool:
        """Wait until `selector` exists; False on timeout rather than raising."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.count(selector):
                return True
            await asyncio.sleep(0.1)
        return False

    def subscribe(self, selector: str, events: list[str] | None = None, throttle_ms: int = 0,
                  prevent_default: bool = False) -> None:
        """Stream host-page events to `on_page_event` listeners.

        `prevent_default` cancels the browser's own action (a link's navigation, a form
        submit) before Python hears about it — the only ordering that can work, since the
        browser acts synchronously and Python does not.
        """
        self._send("page.events", {"selector": selector,
                                   "events": events or ["click"],
                                   "throttle_ms": throttle_ms,
                                   "prevent_default": bool(prevent_default)})

    def on_page_event(self, fn: Callable[[dict], None]) -> Callable[[dict], None]:
        """Register a listener for host-page events. Returns fn for chaining."""
        self._event_handlers.append(create_proxy(fn))
        return fn


class PageError(RuntimeError):
    """Raised when a host-page DOM op fails."""


# --------------------------------------------------------------------------- #
# chrome.* (extension page context only)
# --------------------------------------------------------------------------- #


def _chrome_api(name: str) -> Any:
    api = getattr(js.chrome, name, None)
    if api is None:
        raise RuntimeError(f"chrome.{name} is unavailable here (needs an extension page context)")
    return api


async def tabs_query(**query: Any) -> list[dict]:
    """chrome.tabs.query from Python: [{'id', 'url', 'title', 'active'}]."""
    tabs = await _chrome_api("tabs").query(jsobj(query) if query else jsobj({}))
    return [dict(t.to_py()) if hasattr(t, "to_py") else dict(t) for t in tabs]


async def storage_get(key: str, *, area: str = "local") -> Any:
    store = getattr(_chrome_api("storage"), area)
    result = await store.get(key)
    value = getattr(result, key, None) if not hasattr(result, "to_py") else result.to_py().get(key)
    if hasattr(value, "to_py"):
        return value.to_py()
    return value


async def storage_set(key: str, value: Any, *, area: str = "local") -> None:
    store = getattr(_chrome_api("storage"), area)
    await store.set(jsobj({key: value}))


def runtime_url(path: str) -> str:
    """Resolve a packaged resource to its chrome-extension:// URL."""
    return str(_chrome_api("runtime").getURL(path))


async def shared_run(code: str) -> dict:
    """Run Python in the shared engine (one interpreter for every tab).

    The engine outlives tabs: sockets, caches and module state stay warm, and a
    second tab costs no wasm boot. Requires `engine = "shared"` at build time.
    """
    reply = await js.chrome.runtime.sendMessage(jsobj({"kind": "pyodex-shared", "action": "run", "code": code}))
    return reply.to_py() if hasattr(reply, "to_py") else dict(reply)


async def shared_status() -> dict:
    """{'ok', 'bootId', 'runs', 'ready'} for the shared engine."""
    reply = await js.chrome.runtime.sendMessage(jsobj({"kind": "pyodex-engine-status"}))
    return reply.to_py() if hasattr(reply, "to_py") else dict(reply)


def set_badge(text: str, *, color: str = "#f43f5e") -> None:
    """Toolbar badge for the current extension ('' clears it)."""
    action = getattr(js.chrome, "action", None) or getattr(js.chrome, "browserAction", None)
    if action is None:
        raise RuntimeError("no action API in this context")
    action.setBadgeText(jsobj({"text": text}))
    if text:
        action.setBadgeBackgroundColor(jsobj({"color": color}))


# --------------------------------------------------------------------------- #
# chrome.*, as a namespace rather than four loose functions
# --------------------------------------------------------------------------- #


class _ChromeStorage:
    def __init__(self, area: str) -> None:
        self._area = area

    def _api(self) -> Any:
        return getattr(_chrome_api("storage"), self._area)

    async def get(self, key: str, default: Any = None) -> Any:
        result = await self._api().get(key)
        value = result.to_py().get(key, default) if hasattr(result, "to_py") else getattr(result, key, default)
        return value.to_py() if hasattr(value, "to_py") else value

    async def set(self, key: str, value: Any) -> None:
        await self._api().set(jsobj({key: value}))

    async def remove(self, key: str) -> None:
        await self._api().remove(key)

    async def keys(self) -> list[str]:
        result = await self._api().get(None)
        return list(result.to_py().keys()) if hasattr(result, "to_py") else []


class _ChromeTabs:
    async def query(self, **query: Any) -> list[dict]:
        return await tabs_query(**query)

    async def current(self) -> dict:
        tabs = await tabs_query(active=True, currentWindow=True)
        return tabs[0] if tabs else {}

    async def count(self) -> int:
        return len(await tabs_query())

    async def reload(self, tab_id: int) -> None:
        await _chrome_api("tabs").reload(tab_id)

    async def capture_visible_tab(self, *, format: str = "png", quality: int = 90) -> str:
        """Screenshot the visible area as a data URL. Needs "activeTab" or a host permission."""
        options = {"format": format}
        if format == "jpeg":
            options["quality"] = int(quality)
        return str(await _chrome_api("tabs").captureVisibleTab(jsobj(options)))

    async def create(self, url: str, *, active: bool = True) -> int:
        tab = await _chrome_api("tabs").create(jsobj({"url": url, "active": active}))
        return int(tab.id)


class _ChromeAction:
    def badge(self, text: str, *, color: str = "#f43f5e") -> None:
        set_badge(text, color=color)

    def title(self, text: str) -> None:
        api = getattr(js.chrome, "action", None) or getattr(js.chrome, "browserAction", None)
        api.setTitle(jsobj({"title": text}))


_BROWSER_EVENTS: dict[str, list[Callable]] = {}
_EVENT_BRIDGE_READY = False
_LOCAL_EVENTS: set[str] = set()


def _dispatch_browser_event(payload: Any) -> None:
    try:
        data = payload.to_py() if hasattr(payload, "to_py") else payload
        name = data.get("event")
        args = data.get("payload") or []
    except Exception:  # noqa: BLE001
        return
    if str(name) in _LOCAL_EVENTS:
        return  # already delivered by the local listener
    for fn in _BROWSER_EVENTS.get(str(name), []):
        _invoke(fn, args)


def _install_event_bridge() -> None:
    """Listen once for events the worker forwards, and fan them out in Python."""
    # A list sentinel here was falsy (`[]`), so the bridge installed once per
    # subscription and every event was dispatched twice.
    global _EVENT_BRIDGE_READY
    if _EVENT_BRIDGE_READY:
        return
    try:
        api = js.chrome.runtime.onMessage
    except AttributeError:
        return

    def on_message(msg, _sender, _send) -> bool:
        if getattr(msg, "kind", None) == "pyodex-event":
            _dispatch_browser_event(msg)
        return False

    api.addListener(js_callback(on_message))
    _EVENT_BRIDGE_READY = True


def _subscribe_local(event: str) -> bool:
    """Register the listener in *this* realm rather than the worker.

    webRequest events never wake a sleeping MV3 service worker (documented Chrome
    behaviour), so the worker-side registration is dead on arrival for them. An
    extension page that is already running can subscribe directly.
    """
    if not event.startswith("webRequest."):
        return False
    namespace, _, name = event.partition(".")
    try:
        target = getattr(js.chrome.webRequest, event.split(".", 1)[1])
    except AttributeError:
        return False

    def on_event(*args) -> None:
        try:
            payload = json.loads(js.JSON.stringify(list(args)))
        except Exception:  # noqa: BLE001
            payload = []
        for fn in _BROWSER_EVENTS.get(event, []):
            _invoke(fn, payload)

    # webRequest wants (callback, filter, extraInfoSpec): calling it with the callback
    # alone fails in Chrome's bindings as "TypeError: No matching signature".
    # JSON round-trip: a Python list reaching Chrome's bindings is a PyProxy, not an array.
    filter_ = js.JSON.parse(json.dumps({"urls": ["<all_urls>"]}))
    target.addListener(js_callback(on_event), filter_, js.Array.new())
    return True


class _ChromeRuntime:
    def url(self, path: str) -> str:
        return runtime_url(path)

    @property
    def id(self) -> str:
        return str(js.chrome.runtime.id)

    def manifest(self) -> dict:
        m = js.chrome.runtime.getManifest()
        return m.to_py() if hasattr(m, "to_py") else dict(m)


class _ChromeWindows:
    async def all(self) -> list[dict]:
        wins = await _chrome_api("windows").getAll()
        return [w.to_py() if hasattr(w, "to_py") else dict(w) for w in wins]


class _ChromeDnr:
    """declarativeNetRequest, as a Python rule builder.

    Rules are plain dicts, so nothing here needs a JS AST: the platform wants JSON and
    Python produces JSON. `test()` asks the browser what a URL would match, which is
    the only honest way to check a rule without intercepting live traffic.
    """

    async def add_rule(self, rule_id: int, pattern: str, action: str = "block",
                       *, resource_types: list[str] | None = None) -> int:
        rule = {
            "id": int(rule_id),
            "priority": 1,
            "action": {"type": action},
            "condition": {"urlFilter": pattern,
                          "resourceTypes": resource_types or [
                              "main_frame", "sub_frame", "script", "image",
                              "xmlhttprequest", "other"]},
        }
        if action == "redirect":
            raise ValueError("use add_redirect_rule() for redirect actions")
        await _chrome_api("declarativeNetRequest").updateDynamicRules(
            jsobj({"removeRuleIds": [int(rule_id)], "addRules": [rule]}))
        return int(rule_id)

    async def add_redirect_rule(self, rule_id: int, pattern: str, to: str) -> int:
        rule = {
            "id": int(rule_id),
            "priority": 1,
            "action": {"type": "redirect", "redirect": {"url": to}},
            "condition": {"urlFilter": pattern, "resourceTypes": ["main_frame", "sub_frame"]},
        }
        await _chrome_api("declarativeNetRequest").updateDynamicRules(
            jsobj({"removeRuleIds": [int(rule_id)], "addRules": [rule]}))
        return int(rule_id)

    async def rules(self) -> list[dict]:
        found = await _chrome_api("declarativeNetRequest").getDynamicRules()
        return [r.to_py() if hasattr(r, "to_py") else dict(r) for r in found]

    async def clear(self) -> None:
        current = await self.rules()
        ids = [int(r.get("id", 0)) for r in current if r.get("id")]
        if ids:
            await _chrome_api("declarativeNetRequest").updateDynamicRules(jsobj({"removeRuleIds": ids}))

    async def test(self, url: str, *, type: str = "main_frame") -> dict:  # noqa: A002
        # `type` is required by testMatchOutcome; omitting it is a TypeError, not a default.
        # It can also never settle (seen in Chromium 137 with the feedback permission
        # granted), so it is bounded rather than awaited forever.
        outcome = await asyncio.wait_for(
            _chrome_api("declarativeNetRequest").testMatchOutcome(jsobj({"url": url, "type": type})),
            timeout=3.0)
        # the result field is `matchedRules`, and `rules` is silently absent
        return outcome.to_py() if hasattr(outcome, "to_py") else dict(outcome)


class _ChromeCookies:
    """Cookie access for the sending tab's site. Needs the `cookies` permission."""

    async def get(self, url: str, name: str) -> dict | None:
        cookie = await _chrome_api("cookies").get(jsobj({"url": url, "name": name}))
        return None if cookie is None else (cookie.to_py() if hasattr(cookie, "to_py") else dict(cookie))

    async def get_all(self, url: str) -> list[dict]:
        cookies = await _chrome_api("cookies").getAll(jsobj({"url": url}))
        return [c.to_py() if hasattr(c, "to_py") else dict(c) for c in cookies]

    async def set(self, url: str, name: str, value: str, *, path: str = "/") -> dict:
        cookie = await _chrome_api("cookies").set(
            jsobj({"url": url, "name": name, "value": value, "path": path}))
        return cookie.to_py() if hasattr(cookie, "to_py") else dict(cookie)

    async def remove(self, url: str, name: str) -> None:
        await _chrome_api("cookies").remove(jsobj({"url": url, "name": name}))


class _ChromeDownloads:
    async def download(self, url: str, *, filename: str | None = None) -> int:
        options = {"url": url}
        if filename:
            options["filename"] = filename
        return int(await _chrome_api("downloads").download(jsobj(options)))

    async def search(self, query: dict | None = None) -> list[dict]:
        items = await _chrome_api("downloads").search(jsobj(query or {}))
        return [i.to_py() if hasattr(i, "to_py") else dict(i) for i in items]


class _ChromeBookmarks:
    async def search(self, query: str = "") -> list[dict]:
        items = await _chrome_api("bookmarks").search(query)
        return [i.to_py() if hasattr(i, "to_py") else dict(i) for i in items]

    async def tree(self) -> list[dict]:
        items = await _chrome_api("bookmarks").getTree()
        return [i.to_py() if hasattr(i, "to_py") else dict(i) for i in items]


class _ChromeHistory:
    async def search(self, text: str = "", *, limit: int = 20) -> list[dict]:
        items = await _chrome_api("history").search(jsobj({"text": text, "maxResults": limit}))
        return [i.to_py() if hasattr(i, "to_py") else dict(i) for i in items]


class _ChromeIdentity:
    async def get_auth_token(self, *, interactive: bool = False) -> str:
        """Needs `oauth2.client_id` + `scopes` in pyodex.toml; raises a real message if not."""
        try:
            return str(await _chrome_api("identity").getAuthToken(jsobj({"interactive": bool(interactive)})))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "identity.get_auth_token needs OAuth configured: add [oauth2] client_id and "
                f"scopes in pyodex.toml (browser said: {e})") from e


class _ChromeAlarms:
    """Periodic work. The callback runs through the event bridge (`onAlarm`)."""

    async def create(self, name: str, period_minutes: float, *, delay_minutes: float | None = None) -> None:
        spec = {"periodInMinutes": float(period_minutes)}
        if delay_minutes is not None:
            spec["delayInMinutes"] = float(delay_minutes)
        await _chrome_api("alarms").create(name, jsobj(spec))

    async def get_all(self) -> list[dict]:
        alarms = await _chrome_api("alarms").getAll()
        return [a.to_py() if hasattr(a, "to_py") else dict(a) for a in alarms]

    async def clear(self, name: str) -> bool:
        return bool(await _chrome_api("alarms").clear(name))

    async def clear_all(self) -> bool:
        return bool(await _chrome_api("alarms").clearAll())


async def _call_cb(api: Any, method: str, *args: Any) -> Any:
    """Call a callback-style chrome API and await it.

    Not every chrome.* method is promise-based: `contextMenus.create` returns the id
    synchronously and takes a callback, so `await`ing it raises
    "'str' object can't be used in 'await' expression".
    """
    async def run() -> Any:
        return await js.Promise.new(js_callback(lambda resolve, _reject: getattr(api, method)(*args, resolve)))

    return await run()


class _ChromeContextMenus:
    """Right-click entries. Clicks arrive through `onClicked`."""

    async def add(self, menu_id: str, title: str, *, contexts: list[str] | None = None) -> str:
        await _call_cb(_chrome_api("contextMenus"), "create",
                       jsobj({"id": menu_id, "title": title, "contexts": contexts or ["page"]}))
        return menu_id

    async def remove(self, menu_id: str) -> None:
        await _call_cb(_chrome_api("contextMenus"), "remove", menu_id)

    async def remove_all(self) -> None:
        await _call_cb(_chrome_api("contextMenus"), "removeAll")


class _ChromeNotifications:
    async def notify(self, notification_id: str, title: str, message: str, *, icon: str | None = None) -> str:
        options = {"type": "basic", "title": title, "message": message,
                   "iconUrl": icon or "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="}
        await _chrome_api("notifications").create(notification_id, jsobj(options))
        return notification_id

    async def clear(self, notification_id: str) -> bool:
        return bool(await _chrome_api("notifications").clear(notification_id))


class WasmTrace:
    """Everything a module did while Python was hosting it.

        with guest.trace() as t:
            guest.add(2, 3)
            guest.run()
        t.report()      # per-export calls and timings, plus host-import calls
    """

    def __init__(self, instance: "WasmInstance") -> None:
        self._instance = instance
        self.records: list[tuple[str, tuple, Any, float]] = []
        self.host_calls: list[tuple[str, tuple, float]] = []

    def summary(self) -> dict:
        by_name: dict[str, dict] = {}
        for name, _args, _result, us in self.records:
            row = by_name.setdefault(name, {"calls": 0, "us": 0.0})
            row["calls"] += 1
            row["us"] = round(row["us"] + us, 1)
        return {
            "exports": by_name,
            "export_calls": len(self.records),
            "host_calls": len(self.host_calls),
            "host_names": sorted({name for name, _a, _us in self.host_calls}),
            "total_us": round(sum(r[3] for r in self.records) + sum(h[2] for h in self.host_calls), 1),
            # Browser timers are clamped (~100us, Spectre mitigation): call *counts* are
            # exact, aggregate us is meaningful over many calls, a single-call us is not.
            "timer_resolution_us": 100,
        }

    def report(self) -> str:
        s = self.summary()
        lines = [f"exports={s['export_calls']} host={s['host_calls']} total={s['total_us']}us"]
        for name, row in sorted(s["exports"].items(), key=lambda kv: -kv[1]["us"]):
            lines.append(f"  {name:14} {row['calls']:5}x {row['us']:9.1f}us")
        if s["host_names"]:
            lines.append("  host imports: " + ", ".join(s["host_names"]))
        # Timers are clamped (~100us, Spectre): counts exact, single-call us is not.
        lines.append(f"  (us clamped to ~{s['timer_resolution_us']}us buckets; counts are exact)")
        return "\n".join(lines)

    def __enter__(self) -> "WasmTrace":
        self._instance._recorder = self
        _ACTIVE_RECORDER[0] = self
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._instance._recorder = None
        _ACTIVE_RECORDER[0] = None
        return False


class WasmMemory:
    """A wasm linear memory addressed the way Python addresses bytes."""

    def __init__(self, exports: Any) -> None:
        self._exports = exports

    def _u8(self) -> Any:
        return js.Uint8Array.new(self._exports.memory.buffer)

    def read(self, offset: int, size: int) -> bytes:
        return bytes(self._u8().slice(int(offset), int(offset) + int(size)).to_py())

    def write(self, offset: int, data: bytes | bytearray) -> int:
        self._u8().set(to_js(bytearray(data)), int(offset))
        return len(data)

    def cstring(self, offset: int, limit: int = 4096) -> str:
        raw = self.read(offset, limit).split(b"\x00", 1)[0]
        return raw.decode("utf-8", "replace")


class WasmFunction:
    """An exported function you can call, wrap, or watch."""

    def __init__(self, fn: Any, name: str, owner: "WasmInstance") -> None:
        self._fn, self.name, self._owner = fn, name, owner

    def __call__(self, *args: Any) -> Any:
        import time as _time

        started = _time.perf_counter()
        result = self._fn(*args)
        elapsed = (_time.perf_counter() - started) * 1e6
        owner = self._owner
        if owner._recorder is not None:
            owner._recorder.records.append((self.name, args, result, elapsed))
        for hook in list(owner._hooks):
            hook(self.name, args, result)
        return result

    def __repr__(self) -> str:
        return f"<wasm export {self.name}>"


class WasmInstance:
    """A live wasm instance with a Python surface.

        guest = await Wasm.load("guest.wasm", host={"env": {"log": print}})
        guest.add(2, 3)                      # exports are attributes, not strings
        guest.memory.write(0, b"hi")
        guest.instrument(lambda name, args, result: ...)   # watch every call
    """

    def __init__(self, instance: Any) -> None:
        object.__setattr__(self, "_instance", instance)
        object.__setattr__(self, "_hooks", [])
        object.__setattr__(self, "_cache", {})
        object.__setattr__(self, "_recorder", None)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        cache = object.__getattribute__(self, "_cache")
        if name in cache:
            return cache[name]
        try:
            value = getattr(self._instance.exports, name)
        except AttributeError as exc:
            known = ", ".join(self.exports) or "(none)"
            raise AttributeError(f"wasm has no export {name!r}; exports: {known}") from exc
        if callable(value):
            value = WasmFunction(value, name, self)
        cache[name] = value
        return value

    @property
    def memory(self) -> WasmMemory:
        return WasmMemory(self._instance.exports)

    @property
    def exports(self) -> list[str]:
        keys = js.Object.keys(self._instance.exports)
        return sorted(str(k) for k in keys)

    def trace(self) -> "WasmTrace":
        """Record every export call (with timing) and every host-import call."""
        return WasmTrace(self)

    def instrument(self, hook: Callable[[str, tuple, Any], None]) -> Callable[[str, tuple, Any], None]:
        """Call `hook(name, args, result)` after every export call. Returns the hook."""
        self._hooks.append(hook)
        return hook

    def __repr__(self) -> str:
        return f"<wasm instance exports={self.exports}>"


class Wasm:
    """Load a wasm blob and drive it from Python.

    `host` is Python code the module calls into: {"env": {"log": handler}} becomes the
    module's imports, so instrumentation is ordinary Python rather than JS glue.
    """

    @staticmethod
    async def load(source: str | bytes | bytearray, *, host: dict | None = None,
                   imports: dict | None = None) -> WasmInstance:
        data = await Wasm._bytes(source)
        spec: dict[str, Any] = {}
        for module_name, functions in (host or {}).items():
            spec[module_name] = {
                fn_name: js_callback(_host_function(fn, fn_name))
                for fn_name, fn in functions.items()
            }
        for module_name, functions in (imports or {}).items():
            spec.setdefault(module_name, {}).update(functions)
        _WASM_PROXIES.append(spec)  # keep the JS callbacks alive

        import_object = js.Object.new()
        for module_name, functions in spec.items():
            ns = js.Object.new()
            for fn_name, fn in functions.items():
                setattr(ns, fn_name, fn)
            setattr(import_object, module_name, ns)

        result = await js.WebAssembly.instantiate(_wasm_bytes(data), import_object)
        return WasmInstance(result.instance)

    @staticmethod
    async def _bytes(source: str | bytes | bytearray) -> bytes:
        if isinstance(source, (bytes, bytearray)):
            return bytes(source)
        text = str(source)
        if text.startswith(("http://", "https://", "chrome-extension://", "/")):
            response = await js.fetch(text)
            if not response.ok:
                raise RuntimeError(f"wasm fetch failed: {text} ({response.status})")
            return bytes(js.Uint8Array.new(await response.arrayBuffer()).to_py())
        # packaged file, relative to the extension root
        response = await js.fetch(runtime_url(text))
        if not response.ok:
            raise RuntimeError(f"wasm not found in package: {text} ({response.status})")
        return bytes(js.Uint8Array.new(await response.arrayBuffer()).to_py())


_WASM_PROXIES: list[Any] = []
_ACTIVE_RECORDER: list[Any] = [None]  # set by WasmTrace so imports can record too


def _wasm_bytes(data: bytes) -> Any:
    view = js.Uint8Array.new(len(data))
    view.set(to_js(bytearray(data)))
    return view


def _host_function(fn: Callable, name: str) -> Callable:
    def call(*args: Any) -> Any:
        import time as _time

        started = _time.perf_counter()
        result = fn(*args)
        recorder = _ACTIVE_RECORDER[0]
        if recorder is not None:
            recorder.host_calls.append((name, args, (_time.perf_counter() - started) * 1e6))
        return int(result) if isinstance(result, bool) is False and isinstance(result, int) else result

    call.__name__ = f"host_{name}"
    return call


class Chrome:
    """`chrome.*` with Python names: `chrome.storage.local.get("k")`, etc.

    Deliberately a thin namespace over the real API — anything missing is reachable
    through `js.chrome`, and `chrome.raw` is that escape hatch by name.
    """

    local = _ChromeStorage("local")
    sync = _ChromeStorage("sync")
    session = _ChromeStorage("session")
    tabs = _ChromeTabs()
    action = _ChromeAction()
    runtime = _ChromeRuntime()
    windows = _ChromeWindows()
    alarms = _ChromeAlarms()
    contextMenus = _ChromeContextMenus()
    notifications = _ChromeNotifications()
    dnr = _ChromeDnr()
    cookies = _ChromeCookies()
    downloads = _ChromeDownloads()
    bookmarks = _ChromeBookmarks()
    history = _ChromeHistory()
    identity = _ChromeIdentity()

    def on(self, event: str, fn: Callable | None = None) -> Callable:
        """Subscribe to a browser event from Python.

            @chrome.on("storage.onChanged")
            def changed(args): ...

        The event name must also be declared statically (`chrome.on("ns.event", fn)`
        at import time) so the worker can register the listener at top level — MV3
        will not wake a worker for listeners added later.
        """
        if fn is None:  # decorator form: @chrome.on("storage.onChanged")
            def deco(handler: Callable) -> Callable:
                return self.on(event, handler)

            return deco
        _install_event_bridge()
        _BROWSER_EVENTS.setdefault(event, []).append(fn)
        if _subscribe_local(event):
            _LOCAL_EVENTS.add(event)
        return fn

    @property
    def raw(self) -> Any:
        return js.chrome


chrome = Chrome()


# --------------------------------------------------------------------------- #
# Networking
# --------------------------------------------------------------------------- #


def http_ready() -> bool:
    """True if the bundled HTTP shims are present (requests / urllib patched)."""
    try:
        import requests  # noqa: F401

        return True
    except ImportError:
        return False


async def fetch_json(url: str, **kw: Any) -> Any:
    """Async GET returning parsed JSON, via pyodide.http.pyfetch (no wheels needed)."""
    from pyodide.http import pyfetch

    r = await pyfetch(url, **kw)
    return await r.json()


async def fetch_text(url: str, **kw: Any) -> str:
    from pyodide.http import pyfetch

    r = await pyfetch(url, **kw)
    return await r.string()


class WebSocketClient:
    """Thin async wrapper over the browser WebSocket API.

    Usage:
        ws = await WebSocketClient.connect("ws://127.0.0.1:1234/")
        ws.send(b"PING\r\n")
        data = await ws.recv()
    """

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = asyncio.get_event_loop().create_future()
        raw.binaryType = "arraybuffer"
        listen(raw, "message", self._on_message)
        listen(raw, "close", self._on_close)
        listen(raw, "error", self._on_close)

    @classmethod
    async def connect(cls, url: str, protocols: list[str] | None = None, timeout: float = 10.0) -> "WebSocketClient":
        raw = js.WebSocket.new(url, to_js(protocols)) if protocols else js.WebSocket.new(url)
        state = {"open": False, "error": None}

        def _ok(_ev: Any) -> None:
            state["open"] = True

        def _fail(_ev: Any) -> None:
            state["error"] = "websocket failed"

        listen(raw, "open", _ok)
        listen(raw, "error", _fail)
        # Poll rather than race a promise: a promise race can hang forever under
        # JSPI and would ignore the timeout entirely.
        deadline = asyncio.get_event_loop().time() + timeout
        while not state["open"] and state["error"] is None:
            if asyncio.get_event_loop().time() > deadline:
                raise ConnectionError(f"websocket timed out: {url}")
            await asyncio.sleep(0.01)
        if state["error"]:
            raise ConnectionError(f"{state['error']}: {url}")
        return cls(raw)

    @staticmethod
    def _payload_of(data: Any, default: Any = None) -> Any:
        """Decode a message payload, tolerating `undefined`.

        JS `undefined` becomes Python `None`, and `json.loads(None)` raises — which
        surfaced as a PythonError panel over a working page.
        """
        raw = getattr(data, "payload", None)
        if raw is None:
            return default
        text = js.JSON.stringify(raw)
        if text is None:
            return default
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return default

    def _on_message(self, ev: Any) -> None:
        data = ev.data
        if isinstance(data, str):
            self._queue.put_nowait(data)
        else:
            self._queue.put_nowait(bytes(js.Uint8Array.new(data).to_py()))

    def _on_close(self, _ev: Any) -> None:
        if not self._closed.done():
            self._closed.set_result(True)
        self._queue.put_nowait(None)

    def send(self, data: bytes | str) -> None:
        if isinstance(data, str):
            self.raw.send(data)
        else:
            self.raw.send(js.Uint8Array.new(to_js(list(data))))

    async def recv(self) -> bytes | str | None:
        """Next message, or None once the socket closed."""
        return await self._queue.get()

    def close(self) -> None:
        self.raw.close()

    @property
    def closed(self) -> bool:
        return self._closed.done()


class TcpSocket:
    """Raw TCP over the python_websocket_proxy bridge.

    Browsers block raw sockets, so run the companion proxy:

        uv run python_websocket_proxy.py --port 1234 --allow localhost:*

    then from extension Python:

        sock = await TcpSocket.connect("localhost", 6379)
        sock.send(b"PING\r\n")
        print(await sock.recv())
    """

    def __init__(self, ws: WebSocketClient) -> None:
        self.ws = ws
        self._buf = bytearray()

    @classmethod
    async def connect(cls, host: str, port: int, *, proxy: str = "ws://127.0.0.1:1234",
                      token: str | None = None, timeout: float = 10.0) -> "TcpSocket":
        url = f"{proxy.rstrip('/')}/tcp/{host}/{port}"
        if token:
            url += f"?token={token}"
        return cls(await WebSocketClient.connect(url, timeout=timeout))

    @classmethod
    async def connect_default(cls, *, proxy: str = "ws://127.0.0.1:1234", token: str | None = None,
                              timeout: float = 10.0) -> "TcpSocket":
        """Connect to the proxy's fixed --target destination."""
        url = proxy.rstrip("/") + "/"
        if token:
            url += f"?token={token}"
        return cls(await WebSocketClient.connect(url, timeout=timeout))

    def send(self, data: bytes) -> None:
        self.ws.send(data)

    async def recv(self, n: int = 65536) -> bytes:
        if not self._buf:
            chunk = await self.ws.recv()
            if chunk is None:
                return b""
            self._buf.extend(chunk if isinstance(chunk, bytes) else chunk.encode())
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def recv_until(self, sep: bytes = b"\n", limit: int = 1 << 20) -> bytes:
        while sep not in self._buf:
            if len(self._buf) > limit:
                raise ValueError("recv_until limit exceeded")
            chunk = await self.ws.recv()
            if chunk is None:
                break
            self._buf.extend(chunk if isinstance(chunk, bytes) else chunk.encode())
        idx = self._buf.find(sep)
        if idx < 0:
            out, self._buf = bytes(self._buf), bytearray()
            return out
        out = bytes(self._buf[: idx + len(sep)])
        del self._buf[: idx + len(sep)]
        return out

    def close(self) -> None:
        self.ws.close()


# --------------------------------------------------------------------------- #
# WebTransport: QUIC streams + unreliable datagrams (no local proxy)
# --------------------------------------------------------------------------- #


class WebTransportError(RuntimeError):
    """Raised when WebTransport is unavailable or a stream fails."""


class WebTransportStream:
    """A reliable, bidirectional QUIC stream from WebTransport."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self._reader = raw.readable.getReader()
        self._writer = raw.writable.getWriter()
        self._eof = False

    async def write(self, data: bytes) -> None:
        await self._writer.write(to_js(bytes(data)))

    async def read(self) -> bytes:
        """Next chunk; b"" at end of stream."""
        if self._eof:
            return b""
        res = await self._reader.read()
        if res.done:
            self._eof = True
            return b""
        return bytes(js.Uint8Array.new(res.value).to_py())

    async def read_until(self, sep: bytes = b"\n", limit: int = 1 << 20) -> bytes:
        buf = bytearray()
        while sep not in buf:
            if len(buf) > limit:
                raise WebTransportError("read_until limit exceeded")
            chunk = await self.read()
            if not chunk:
                break
            buf.extend(chunk)
        idx = buf.find(sep)
        return bytes(buf[: idx + len(sep)]) if idx >= 0 else bytes(buf)

    async def close(self) -> None:
        try:
            await self._writer.close()
        except Exception:  # noqa: BLE001
            pass

    async def __aenter__(self) -> "WebTransportStream":
        return self

    async def __aexit__(self, *a: Any) -> None:
        await self.close()


class WebTransport:
    """WebTransport client: QUIC streams plus unreliable datagrams.

    Needs an HTTP/3 endpoint with a valid certificate. For local development,
    pass `cert_hashes=[sha256_of_cert]` (Chrome allows short-lived self-signed
    certs for WebTransport via serverCertificateHashes).

        wt = await WebTransport.connect("https://localhost:4433/wt")
        wt.send_datagram(b"tick")
        print(await wt.recv_datagram())
        async with wt.stream() as s:
            s.write(b"hello")
            print(await s.read())
    """

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self._dg_reader = raw.datagrams.readable.getReader() if raw.datagrams else None
        self._dg_writer = raw.datagrams.writable.getWriter() if raw.datagrams else None
        self._closed = False

    @staticmethod
    def available() -> bool:
        return hasattr(js, "WebTransport")

    @classmethod
    async def connect(cls, url: str, *, cert_hashes: list[bytes] | None = None,
                      timeout: float = 15.0) -> "WebTransport":
        if not cls.available():
            raise WebTransportError("WebTransport is not available in this browser")
        opts = None
        if cert_hashes:
            opts = jsobj({"serverCertificateHashes": [
                {"algorithm": "sha-256", "value": to_js(h)} for h in cert_hashes]})
        raw = js.WebTransport.new(url, opts) if opts else js.WebTransport.new(url)
        try:
            await asyncio.wait_for(_awaitable(raw.ready), timeout)
        except asyncio.TimeoutError:
            raise WebTransportError(f"WebTransport handshake timed out: {url}") from None
        return cls(raw)

    # -- datagrams (unreliable, unordered) --------------------------------- #
    def send_datagram(self, data: bytes) -> None:
        if self._dg_writer is None:
            raise WebTransportError("this transport has no datagram support")
        # Fire and forget: write() returns a promise; schedule it on the loop.
        asyncio.ensure_future(self._dg_writer.write(to_js(bytes(data))))

    async def send_datagram_sync(self, data: bytes) -> None:
        """Await the write (backpressure-aware)."""
        if self._dg_writer is None:
            raise WebTransportError("this transport has no datagram support")
        await self._dg_writer.write(to_js(bytes(data)))

    async def recv_datagram(self) -> bytes:
        """Next datagram; b"" once closed."""
        if self._dg_reader is None or self._closed:
            return b""
        res = await self._dg_reader.read()
        if res.done:
            self._closed = True
            return b""
        return bytes(js.Uint8Array.new(res.value).to_py())

    # -- streams (reliable) ------------------------------------------------ #
    async def stream(self) -> WebTransportStream:
        raw = await self.raw.createBidirectionalStream()
        return WebTransportStream(raw)

    def close(self) -> None:
        self._closed = True
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass


async def _awaitable(promise: Any) -> Any:
    """Await a JS promise from Python (Pyodide turns promises into awaitables)."""
    return await promise


def vendored(name: str, *, default_export: bool = True) -> Any:
    """Return a global vendored JS library, unwrapping `.default` for UMD builds if present."""
    lib = getattr(js, name)
    if default_export and hasattr(lib, "default"):
        return lib.default
    return lib
