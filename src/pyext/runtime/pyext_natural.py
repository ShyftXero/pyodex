"""pyext_natural: make Pyodide feel like plain Python inside the extension.

Installed by bootstrap before main.py runs. Everything degrades gracefully:
each shim checks the capability it needs and leaves stdlib untouched if absent.

What it does (when the runtime allows it):

  socket        sync `socket.socket()` / connect / send / recv over the
                python_websocket_proxy bridge, using JSPI `run_sync` so plain
                blocking code works (redis-py, smtplib, http.client, ...).
  ssl           `wrap_socket` routes through the proxy's /tls/HOST/PORT path
                (proxy terminates TLS; browser cannot do raw TLS).
  input()       `window.prompt`;   getpass.getpass()  -> prompt too.
  time.sleep()  yields to the browser event loop under JSPI instead of spinning.
  webbrowser    `open()` -> window.open.
  open()/cwd    cwd is /home/pyodide/data on IndexedDB; persists across reloads.
                Call `pyext_natural.save()` (or use `persist()` ctx) to flush.
  pip           `pip_install("pkg")` via micropip at runtime.
  print()       stdout/stderr also mirrored to the overlay console pill.

Settings via `pyext_natural.config` (set in main.py before use):
    config.proxy = "ws://127.0.0.1:1234"
    config.token = None
"""

from __future__ import annotations

import builtins
import sys
import types
from dataclasses import dataclass
from typing import Any

import js
from pyodide.ffi import create_proxy, to_js

try:
    from pyodide.ffi import can_run_sync, run_sync
except ImportError:  # old pyodide
    can_run_sync = lambda: False  # noqa: E731
    run_sync = None

CAPS: dict[str, Any] = {}  # filled by detect()


@dataclass
class Config:
    proxy: str = "ws://127.0.0.1:1234"
    token: str | None = None
    data_dir: str = "/home/pyodide/data"
    autosave_ms: int = 2000


config = Config()


# --------------------------------------------------------------------------- #
# Capability detection (same code runs in Node for `pyext pyodide probe`)
# --------------------------------------------------------------------------- #


def detect() -> dict[str, Any]:
    import platform

    import pyodide
    import pyodide.ffi as ffi

    caps = {
        "pyodide": getattr(pyodide, "__version__", "?"),
        "python": sys.version.split()[0],
        "emscripten": platform.platform(),
        "jspi": bool(can_run_sync and can_run_sync()),
        "run_sync": run_sync is not None,
        "ffi": sorted(k for k in dir(ffi) if not k.startswith("_")),
        "websocket": hasattr(js, "WebSocket"),
        "idbfs": _has_fs("IDBFS"),
        "opfs": hasattr(js, "navigator") and hasattr(js.navigator, "storage"),
        "prompt": hasattr(js, "prompt"),
        "window_open": hasattr(js, "open"),
        "micropip": _can_import("micropip"),
        "ssl_module": _can_import("ssl"),
        "sqlite3": _can_import("sqlite3"),
        "stdlib_missing": [m for m in ("ssl", "sqlite3", "ctypes", "_multiprocessing", "tkinter", "lzma", "bz2")
                           if not _can_import(m)],
        "socket_native": _socket_native(),
    }
    CAPS.update(caps)
    return caps


def _has_fs(name: str) -> bool:
    try:
        return hasattr(js.pyodide.FS.filesystems, name)
    except Exception:  # noqa: BLE001
        return False


def _can_import(mod: str) -> bool:
    import importlib

    try:
        importlib.import_module(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


def _socket_native() -> bool:
    """True only if the stdlib socket can actually reach the network (Node with sockfs).
    socket() construction succeeds everywhere; connect is the real test."""
    import errno
    import socket

    if getattr(socket.socket, "__module__", "") == __name__:
        return False  # already shimmed
    try:
        s = socket.socket()
        s.settimeout(0.2)
        rc = s.connect_ex(("127.0.0.1", 1))
        s.close()
        return rc in (0, errno.ECONNREFUSED, errno.EINPROGRESS, errno.EAGAIN)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# socket over proxy (sync via JSPI)
# --------------------------------------------------------------------------- #


def _await(promise: Any) -> Any:
    """Block on a JS promise. Needs JSPI; raises clear error otherwise."""
    if not (run_sync and can_run_sync()):
        raise RuntimeError("blocking socket needs JSPI (run_sync). Use pyext_runtime.TcpSocket (async) instead.")
    return run_sync(promise)


class _ProxySocket:
    """Minimal socket.socket replacement backed by a WebSocket to the proxy."""

    family = 2  # AF_INET
    type = 1  # SOCK_STREAM
    proto = 0

    def __init__(self, family: int = 2, type: int = 1, proto: int = 0, fileno: Any = None) -> None:
        self._ws: Any = None
        self._buf = bytearray()
        self._chunks: list[bytes | None] = []
        self._waiters: list[Any] = []
        self._timeout: float | None = None
        self._closed = False
        self._tls = False
        self._peer: tuple[str, int] | None = None
        self._proxies: list[Any] = []

    # -- connection -------------------------------------------------------- #
    def _url(self, host: str, port: int) -> str:
        kind = "tls" if self._tls else "tcp"
        u = f"{config.proxy.rstrip('/')}/{kind}/{host}/{port}"
        if config.token:
            u += f"?token={config.token}"
        return u

    def connect(self, address: tuple[str, int]) -> None:
        host, port = address
        self._peer = (host, int(port))
        ws = js.WebSocket.new(self._url(host, int(port)))
        ws.binaryType = "arraybuffer"
        opened = js.Promise.new(create_proxy(lambda res, rej: self._wire(ws, res, rej)))
        self._ws = ws
        _await(_with_timeout(opened, self._timeout))

    def connect_ex(self, address: tuple[str, int]) -> int:
        try:
            self.connect(address)
            return 0
        except OSError as e:
            return e.errno or 111

    def _wire(self, ws: Any, res: Any, rej: Any) -> None:
        def on_open(_e: Any) -> None:
            res(True)

        def on_msg(e: Any) -> None:
            d = e.data
            b = d.encode() if isinstance(d, str) else bytes(js.Uint8Array.new(d).to_py())
            self._chunks.append(b)
            self._wake()

        def on_close(e: Any) -> None:
            self._chunks.append(None)
            self._wake()
            reason = getattr(e, "reason", "") or "closed"
            rej(js.Error.new(f"connect failed: {reason}"))

        for ev, fn in (("open", on_open), ("message", on_msg), ("close", on_close), ("error", on_close)):
            p = create_proxy(fn)
            self._proxies.append(p)
            ws.addEventListener(ev, p)

    def _wake(self) -> None:
        for w in self._waiters:
            w(True)
        self._waiters.clear()

    def _wait_data(self) -> None:
        if self._chunks:
            return
        p = js.Promise.new(create_proxy(lambda res, rej: self._waiters.append(res)))
        _await(_with_timeout(p, self._timeout))

    # -- io ---------------------------------------------------------------- #
    def send(self, data: bytes, flags: int = 0) -> int:
        if self._closed or self._ws is None:
            raise OSError(9, "Bad file descriptor")
        self._ws.send(js.Uint8Array.new(to_js(list(bytes(data)))))
        return len(data)

    def sendall(self, data: bytes, flags: int = 0) -> None:
        self.send(data)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        while not self._buf:
            if self._chunks:
                c = self._chunks.pop(0)
                if c is None:
                    return b""
                self._buf.extend(c)
            else:
                self._wait_data()
        out = bytes(self._buf[:bufsize])
        del self._buf[:bufsize]
        return out

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        data = self.recv(nbytes or len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def makefile(self, mode: str = "r", buffering: int = -1, **kw: Any) -> Any:
        import io
        import socket as _s

        raw = _s.SocketIO(self, mode.replace("b", ""))  # type: ignore[arg-type]
        if "b" in mode:
            return io.BufferedReader(raw) if "r" in mode else io.BufferedWriter(raw)
        return io.TextIOWrapper(io.BufferedRWPair(raw, raw), **kw)

    # -- misc -------------------------------------------------------------- #
    def settimeout(self, t: float | None) -> None:
        self._timeout = t

    def gettimeout(self) -> float | None:
        return self._timeout

    def setblocking(self, flag: bool) -> None:
        self._timeout = None if flag else 0.0

    def setsockopt(self, *a: Any) -> None:
        pass

    def getsockopt(self, *a: Any) -> int:
        return 0

    def getpeername(self) -> tuple[str, int]:
        return self._peer or ("", 0)

    def getsockname(self) -> tuple[str, int]:
        return ("0.0.0.0", 0)

    def fileno(self) -> int:
        return -1

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        if not self._closed and self._ws is not None:
            self._closed = True
            self._ws.close()

    def detach(self) -> int:
        return -1

    def __enter__(self) -> "_ProxySocket":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # ssl hook
    def _upgrade_tls(self, server_hostname: str | None = None) -> "_ProxySocket":
        """Called before connect() by our ssl shim. Proxy terminates TLS."""
        self._tls = True
        return self


def _with_timeout(promise: Any, t: float | None) -> Any:
    if not t:
        return promise
    ms = int(t * 1000)
    return js.Promise.race(to_js([promise, js.Promise.new(create_proxy(
        lambda res, rej: js.setTimeout(create_proxy(lambda: rej(js.Error.new("timed out"))), ms)))]))


def _install_socket() -> None:
    import socket

    if CAPS.get("socket_native"):
        return
    socket.socket = _ProxySocket  # type: ignore[misc]
    socket.SocketType = _ProxySocket  # type: ignore[misc]

    def create_connection(address: tuple[str, int], timeout: Any = None, source_address: Any = None, **kw: Any) -> _ProxySocket:
        s = _ProxySocket()
        if timeout is not None and timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
            s.settimeout(timeout)
        s.connect(address)
        return s

    socket.create_connection = create_connection  # type: ignore[assignment]

    def getaddrinfo(host: Any, port: Any, *a: Any, **kw: Any) -> list:
        return [(2, 1, 6, "", (str(host), int(port or 0)))]

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = lambda h: h  # type: ignore[assignment]


def _install_ssl() -> None:
    """Route TLS through the proxy. Real `ssl` present: patch wrap_socket for proxy
    sockets. Absent: provide a minimal fake good enough for http.client / smtplib."""
    if CAPS.get("socket_native"):
        return
    if CAPS.get("ssl_module"):
        import ssl as _ssl

        _orig_wrap = _ssl.SSLContext.wrap_socket

        def wrap_socket(self: Any, sock: Any, *a: Any, server_hostname: str | None = None, **kw: Any) -> Any:
            if isinstance(sock, _ProxySocket):
                return sock._upgrade_tls(server_hostname)
            return _orig_wrap(self, sock, *a, server_hostname=server_hostname, **kw)

        _ssl.SSLContext.wrap_socket = wrap_socket  # type: ignore[method-assign]
        return
    m = types.ModuleType("ssl")
    m.PROTOCOL_TLS_CLIENT = m.PROTOCOL_TLS = 2  # type: ignore[attr-defined]
    m.CERT_NONE, m.CERT_REQUIRED = 0, 2  # type: ignore[attr-defined]
    m.HAS_SNI = True  # type: ignore[attr-defined]

    class SSLError(OSError):
        pass

    class SSLContext:
        def __init__(self, protocol: int = 2) -> None:
            self.check_hostname = False
            self.verify_mode = 0
            self.minimum_version = None

        def load_default_certs(self, *a: Any) -> None:
            pass

        def load_verify_locations(self, *a: Any, **k: Any) -> None:
            pass

        def set_ciphers(self, *a: Any) -> None:
            pass

        def set_alpn_protocols(self, *a: Any) -> None:
            pass

        def wrap_socket(self, sock: Any, server_hostname: str | None = None, **kw: Any) -> Any:
            if isinstance(sock, _ProxySocket):
                return sock._upgrade_tls(server_hostname)
            raise SSLError("ssl only supported on proxy sockets in the browser")

    m.SSLError = SSLError  # type: ignore[attr-defined]
    m.SSLContext = SSLContext  # type: ignore[attr-defined]
    m.create_default_context = lambda *a, **k: SSLContext()  # type: ignore[attr-defined]
    m._create_unverified_context = m.create_default_context  # type: ignore[attr-defined]
    m.wrap_socket = lambda sock, **k: SSLContext().wrap_socket(sock, **k)  # type: ignore[attr-defined]
    sys.modules["ssl"] = m


# --------------------------------------------------------------------------- #
# input / getpass / sleep / webbrowser
# --------------------------------------------------------------------------- #


def _install_io() -> None:
    if CAPS.get("prompt"):
        def _input(prompt: str = "") -> str:
            r = js.prompt(str(prompt))
            if r is None:
                raise EOFError
            return str(r)

        builtins.input = _input  # type: ignore[assignment]
        try:
            import getpass

            getpass.getpass = lambda prompt="Password: ", stream=None: _input(prompt)  # type: ignore[assignment]
        except ImportError:
            pass

    if CAPS.get("window_open"):
        import webbrowser

        webbrowser.open = lambda url, new=0, autoraise=True: bool(js.open(url, "_blank"))  # type: ignore[assignment]
        webbrowser.open_new = webbrowser.open_new_tab = webbrowser.open  # type: ignore[assignment]

    if CAPS.get("jspi"):
        import time

        _orig = time.sleep

        def _sleep(secs: float) -> None:
            if secs <= 0:
                return
            if can_run_sync():
                run_sync(js.Promise.new(create_proxy(lambda res, rej: js.setTimeout(create_proxy(lambda: res(None)), int(secs * 1000)))))
            else:
                _orig(secs)

        time.sleep = _sleep  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# persistent filesystem
# --------------------------------------------------------------------------- #

_fs_ready = False


def _mount_persist() -> None:
    """Mount IDBFS at config.data_dir, sync from IndexedDB, chdir there. Sync, needs JSPI; else async fallback later."""
    global _fs_ready
    if not CAPS.get("idbfs"):
        return
    import os

    FS = js.pyodide.FS
    os.makedirs(config.data_dir, exist_ok=True)
    try:
        FS.mount(FS.filesystems.IDBFS, js.Object.new(), config.data_dir)
    except Exception:  # noqa: BLE001  already mounted
        pass
    p = js.Promise.new(create_proxy(lambda res, rej: FS.syncfs(True, create_proxy(lambda err: rej(err) if err else res(None)))))
    if can_run_sync():
        run_sync(p)
        _fs_ready = True
    os.chdir(config.data_dir)
    if config.autosave_ms:
        js.setInterval(create_proxy(lambda: save()), config.autosave_ms)


def save() -> None:
    """Flush the persistent data dir to IndexedDB."""
    if not CAPS.get("idbfs"):
        return
    js.pyodide.FS.syncfs(False, create_proxy(lambda err: None))


class persist:
    """`with persist(): open('x','w').write(...)` flushes on exit."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *a: Any) -> None:
        save()


# --------------------------------------------------------------------------- #
# pip at runtime
# --------------------------------------------------------------------------- #


def pip_install(*packages: str) -> None:
    """Install packages from PyPI at runtime via micropip (pure wheels / Pyodide builds only)."""
    import micropip

    p = micropip.install(list(packages))
    if can_run_sync():
        run_sync(p)
    else:
        import asyncio

        asyncio.ensure_future(p)


async def pip_install_async(*packages: str) -> None:
    import micropip

    await micropip.install(list(packages))


# --------------------------------------------------------------------------- #
# console mirror
# --------------------------------------------------------------------------- #


def _install_console() -> None:
    class _Tee:
        def __init__(self, orig: Any, level: str) -> None:
            self.orig, self.level = orig, level

        def write(self, s: str) -> int:
            if s.strip():
                getattr(js.console, self.level)("[py] " + s.rstrip("\n"))
            return self.orig.write(s)

        def flush(self) -> None:
            self.orig.flush()

        def __getattr__(self, k: str) -> Any:
            return getattr(self.orig, k)

    sys.stdout = _Tee(sys.stdout, "log")  # type: ignore[assignment]
    sys.stderr = _Tee(sys.stderr, "error")  # type: ignore[assignment]


# --------------------------------------------------------------------------- #


def install(*, console: bool = True, persist_fs: bool = True) -> dict[str, Any]:
    """Apply all shims the runtime supports. Returns detected capabilities."""
    caps = detect()
    _install_socket()
    _install_ssl()
    _install_io()
    if console:
        _install_console()
    if persist_fs:
        try:
            _mount_persist()
        except Exception as e:  # noqa: BLE001
            js.console.warn(f"[pyext] persistent fs unavailable: {e}")
    return caps
