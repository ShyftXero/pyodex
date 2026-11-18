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

document = js.document
window = js.window

_proxies: list[Any] = []  # keep event proxies alive


def jsobj(d: dict) -> Any:
    """Convert a Python dict to a plain JS object (recursively)."""
    return to_js(d, dict_converter=js.Object.fromEntries)


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
    """Show/hide the built-in bottom-right status pill. `None` hides it."""
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


def want_boot(mode: str = "auto") -> None:
    """Ask the host page to boot (or stay) in `mode`.

    Only ever downgrades in practice: the content script refuses anything above
    the configured ceiling, so an app cannot escape a "user" build.
    """
    if mode not in ("user", "visible", "auto"):
        raise ValueError(f"mode must be user|visible|auto, got {mode!r}")
    _post("page.boot", mode)


def release() -> None:
    """Free this tab's interpreter now (e.g. the work is done). Reload state is lost."""
    _post("page.release")


class Overlay:
    """Convenience wrapper around the overlay document body.

    The overlay is click-through by default. Elements created via
    `button`/`panel` get `pointer-events: auto` so they are interactive.
    """

    def __init__(self) -> None:
        self.body = document.body

    def mount(self, element: Any) -> Any:
        self.body.appendChild(element)
        return element

    def button(self, label: str, *, top: int | None = None, left: int | None = None,
               right: int | None = None, bottom: int | None = None, id: str | None = None) -> Any:
        b = el("button", id=id, text=label, position="fixed", z_index="10", pointer_events="auto",
               padding="10px 16px", font="600 14px system-ui, sans-serif", color="#fff",
               background="#18181b", border="2px solid #f43f5e", border_radius="8px",
               box_shadow="0 4px 12px rgba(0,0,0,.25)", cursor="pointer")
        for k, v in (("top", top), ("left", left), ("right", right), ("bottom", bottom)):
            if v is not None:
                setattr(b.style, k, f"{v}px")
        return self.mount(b)

    def panel(self, *, id: str | None = None, **style: str) -> Any:
        base = dict(position="fixed", pointer_events="auto", background="rgba(24,24,27,.95)",
                    color="#fff", padding="12px", border_radius="8px", font="14px system-ui, sans-serif")
        base.update(style)
        return self.mount(el("div", id=id, **base))

    def layer(self, *, id: str | None = None) -> Any:
        """Full-screen non-interactive container (e.g. for a canvas)."""
        return self.mount(el("div", id=id, position="fixed", inset="0", pointer_events="none"))


class Page:
    """Talk to the host page via the content-script bridge (postMessage)."""

    def __init__(self) -> None:
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._event_handlers: list[Any] = []
        self._last_error: str | None = None
        listen(window, "message", self._on_message)

    def _on_message(self, ev: Any) -> None:
        data = ev.data
        try:
            if not data or not data.pyodex:
                return
        except AttributeError:
            return
        if getattr(data, "type", None) == "page.event":
            payload = json.loads(js.JSON.stringify(data.payload))
            for h in list(self._event_handlers):
                h(payload)
            return
        err = getattr(data, "error", None)
        if err:
            self._last_error = str(err)
        fut = self._pending.pop(int(data.id), None) if hasattr(data, "id") else None
        if fut and not fut.done():
            fut.set_result(json.loads(js.JSON.stringify(data.payload)))

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

    async def set_value(self, sel: str, value: str, index: int = 0) -> None:
        await self.dom("set_value", sel, value=value, index=index)

    async def click(self, sel: str, index: int = 0) -> None:
        await self.dom("click", sel, index=index)

    async def scroll_to(self, sel: str, index: int = 0) -> None:
        await self.dom("scroll_to", sel, index=index)

    def subscribe(self, selector: str, events: list[str] | None = None, throttle_ms: int = 0) -> None:
        """Stream host-page events to `on_page_event` listeners."""
        self._send("page.events", {"selector": selector,
                                   "events": events or ["click"],
                                   "throttle_ms": throttle_ms})

    def on_page_event(self, fn: Callable[[dict], None]) -> Callable[[dict], None]:
        """Register a listener for host-page events. Returns fn for chaining."""
        self._event_handlers.append(create_proxy(fn))
        return fn


class PageError(RuntimeError):
    """Raised when a host-page DOM op fails."""


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
        loop = asyncio.get_event_loop()
        opened = loop.create_future()

        def _ok(_ev: Any) -> None:
            if not opened.done():
                opened.set_result(True)

        def _fail(_ev: Any) -> None:
            if not opened.done():
                opened.set_exception(ConnectionError(f"websocket failed: {url}"))

        listen(raw, "open", _ok)
        listen(raw, "error", _fail)
        await asyncio.wait_for(opened, timeout)
        return cls(raw)

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
