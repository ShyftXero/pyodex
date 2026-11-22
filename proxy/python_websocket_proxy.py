# /// script
# requires-python = ">=3.12"
# dependencies = ["websockets>=13"]
# ///
"""python_websocket_proxy: WebSocket <-> raw TCP bridge for Pyodide extensions.

Browsers cannot open raw TCP sockets. This proxy accepts WebSocket connections
from the extension and pipes bytes to a real TCP service, so Python code inside
the extension can talk to databases, SSH, Redis, SMTP, etc. via
`pyodex_runtime.TcpSocket`.

Usage:
    uv run python_websocket_proxy.py --port 1234 --target localhost:6379
    uv run python_websocket_proxy.py --port 1234 --allow localhost:* --allow 10.0.0.0/8:5432

Routing:
    ws://127.0.0.1:1234/                -> --target (fixed destination)
    ws://127.0.0.1:1234/tcp/HOST/PORT   -> HOST:PORT, only if matched by an --allow rule
    ws://127.0.0.1:1234/tls/HOST/PORT   -> same, proxy opens TLS to HOST (SNI=HOST, verified)

Security: by default binds 127.0.0.1 and only allows --target. Dynamic targets
require explicit --allow rules. Use --token to require `?token=...` on connect.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import ipaddress
import logging
import os
import signal
import ssl
import sys
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger("wsproxy")

BUF = 64 * 1024


class AllowList:
    """Rules of the form HOST:PORT where HOST may be hostname, glob, or CIDR and PORT may be '*' or a number or a-b range."""

    def __init__(self, rules: list[str]) -> None:
        self.rules = [self._parse(r) for r in rules]

    @staticmethod
    def _parse(rule: str):
        host, _, port = rule.rpartition(":")
        if not host:
            raise ValueError(f"bad allow rule {rule!r}, want HOST:PORT")
        net = None
        if "/" in host:
            net = ipaddress.ip_network(host, strict=False)
        if port == "*":
            ports = None
        elif "-" in port:
            a, b = port.split("-", 1)
            ports = range(int(a), int(b) + 1)
        else:
            ports = range(int(port), int(port) + 1)
        return host, net, ports

    def allows(self, host: str, port: int) -> bool:
        for pat, net, ports in self.rules:
            if ports is not None and port not in ports:
                continue
            if net is not None:
                try:
                    if ipaddress.ip_address(host) in net:
                        return True
                except ValueError:
                    pass
                continue
            if fnmatch.fnmatch(host.lower(), pat.lower()):
                return True
        return False


class Proxy:
    def __init__(self, target: tuple[str, int] | None, allow: AllowList, token: str | None, connect_timeout: float) -> None:
        self.target = target
        self.allow = allow
        self.token = token
        self.connect_timeout = connect_timeout
        self.active = 0

    def resolve(self, path: str) -> tuple[str, int, bool]:
        parts = urlsplit(path)
        if self.token:
            q = parse_qs(parts.query)
            if q.get("token", [None])[0] != self.token:
                raise PermissionError("bad token")
        segs = [s for s in parts.path.split("/") if s]
        if not segs:
            if not self.target:
                raise LookupError("no --target configured; use /tcp/HOST/PORT")
            return (*self.target, False)
        if len(segs) == 3 and segs[0] in ("tcp", "tls"):
            host, port, tls = segs[1], int(segs[2]), segs[0] == "tls"
            if self.target and (host, port) == self.target:
                return host, port, tls
            if not self.allow.allows(host, port):
                raise PermissionError(f"{host}:{port} not in --allow list")
            return host, port, tls
        raise LookupError(f"unknown path {parts.path}")

    async def handle(self, ws: ServerConnection) -> None:
        peer = ws.remote_address
        path = ws.request.path if ws.request else "/"
        try:
            host, port, tls = self.resolve(path)
        except (PermissionError, LookupError, ValueError) as e:
            log.warning("%s rejected %s: %s", peer, path, e)
            await ws.close(code=1008, reason=str(e)[:120])
            return

        try:
            ssl_ctx = ssl.create_default_context() if tls else None
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ssl_ctx, server_hostname=host if tls else None),
                self.connect_timeout)
        except Exception as e:  # noqa: BLE001
            log.warning("%s -> %s:%d connect failed: %s", peer, host, port, e)
            await ws.close(code=1011, reason=f"connect failed: {e}"[:120])
            return

        self.active += 1
        log.info("%s -> %s:%d%s open (active=%d)", peer, host, port, " tls" if tls else "", self.active)

        async def ws_to_tcp() -> None:
            try:
                async for msg in ws:
                    writer.write(msg if isinstance(msg, bytes) else msg.encode())
                    await writer.drain()
            finally:
                if not writer.is_closing():
                    writer.close()

        async def tcp_to_ws() -> None:
            try:
                while data := await reader.read(BUF):
                    await ws.send(data)
            finally:
                await ws.close()

        try:
            await asyncio.gather(ws_to_tcp(), tcp_to_ws(), return_exceptions=True)
        finally:
            self.active -= 1
            writer.close()
            log.info("%s -> %s:%d closed (active=%d)", peer, host, port, self.active)


def parse_hostport(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(f"want HOST:PORT, got {s!r}")
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=1234, help="WebSocket listen port (default 1234)")
    p.add_argument("--host", default="127.0.0.1", help="WebSocket bind address (default 127.0.0.1)")
    p.add_argument("--target", type=parse_hostport, help="fixed TCP destination HOST:PORT for ws://.../")
    p.add_argument("--allow", action="append", default=[], metavar="HOST:PORT",
                   help="allow dynamic /tcp/HOST/PORT routing. HOST = name, glob, or CIDR; PORT = n, a-b, or *. Repeatable.")
    p.add_argument("--token", help="require ?token=... on connect")
    p.add_argument("--connect-timeout", type=float, default=10.0)
    p.add_argument("--max-size", type=int, default=16 * 1024 * 1024, help="max WebSocket frame size")
    p.add_argument("--http-offset", type=int, default=1,
                   help="HTTP bridge runs on port+offset (0 = same port; default 1)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


# --------------------------------------------------------------------------- #
# HTTP bridge: for runtimes that have no WebSocket client (the MicroPython tier).
# Same routing and allow rules as the WebSocket side; polling instead of duplex,
# which is a latency trade, not a capability one.
# --------------------------------------------------------------------------- #

SESSIONS: dict[str, tuple[object, object]] = {}


def _http_response(writer, status: int, body: bytes, ctype: str = "application/json") -> None:
    head = (
        f"HTTP/1.1 {status} OK\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Headers: content-type\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    writer.write(head + body)


async def _http_client(reader, writer, proxy: "Proxy") -> None:
    import base64
    import json as _json

    try:
        line = await asyncio.wait_for(reader.readline(), 5)
        if not line:
            return
        method, target, _ = line.decode(errors="replace").split(" ", 2)
        headers: dict[str, str] = {}
        while True:
            h = await asyncio.wait_for(reader.readline(), 5)
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode(errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        body = await reader.readexactly(length) if length else b""

        if method == "OPTIONS":
            _http_response(writer, 204, b"")
            return

        parts = urlsplit(target)
        segs = [x for x in parts.path.split("/") if x]
        query = parse_qs(parts.query)
        token = (query.get("token") or [None])[0]

        if proxy.token and token != proxy.token:
            _http_response(writer, 403, b'{"error":"bad token"}')
            return

        # /tcp/HOST/PORT/open | /send?id= | /recv?id=&timeout= | /close?id=
        if len(segs) >= 4 and segs[0] == "tcp":
            host, port, op = segs[1], int(segs[2]), segs[3]
            if not proxy.allow.allows(host, port):
                _http_response(writer, 403, b'{"error":"host not allowed"}')
                return

            if op == "open":
                r, w = await asyncio.wait_for(asyncio.open_connection(host, port), proxy.connect_timeout)
                sid = base64.urlsafe_b64encode(os.urandom(9)).decode()
                SESSIONS[sid] = (r, w)
                _http_response(writer, 200, _json.dumps({"id": sid}).encode())
                return

            sid = (query.get("id") or [""])[0]
            sess = SESSIONS.get(sid)
            if sess is None:
                _http_response(writer, 404, b'{"error":"no session"}')
                return
            r, w = sess

            if op == "send":
                w.write(body)
                await w.drain()
                _http_response(writer, 200, b'{"sent":%d}' % len(body))
                return

            if op == "recv":
                timeout = float((query.get("timeout") or ["5"])[0])
                try:
                    data = await asyncio.wait_for(r.read(65536), timeout)
                except asyncio.TimeoutError:
                    data = b""
                except Exception:  # noqa: BLE001
                    data = b""
                payload = _json.dumps({"data": base64.b64encode(data).decode(), "eof": data == b""}).encode()
                _http_response(writer, 200, payload)
                return

            if op == "close":
                try:
                    w.close()
                finally:
                    SESSIONS.pop(sid, None)
                _http_response(writer, 200, b'{"closed":true}')
                return

        _http_response(writer, 404, b'{"error":"unknown path"}')
    except Exception as e:  # noqa: BLE001
        log.warning("http bridge error: %s", e)
        try:
            _http_response(writer, 500, b'{"error":"internal"}')
        except Exception:  # noqa: BLE001
            pass
    finally:
        writer.close()


async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if not args.target and not args.allow:
        log.error("need --target and/or --allow")
        return 2
    proxy = Proxy(args.target, AllowList(args.allow), args.token, args.connect_timeout)
    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, lambda: not stop.done() and stop.set_result(None))
        except NotImplementedError:  # Windows
            pass
    http_srv = await asyncio.start_server(
        lambda r, w: _http_client(r, w, proxy), args.host, args.port + (args.http_offset or 0))

    async with serve(proxy.handle, args.host, args.port, max_size=args.max_size, ping_interval=20, ping_timeout=20):
        log.info("http bridge on http://%s:%d (POST /tcp/HOST/PORT/open)", args.host, args.port + (args.http_offset or 0))
        log.info("listening ws://%s:%d  target=%s allow=%s token=%s", args.host, args.port,
                 f"{args.target[0]}:{args.target[1]}" if args.target else "-", args.allow or "-", "yes" if args.token else "no")
        await stop
    log.info("bye")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
