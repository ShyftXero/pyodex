"""App authoring API. The builder installs this module inside page-based Pyodide.

The surface is deliberately small and names its surfaces:

    ext_page   the page pyodex ships (main.html)      sync, yours
    page       the page this app is injected into     async, theirs
    chrome     browser and extension APIs
    ctx        handles owned by this app

Element access is strict by default (`get` raises, `maybe` returns None,
`get_all` returns a possibly-empty list) because a silent empty result is how
selector typos hide.
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import json
import traceback
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, Self, TypeVar

import pyodex_runtime as _runtime

P = ParamSpec("P")
T = TypeVar("T")
_startup: Callable[[], Any] | None = None
_start_interactive = True


class _Document:
    def element(self, id: str) -> Any:
        """Get a required element by ID, without a CSS selector or a null check."""
        node = _runtime.document.getElementById(id)
        if not node:
            raise LookupError(f"No element with id {id!r}")
        return node

    def __getattr__(self, name: str) -> Any:
        return getattr(_runtime.document, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(_runtime.document, name, value)


document = _Document()


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(_runtime, name)


def start(fn: Callable[[], Any] | None = None, *, interactive: bool = True) -> Callable[..., Any]:
    """Mark one function as the app entry point. Pyodex calls it after loading the file.

    `interactive=True` (the default) asks the content script to give this overlay
    the pointer. Anything with a form or a button needs that; an effects-only
    overlay (`interactive=False`) must let the page underneath keep working.
    """
    def register(handler: Callable[[], Any]) -> Callable[[], Any]:
        global _startup, _start_interactive
        if _startup is not None:
            raise RuntimeError("An app entry file can have only one @start function")
        _startup = handler
        _start_interactive = interactive
        return handler

    return register(fn) if callable(fn) else register


async def _run_start() -> None:
    global _startup
    fn, _startup = _startup, None
    if _start_interactive:
        # A page with markup is interactive whether or not it declares @start.
        try:  # no parent frame in an IWA; the message has no listener there
            _runtime._post("page.pointer", True)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            # The overlay body is click-through so the page keeps working. Mark
            # the app's own top-level blocks as the part that is not.
            _runtime.document.body.dataset.pyodexInteractive = "1"
        except Exception:  # noqa: BLE001, S110 - no document (engine mode)
            pass
    if fn is not None:
        result = fn()
        if inspect.isawaitable(result):
            await result
    if _PAGE_BINDINGS:
        try:
            await _install_page_bindings()
        except Exception:  # noqa: BLE001 - a broken binding must not stop the app
            _runtime._report_exception(traceback.format_exc())


def on(target: Any, event: str, *, prevent_default: bool = False) -> Callable[[Callable], Callable]:
    """Bind a Python handler, sync or async. Report errors raised after it yields."""
    def decorate(fn: Callable) -> Callable:
        async def invoke(event_arg: Any) -> None:
            try:
                result = fn(event_arg)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _runtime._report_exception(traceback.format_exc())

        # Pyodide enters async proxies in a suspendable stack. The browser does
        # not await listeners, so default cancellation must precede that handoff.
        callback = _runtime.js_callback(invoke)
        if prevent_default:
            @_runtime.on(target, event)
            def receive(event_arg: Any) -> None:
                event_arg.preventDefault()
                callback(event_arg)
        else:
            target.addEventListener(event, callback)
        return fn

    return decorate


# --------------------------------------------------------------------------- #
# Errors that crossed the Python/JS boundary
# --------------------------------------------------------------------------- #


class JsError(RuntimeError):
    """A JavaScript error, kept legible in Python.

    `str()` names the JS class (`SyntaxError: ...`) because "invalid selector"
    without the class is the kind of message that sends people to Stack Overflow.
    The original stack is preserved for the panel.
    """

    def __init__(self, name: str, message: str, stack: str = "") -> None:
        self.js_name = name or "Error"
        self.js_message = message
        self.js_stack = stack
        text = message if self.js_name in ("", "Error") else f"{self.js_name}: {message}"
        super().__init__(text)


def translate_error(exc: BaseException) -> BaseException:
    """Turn a Pyodide JsException into a JsError; leave everything else alone."""
    if isinstance(exc, JsError):
        return exc
    try:
        from pyodide.ffi import JsException
    except ImportError:  # not running under Pyodide
        return exc
    if not isinstance(exc, JsException):
        return exc
    js_error = getattr(exc, "js_error", None)
    name = str(getattr(js_error, "name", "") or "")
    message = str(getattr(js_error, "message", "") or exc)
    stack = str(getattr(js_error, "stack", "") or "")
    return JsError(name, message, stack)


def _raise_translated(exc: BaseException) -> None:
    translated = translate_error(exc)
    if translated is exc:
        raise exc
    raise translated from exc


# --------------------------------------------------------------------------- #
# ext_page: the extension's own DOM
# --------------------------------------------------------------------------- #


def _declared_ids() -> list[str]:
    """Every id in the extension page, for error messages only."""
    doc = _runtime.document
    if doc is None:
        return []
    try:
        nodes = doc.querySelectorAll("[id]")
        return sorted({str(nodes.item(i).id) for i in range(int(getattr(nodes, "length", 0)))})
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return []


def _query_one(selector: str) -> Any:
    doc = _runtime.document
    if doc is None:
        return None
    text = selector.strip()
    try:
        if text.startswith("#") and text[1:].isidentifier():
            node = doc.getElementById(text[1:])
        else:
            node = doc.querySelector(text)
    except Exception as exc:  # noqa: BLE001 - an invalid selector is a JS SyntaxError
        _raise_translated(exc)
    # A missing element crosses back as null, which is falsy but not always None.
    return node or None


def _id_for(name: str) -> str:
    """`pyodex_ssh_prompt` -> `pyodex-ssh-prompt`, keeping exact ids intact."""
    known = set(_declared_ids())
    for candidate in (name, name.replace("_", "-"), name.rstrip("_").replace("_", "-")):
        if candidate in known:
            return candidate
    return name.rstrip("_").replace("_", "-")


def _missing(selector: str) -> str:
    ids = _declared_ids()
    wanted = selector.lstrip("#")
    close = difflib.get_close_matches(wanted, ids, n=1)
    lines = [f"no element {selector!r} in the extension page."]
    if close:
        lines.append(f"  Did you mean #{close[0]}?")
    lines.append(f"  Declared ids: {', '.join(ids) or 'none'}")
    lines.append("  (looking for a host-page element? use `page.get()`)")
    return "\n".join(lines)


class Node:
    """One element of the extension page.

    Attributes pass through to the DOM node (`node.value`, `node.disabled`), so
    nothing has to be wrapped to be useful. The additions are the ones every app
    writes by hand: `.text`, `.as_int()`, and `.on()`.
    """

    __slots__ = ("_node", "selector")

    def __init__(self, node: Any, selector: str) -> None:
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "selector", selector)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return getattr(self._node, name)
        except Exception as exc:  # noqa: BLE001 - a null node surfaces here
            _raise_translated(exc)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in Node.__slots__ or hasattr(type(self), name):
            object.__setattr__(self, name, value)
        else:
            setattr(self._node, name, value)

    @property
    def text(self) -> str:
        return str(self._node.textContent)

    @text.setter
    def text(self, value: Any) -> None:
        self._node.textContent = str(value)

    def as_int(self) -> int:
        raw = str(self.value).strip()
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.selector} holds {raw!r}, which is not an integer") from None

    def as_float(self) -> float:
        raw = str(self.value).strip()
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.selector} holds {raw!r}, which is not a number") from None

    def on(self, event: str, fn: Callable | None = None, *, prevent_default: bool = False) -> Any:
        """Bind a handler to this element. Works as `node.on("click", fn)` or a decorator."""
        def register(handler: Callable) -> Callable:
            def call(event_arg: Any) -> None:
                if prevent_default:
                    event_arg.preventDefault()
                _call(handler, event_arg)
            self._node.addEventListener(event, _runtime.js_callback(call))
            return handler

        return register if fn is None else register(fn)

    def unwrap(self) -> Any:
        return self._node

    def __repr__(self) -> str:
        return f"<Node {self.selector!r}>"


class _ExtensionPage:
    """The page pyodex ships: `main.html`, same document as this Python."""

    def get(self, selector: str) -> Node:
        """One required element. Raises, naming the DOM it searched."""
        node = _query_one(selector)
        if not node:
            raise LookupError(_missing(selector))
        return Node(node, selector)

    def get_all(self, selector: str) -> list[Node]:
        """Every match; an empty list is a legitimate answer."""
        try:
            nodes = _runtime.document.querySelectorAll(selector)
        except Exception as exc:  # noqa: BLE001
            _raise_translated(exc)
        return [Node(nodes.item(i), selector) for i in range(int(getattr(nodes, "length", 0)))]

    def maybe(self, selector: str) -> Node | None:
        """One optional element. The name carries the cardinality and the leniency."""
        node = _query_one(selector)
        return Node(node, selector) if node else None

    def __getattr__(self, name: str) -> Node:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get("#" + _id_for(name.rstrip("_")))

    def __repr__(self) -> str:
        return f"<ext_page {len(_declared_ids())} id(s)>"


ext_page = _ExtensionPage()


# --------------------------------------------------------------------------- #
# page: the host page (async, crossed via the content-script bridge)
# --------------------------------------------------------------------------- #


def _require_host_page() -> None:
    chrome = getattr(_runtime.js, "chrome", None)
    if chrome is None or getattr(chrome, "runtime", None) is None:
        raise RuntimeError(
            "no host page — this app runs as an Isolated Web App.\n"
            "  `ext_page` is the app itself; `page` only exists in an extension tab."
        )


class _HostPage:
    """The page this app is injected into.

    `page.get()` returns a lazy handle, so it stays synchronous while its
    operations are awaited — the same shape as a Playwright locator.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_client_cache", None)

    def _client(self) -> Any:
        cached = object.__getattribute__(self, "_client_cache")
        if cached is not None:
            return cached
        _require_host_page()
        cached = _runtime.Page()
        object.__setattr__(self, "_client_cache", cached)
        return cached

    def get(self, selector: str) -> Any:
        return _runtime.Element(self._client(), selector)

    def __getitem__(self, selector: str) -> Any:
        """`page["input[name=user]"]` — the element-handle form this surface started with."""
        return self.get(selector)

    async def get_all(self, selector: str) -> list[Any]:
        total = int(await self._client().count(selector))
        return [_runtime.Element(self._client(), selector, i) for i in range(total)]

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._client(), name)

    def __repr__(self) -> str:
        return "<page (host)>"


page = _HostPage()


# --------------------------------------------------------------------------- #
# ctx: handles owned by this app instance
# --------------------------------------------------------------------------- #


def _release(handle: Any) -> None:
    """Teardown by convention: the first of close/remove/detach/disconnect wins."""
    for method in ("close", "remove", "detach", "disconnect"):
        action = getattr(handle, method, None)
        if callable(action):
            try:
                action()
            except Exception:  # noqa: BLE001 - teardown must not raise
                _runtime._report_exception(traceback.format_exc())
            return


class Mount:
    """A teardown handle: what an attach returns, and what ctx releases.

    `attach_to(element)` on a plugin decides where its content lives; returning a
    Mount (or any object with close/remove/detach/disconnect) is what makes the
    ownership explicit instead of a `finally:` block someone forgot.
    """

    __slots__ = ("_close", "element")

    def __init__(self, element: Any, close: Callable[[], None]) -> None:
        self.element = element
        self._close = close

    def close(self) -> None:
        action, self._close = self._close, None
        if action is not None:
            action()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        state = "open" if self._close is not None else "closed"
        return f"<Mount {self.element!r} {state}>"


class _Context:
    """Attribute assignment is ownership: whatever you put here is released later."""

    def __init__(self) -> None:
        object.__setattr__(self, "_owned", {})

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._owned[name] = value
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        self._owned.pop(name, None)
        object.__delattr__(self, name)

    def own(self, handle: T) -> T:
        """Register a handle without naming it."""
        self._owned[f"_{len(self._owned)}"] = handle
        return handle

    def close(self) -> None:
        """Release every owned handle, newest first. Each is tried once."""
        for handle in reversed(list(self._owned.values())):
            _release(handle)
        self._owned.clear()
        for name in [n for n in vars(self) if not n.startswith("_")]:
            vars(self).pop(name, None)

    def __repr__(self) -> str:
        return f"<ctx {len(self._owned)} handle(s)>"


ctx = _Context()


try:  # release owned handles when the page goes away
    _runtime.listen(_runtime.window, "pagehide", lambda _event: ctx.close())
except Exception:  # noqa: BLE001, S110 - no window (engine mode, tests)
    pass


# --------------------------------------------------------------------------- #
# on_page: bind to the page you do not own
# --------------------------------------------------------------------------- #

_PAGE_BINDINGS: dict[int, dict] = {}


def on_page(event: str, *, selector: str | None = None, testid: str | None = None,
            role: str | None = None, name: str | None = None, text: str | None = None,
            any_of: list[dict] | None = None, prevent_default: bool = False) -> Callable:
    """React to an event on the host page.

        @on_page("click", role="button", name="Buy now")   # survives a redesign
        @on_page("click", selector="#buy")                  # allowed, fragile
        @on_page("click", any_of=[{"selector": "#buy"}, {"role": "button", "name": "Buy now"}])

    Matching happens at dispatch time on the page side, so a re-render or a
    virtualized list does not break the binding. `prevent_default` cancels the
    browser's own action before Python hears about it.
    """
    match: dict[str, Any] = {}
    for key, value in (("selector", selector), ("testid", testid), ("role", role),
                       ("name", name), ("text", text), ("any_of", any_of)):
        if value:
            match[key] = value
    if not match:
        raise ValueError(
            "@on_page needs selector=, testid=, role=/name=, text= or any_of="
        )

    def decorate(fn: Callable) -> Callable:
        binding = len(_PAGE_BINDINGS)
        _PAGE_BINDINGS[binding] = {
            "match": match,
            "event": str(event).split(".")[-1],
            "prevent_default": bool(prevent_default),
            "callable": fn,
        }
        return fn

    return decorate


def _route_page_event(payload: Any) -> None:
    binding = payload.get("binding") if isinstance(payload, dict) else None
    entry = _PAGE_BINDINGS.get(binding)
    if entry is None:
        return
    _call(entry["callable"], payload)


async def _install_page_bindings() -> None:
    if not _PAGE_BINDINGS:
        return
    client = page._client()  # raises the IWA message when there is no host page
    for binding, entry in _PAGE_BINDINGS.items():
        client.subscribe_match(binding, entry["match"], [entry["event"]],
                               prevent_default=entry["prevent_default"])
    client.on_page_event(_route_page_event)


# --------------------------------------------------------------------------- #
# attach_when: wait for an element to appear, and let go when it leaves
# --------------------------------------------------------------------------- #

_ATTACHERS: dict[str, dict] = {}
_ATTACH_WATCHERS: list[Any] = []


def _require_document(what: str) -> Any:
    doc = _runtime.document
    if doc is None:
        raise RuntimeError(
            f"{what} needs the extension page; engine mode paints through a per-tab agent"
        )
    return doc


def _attached(entry: dict, element: Any) -> bool:
    return any(existing is element for existing, _handle in entry["live"])


def _maybe_attach(selector: str, element: Any) -> None:
    entry = _ATTACHERS.get(selector)
    if entry is None or _attached(entry, element):
        return
    if not entry["each"] and entry["live"]:
        return
    try:
        handle = entry["fn"](Node(element, selector))
    except Exception:  # noqa: BLE001 - one bad attach must not stop the watcher
        _runtime._report_exception(traceback.format_exc())
        return
    entry["live"].append((element, handle))


def _scan_added(selector: str, nodes: Any) -> None:
    for index in range(int(getattr(nodes, "length", 0))):
        node = nodes[index]
        if getattr(node, "nodeType", 0) != 1:
            continue
        try:
            if node.matches(selector):
                _maybe_attach(selector, node)
            for found in node.querySelectorAll(selector):
                _maybe_attach(selector, found)
        except Exception:  # noqa: BLE001, S112 - a detached subtree can throw; keep watching
            continue


def _scan_removed(selector: str, nodes: Any) -> None:
    entry = _ATTACHERS.get(selector)
    if entry is None:
        return
    for index in range(int(getattr(nodes, "length", 0))):
        node = nodes[index]
        still_live = []
        for element, handle in entry["live"]:
            try:
                gone = node is element or bool(node.contains(element))
            except Exception:  # noqa: BLE001
                gone = node is element
            if gone:
                _release(handle)
            else:
                still_live.append((element, handle))
        entry["live"] = still_live


def _watch(selector: str) -> None:
    doc = _require_document("@attach_when")
    try:
        doc.querySelector(selector)
    except Exception as exc:  # noqa: BLE001 - a bad selector must fail at import
        raise ValueError(f"@attach_when({selector!r}): {exc}") from None

    def flush(records: Any) -> None:
        for index in range(int(getattr(records, "length", 0))):
            record = records[index]
            _scan_added(selector, record.addedNodes)
            _scan_removed(selector, record.removedNodes)

    proxy = _runtime.js_callback(flush)
    # Never called while Python is running: the JS trampoline defers to a timer.
    deferred = _runtime.js.pyodexDefer(proxy)
    observer = _runtime.js.MutationObserver.new(deferred)
    observer.observe(doc.body, _runtime.jsobj({"childList": True, "subtree": True}))
    _ATTACH_WATCHERS.append((proxy, observer))


def attach_when(selector: str, *, each: bool = True) -> Callable[[Callable], Callable]:
    """Run a function when an element matching `selector` appears.

        @attach_when("#terminal")
        def mount_terminal(el):
            return Terminal().attach_to(el)   # released when the element goes

    The return value is the teardown token. `each=False` attaches the first match
    only. Elements already present are attached immediately, so a static page
    needs no special case.
    """
    def decorate(fn: Callable) -> Callable:
        entry = _ATTACHERS.get(selector)
        if entry is None:
            entry = {"fn": fn, "each": each, "live": []}
            _ATTACHERS[selector] = entry
            _watch(selector)
            for element in _runtime.document.querySelectorAll(selector):
                _maybe_attach(selector, element)
        return fn

    return decorate


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

_HANDLERS: dict[str, Callable] = {}


def _takes_event(fn: Callable) -> bool:
    """Event is passed only if the signature accepts one — no string sniffing."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return any(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        for p in parameters.values()
    )


async def _finish(result: Any) -> None:
    try:
        await result
    except Exception:  # noqa: BLE001
        _runtime._report_exception(traceback.format_exc())


def _call(fn: Callable, event_arg: Any = None) -> None:
    """Run a handler now; errors go to the panel, never to the event loop."""
    try:
        result = fn(event_arg) if _takes_event(fn) else fn()
    except Exception as exc:  # noqa: BLE001
        translated = translate_error(exc)
        if translated is exc:
            _runtime._report_exception(traceback.format_exc())
        else:
            _runtime._report_exception(f"{translated}\n{translated.js_stack}".strip())
        return
    if inspect.isawaitable(result):
        _runtime._task_done(asyncio.ensure_future(_finish(result)))


def handler(name: str | Callable | None = None) -> Any:
    """Name a function for markup bindings.

        @handler                      # `pyodex-click="connect"`
        def connect(): ...

        @handler("save_target")       # when markup names it differently
        def save(): ...
    """
    if callable(name):
        _HANDLERS[name.__name__] = name
        return name

    def decorate(fn: Callable) -> Callable:
        _HANDLERS[name or fn.__name__] = fn
        return fn

    return decorate


def bind(selector: str, event: str, fn: Callable | None = None, *,
         prevent_default: bool = False) -> Any:
    """Attach a handler to an element now — the low-level form of @handler."""
    def register(target: Callable) -> Callable:
        ext_page.get(selector).on(event, target, prevent_default=prevent_default)
        return target

    return register if fn is None else register(fn)


def _dispatch(name: str, event_arg: Any = None) -> None:
    """Called by the boot binder for `pyodex-*` attributes."""
    fn = _HANDLERS.get(name)
    if fn is None:
        declared = ", ".join(sorted(_HANDLERS)) or "none"
        _runtime._report_exception(
            f"pyodex: no handler named {name!r} (declared: {declared})"
        )
        return
    _call(fn, event_arg)


def _handler_names() -> str:
    return json.dumps(sorted(_HANDLERS))


def blocking(fn: Callable[P, Awaitable[T]]) -> Callable[P, T]:
    """Expose an async implementation as a sync API in a JSPI-enabled entry point or handler."""
    @wraps(fn)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        message = f"{fn.__name__} needs a JSPI-enabled Python context (such as @start, @handler or @on)"
        try:
            from pyodide.ffi import can_run_sync, run_sync
        except ImportError as exc:
            raise RuntimeError(message) from exc
        if not can_run_sync():
            raise RuntimeError(message)
        return run_sync(fn(*args, **kwargs))

    return call
