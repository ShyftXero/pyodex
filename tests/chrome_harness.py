"""Chrome harness: prove pyodex works in a real browser, graded by complexity.

Levels (each a gate; a later level failing is a bug, an earlier one failing is
a regression):

    L1      trivial      content script, launcher, Pyodide boot, overlay DOM
    L2      quickstart   dependencies, compute, HTTP, async
    L3      tutorial     declarations, host-page DOM, persistence across reload
    L4.1    web          URL gate, trigger boot, two tabs, keep_alive, traceback
    L4.2    net          requests, raw TCP, proxy TLS, WebSocket
    L4.3    runtime      micropip at runtime, persistent dir, input(), caps

Monotonicity: a passing higher level implies every lower level passes; a
violation fails the suite, because it means the results are not trustworthy.

    uv run tests/chrome_harness.py                  # everything, auto parallel
    uv run tests/chrome_harness.py L1 L3            # subset
    uv run tests/chrome_harness.py --jobs 8         # override parallelism
    uv run tests/chrome_harness.py --headed         # visible windows (debugging)

Parallelism is chosen from the machine: half the CPUs, capped by available
memory (each browser + Pyodide is budgeted ~900 MB), clamped to 2..20.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "levels"
sys.path.insert(0, str(ROOT / "src"))

BOOT_TIMEOUT = 90_000  # ms


LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Login</title></head>
<body>
  <h1>Sign in</h1>
  <form method="post" action="/submit">
    <label>User <input name="user" id="user" type="text"></label>
    <label>Pass <input name="password" id="pw" type="password"></label>
    <button type="submit" id="go">Go</button>
  </form>
  <div id="l3-report">not filled</div>
  <script>document.querySelector('form').addEventListener('submit', e => e.preventDefault());</script>
</body></html>"""

PAGE_CACHE_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Pyodide page</title></head>
<body><h1>app that uses pyodide</h1><div id="net">pending</div><div id="mp">pending</div>
<script>
  // Mimics what the Pyodide loader does: fetch a blob from the CDN path.
  (async () => {
    const url = "/pyodide/v314.0.8/full/demo-1.0.0-py3-none-any.whl";
    try {
      const r = await fetch(url);
      const buf = await r.arrayBuffer();
      const src = r.headers.get("x-pyodex-source") || "cdn";
      document.getElementById("net").textContent = `source=${src} len=${buf.byteLength}`;
      const mp = await fetch("/mp/micropython.wasm");
      const mbuf = await mp.arrayBuffer();
      document.getElementById("mp").textContent =
        `source=${mp.headers.get("x-pyodex-source") || "cdn"} len=${mbuf.byteLength}`;
    } catch (e) {
      document.getElementById("net").textContent = "error=" + e.message;
    }
  })();
</script>
</body></html>"""

SHOP_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Shop</title></head>
<body><h1 id="item" data-item="Wireless mouse">Wireless mouse</h1>
<p>Price: <span id="p" data-price="PRICE">PRICE</span></p></body></html>"""

PHISH_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Links</title></head>
<body>
  <a href="https://example.com/a">ok</a>
  <a href="https://paypa1.com/login">lookalike</a>
  <a href="https://gogle.com/">near miss</a>
  <a href="https://paypal.com.evil.example/">subdomain trick</a>
</body></html>"""

PLAIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Plain</title></head>
<body><h1>Nothing to see</h1><p id="plain">plain page</p></body></html>"""


# --------------------------------------------------------------------------- #
# servers
# --------------------------------------------------------------------------- #


class Blobs:
    """Serves immutable-ish blobs and counts requests, for the cache level."""

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}
        self._lock = threading.Lock()

    def payload(self, key: str) -> bytes:
        with self._lock:
            self.hits[key] = self.hits.get(key, 0) + 1
            n = self.hits[key]
        return f"blob:{key}:v1".encode() + b"x" * 2048

    def digest(self, key: str) -> str:
        import hashlib

        return hashlib.sha256(f"blob:{key}:v1".encode() + b"x" * 2048).hexdigest()

    def count(self, key: str) -> int:
        with self._lock:
            return self.hits.get(key, 0)


BLOBS = Blobs()
PYODIDE_DIR = Path.home() / ".cache" / "pyodex" / "pyodide-314.0.8"
MP_BLOB = b"mp-wasm-bytes" + b"y" * 1024
MP_SHA = __import__("hashlib").sha256(MP_BLOB).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/phish"):
            body = PHISH_PAGE.encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/shop/"):
            price = parse_qs(urlsplit(self.path).query).get("price", ["0"])[0]
            body = SHOP_PAGE.replace("PRICE", price).encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/blob/"):
            body = BLOBS.payload(self.path.rsplit("/", 1)[-1].split("?")[0])
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/mp/"):
            BLOBS.payload("mp")
            body = MP_BLOB
            self.send_response(200)
            self.send_header("content-type", "application/wasm")
            self.send_header("access-control-allow-origin", "*")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/cdn/full/"):
            name = self.path.rsplit("/", 1)[-1].split("?")[0]
            src = PYODIDE_DIR / name
            if not src.exists():
                self.send_error(404, f"no {name}")
                return
            BLOBS.payload(name)  # count the request
            body = src.read_bytes()
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("access-control-allow-origin", "*")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/pyodide/"):
            if self.path.endswith("pyodide-lock.json"):
                body = json.dumps({
                    "info": {"version": "314.0.8"},
                    "packages": {
                        "demo": {"file_name": "demo-1.0.0-py3-none-any.whl",
                                 "sha256": BLOBS.digest("wheel")},
                    },
                }).encode()
            else:
                body = BLOBS.payload("wheel")
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("cache-control", "no-store")
            self.send_header("access-control-allow-origin", "*")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/json"):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/pyodide-page"):
            page = PAGE_CACHE_PAGE
        else:
            page = LOGIN_PAGE if self.path.startswith(("/login", "/boom")) else PLAIN_PAGE
        body = page.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


class _EchoHandler(__import__("socketserver", fromlist=["BaseRequestHandler"]).BaseRequestHandler):
    """Line echo: replies `echo:<line>` for every line received."""

    def handle(self) -> None:
        with self.request:
            f = self.request.makefile("rb")
            for line in f:
                self.request.sendall(b"echo:" + line)


def echo_server(tls: ssl.SSLContext | None = None):
    import socketserver

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    srv.daemon_threads = True
    if tls is not None:
        if hasattr(srv, "socket"):
            srv.socket = tls.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class WsEcho:
    """Line-echo WebSocket server for the L4.2 transport check."""

    def __init__(self) -> None:
        self.port = 0
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import asyncio

        import websockets

        async def handler(ws) -> None:
            async for msg in ws:
                await ws.send(msg)

        async def serve() -> None:
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self.ready.set()
                await asyncio.Future()

        asyncio.run(serve())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_cert(dirpath: Path) -> tuple[Path, Path]:
    cert, key = dirpath / "tls.pem", dirpath / "tls.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "2", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
         "-addext", "basicConstraints=critical,CA:TRUE"],
        check=True, capture_output=True,
    )
    return cert, key


# --------------------------------------------------------------------------- #
# levels
# --------------------------------------------------------------------------- #


@dataclass
class Level:
    name: str
    slug: str
    fixture: str
    description: str
    rank: int = 0  # ordering for the monotonicity check
    needs: tuple[str, ...] = field(default_factory=tuple)
    experimental: bool = False  # not part of the gate yet
    demo: str = ""  # build from examples/demos/<demo> instead of tests/fixtures


LEVELS = [
    Level("L1", "l1-trivial", "L1_trivial", "plumbing: launcher, boot, overlay DOM", 1),
    Level("L2", "l2-quickstart", "L2_quickstart", "deps, compute, HTTP, async", 2),
    Level("L3", "l3-tutorial", "L3_tutorial", "declarations, host DOM, persistence", 3),
    Level("L4.1", "l4-1-web", "L4_1_web", "gate, 2 tabs, keep_alive, traceback", 4, ("primary",)),
    Level("L4.2", "l4-2-net", "L4_2_net", "requests, TCP, proxy TLS, WebSocket", 4, ("primary", "echo", "tls", "ws")),
    Level("L4.3", "l4-3-runtime", "L4_3_runtime", "runtime pip, persistence, input(), caps", 4, ("primary",)),
    Level("L5", "l5-cache", "L5_cache", "blob cache: digests, no stale serve, opt-out", 5, ("primary",)),
    Level("L6", "l6-pagecache", "L6_pagecache", "shared CDN cache for other pages", 6, ("primary",)),
    Level("L7", "l7-remote", "L7_remote", "remote runtime streamed through the cache", 7, ("primary",)),
    Level("L8", "l8-shared", "L8_shared", "one interpreter shared by every tab", 8, ("primary",)),
    Level("L9", "l9-engine-ui", "L9_engine_ui", "one worker drives two tabs; no per-tab interpreter", 9,
          ("primary",)),
    Level("L10", "l10-compat-chrome", "L10_compat_chrome", "compat probes in Chrome's V8, not just Node", 10,
          ("primary",)),
    Level("L11", "l11-micropython", "L11_micropython", "MicroPython tier boots, paints, crypto+http", 11,
          ("primary", "echo")),
    Level("L12", "l12-rustpython", "L12_rustpython", "RustPython tier in a sandbox page", 12, ("primary",)),
    Level("L13", "l13-rustpython-nosandbox", "L13_rustpython_nosandbox",
          "RustPython in a normal page (eval-stubbed glue)", 13, ("primary",), experimental=True),
    Level("L14", "demo-01-minimal-ui", "", "demo 01: minimal UI renders and reacts", 14, ("primary",),
          demo="01-minimal-ui"),
    Level("L15", "demo-02-price-compare", "", "demo 02: collects prices, shows the cheapest", 15, ("primary",),
          demo="02-price-compare"),
    Level("L16", "demo-03-anti-phishing", "", "demo 03: flags lookalike domains", 16, ("primary",),
          demo="03-anti-phishing"),
    Level("L17", "demo-04-password-manager", "", "demo 04: saves and fills credentials", 17, ("primary",),
          demo="04-password-manager"),
    Level("L25", "demo-12-wasm", "", "demo 12: a wasm module driven from Python", 25, ("primary",),
          demo="12-wasm"),
    Level("L24", "demo-11-popup", "", "demo 11: the toolbar popup runs popup.py", 24, ("primary",),
          demo="11-popup"),
    Level("L23", "demo-10-network", "", "demo 10: declarativeNetRequest + webRequest", 23, ("primary",),
          demo="10-network"),
    Level("L22", "demo-09-toolbox", "", "demo 09: cookies, capture, downloads, bookmarks", 22, ("primary",),
          demo="09-toolbox"),
    Level("L21", "demo-08-setters", "", "demo 08: alarms, context menus, notifications", 21, ("primary",),
          demo="08-setters"),
    Level("L20", "demo-07-event-bridge", "", "demo 07: browser events reach Python", 20, ("primary",),
          demo="07-event-bridge"),
    Level("L19", "demo-06-api-tour", "", "demo 06: chrome.* and DOM breadth", 19, ("primary",),
          demo="06-api-tour"),
    Level("L18", "demo-05-tab-watcher", "", "demo 05: always-on watcher, shared interpreter", 18, ("primary",),
          demo="05-tab-watcher"),
]


class Checks:
    def __init__(self, level: str) -> None:
        self.level = level
        self.failures: list[str] = []
        self.passes: list[str] = []

    def expect(self, name: str, cond: bool, detail: str = "") -> bool:
        if cond:
            self.passes.append(name)
            print(f"    ok   {name}", flush=True)
        else:
            self.failures.append(f"{name}: {detail or 'assertion failed'}")
            print(f"    FAIL {name}: {detail or 'assertion failed'}", flush=True)
        return bool(cond)


# --------------------------------------------------------------------------- #
# browser helpers
# --------------------------------------------------------------------------- #


def click_launcher(page, slug: str) -> bool:
    return bool(page.evaluate(
        """(id) => {
            const host = document.getElementById(id);
            const btn = host && host.shadowRoot && host.shadowRoot.querySelector('button');
            if (!btn) return false;
            btn.click();
            return true;
        }""",
        f"pyodex-{slug}-launcher",
    ))


def overlay_frame(page, timeout: int = BOOT_TIMEOUT):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for frame in page.frames:
            if "overlay.html" in (frame.url or ""):
                return frame
        page.wait_for_timeout(200)
    return None


def frame_text(frame) -> str:
    """Text of the overlay, including hidden nodes.

    `innerText` honours CSS visibility, so a status pill that bootstrap hid after
    main.py returned reads as empty even though Python wrote to it. `textContent`
    is what tests want here.
    """
    try:
        return frame.evaluate("() => document.body?.textContent || ''")
    except Exception:  # noqa: BLE001
        return ""


def wait_for(predicate, timeout_s: float = 30, interval: float = 0.2):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            value = predicate()
        except Exception:  # noqa: BLE001  (frame/navigation churn)
            value = None
        if value:
            return value
        time.sleep(interval)
    return None


# --------------------------------------------------------------------------- #
# per-level assertions
# --------------------------------------------------------------------------- #


def status_now(page) -> str:
    """Current overlay status text (never the matched value: diagnostics on failure)."""
    frame = overlay_frame(page, 5000)
    return frame_text(frame) if frame else ""


def wait_status(page, needle: str, timeout: float = 90):
    """Wait for the overlay's status text to contain `needle`.

    Re-resolves the frame on every probe: after a navigation the previous frame
    object is detached and evaluates to an empty string forever.
    """

    def probe():
        frame = overlay_frame(page, 5000)
        text = frame_text(frame) if frame else ""
        return text if needle in text else None

    return wait_for(probe, timeout)


def check_l1(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#l1-btn")
    txt = wait_for(lambda: "L1 ready" in frame_text(frame) and frame_text(frame))
    c.expect("python ran and painted", bool(txt), f"text={frame_text(frame)!r}")
    caps = frame.evaluate("() => window.pyodexCaps || null")
    c.expect("capabilities detected", bool(caps and caps.get("pyodide")), f"caps={caps!r}")
    c.expect("jspi available", bool(caps and caps.get("jspi")), f"caps={caps!r}")


def check_l2(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    click_launcher(page, level.slug)
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#l2-btn")
    txt = wait_for(lambda: "numpy=" in frame_text(frame) and frame_text(frame), 120)
    c.expect("numpy loaded", bool(txt), f"text={frame_text(frame)!r}")
    c.expect("compute produced a mean", "mean=" in (txt or ""), f"text={txt!r}")
    c.expect("cross-origin HTTP worked", "http=ok" in (txt or ""), f"text={txt!r}")


def check_l3(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    frame = overlay_frame(page)  # @watch_for: no click needed
    c.expect("auto-boot from @watch_for", frame is not None, "no overlay without a click")
    c.expect("filled user field", bool(wait_for(lambda: page.input_value("#user") == "l3@pytest", 60)),
             f"user={page.input_value('#user')!r}")
    c.expect("filled password field", page.input_value("#pw") == "l3-secret",
             f"pw={page.input_value('#pw')!r}")
    c.expect("reported fill count",
             bool(wait_for(lambda: "filled x" in page.inner_text("#l3-report"), 30)),
             f"report={page.inner_text('#l3-report')!r}")

    page.reload()
    c.expect("state survived reload (IDBFS)",
             bool(wait_for(lambda: "filled x2" in page.inner_text("#l3-report"), 90)),
             f"report={page.inner_text('#l3-report')!r}")

    plain = ctx.new_page()
    plain.goto(f"{env['base']}/plain.html")
    plain.wait_for_timeout(3000)
    c.expect("no boot on unmatched page",
             not any("overlay.html" in (f.url or "") for f in plain.frames),
             "overlay appeared on the plain page")
    plain.close()


def check_l41(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    frame = overlay_frame(page)
    c.expect("auto-boot on urlmatch+watch_for", frame is not None, "no overlay frame")
    c.expect("filled tab 1", bool(wait_for(lambda: page.input_value("#user") == "l41@pytest", 60)),
             f"user={page.input_value('#user')!r}")
    c.expect("status reflects readiness",
             bool(wait_for(lambda: "L4.1 ready" in frame_text(frame), 30)),
             f"status={frame_text(frame)!r}")

    second = ctx.new_page()
    second.goto(f"{env['base']}/login.html")
    c.expect("second tab filled independently",
             bool(wait_for(lambda: second.input_value("#user") == "l41@pytest", 90)),
             f"user={second.input_value('#user')!r}")
    second.close()

    boom = ctx.new_page()
    boom.goto(f"{env['base']}/boom.html")
    boom_frame = overlay_frame(boom)
    panel = None
    if boom_frame is not None:
        panel = wait_for(lambda: boom_frame.evaluate(
            "() => document.querySelector('#pyodex-error.show')?.innerText || null"), 60)
    c.expect("traceback surfaced", bool(panel and "deliberate" in panel), f"panel={(panel or '')[:160]!r}")
    boom.close()


def check_l42(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    click_launcher(page, level.slug)
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#l42-btn")
    txt = wait_for(lambda: "ws=" in frame_text(frame) and frame_text(frame), 150)
    c.expect("all transports reported", bool(txt), f"text={frame_text(frame)!r}")
    for key in ("http=ok", "tcp=echo:PING", "tls=echo:TLS", "ws=PING"):
        c.expect(f"{key.split('=')[0]} works", key in (txt or ""), f"text={txt!r}")


def check_l43(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept("harness-input")))
    page.goto(f"{env['base']}/plain.html")
    click_launcher(page, level.slug)
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#l43-btn")
    txt = wait_for(lambda: "caps=" in frame_text(frame) and frame_text(frame), 120)
    c.expect("runtime pip install worked", "pip=ok:" in (txt or ""), f"text={txt!r}")
    c.expect("persistent dir writable", "fs=x1" in (txt or ""), f"text={txt!r}")
    c.expect("input() surfaced a prompt", bool(dialogs), f"dialogs={dialogs!r}")
    c.expect("input() returned the value", "input=harness-input" in (txt or ""), f"text={txt!r}")
    c.expect("capability probe ran", "caps=" in (txt or "") and "jspi=1" in (txt or ""), f"text={txt!r}")
    c.expect("chrome.* wrappers work from Python",
             "chrome=storage=roundtrip" in (txt or ""), f"text={txt!r}")


def check_l6(level, ctx, env, c: Checks) -> None:
    """A page's own Pyodide CDN fetch should be served from our cache."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/pyodide-page.html")
    first = wait_for(lambda: page.inner_text("#net") if "source=" in page.inner_text("#net") else None, 30)
    c.expect("first page load fetched from the network",
             bool(first) and "source=network" in (first or ""), f"net={page.inner_text('#net')!r}")
    c.expect("server saw one fetch", BLOBS.count("wheel") == 1, f"count={BLOBS.count('wheel')}")

    second = ctx.new_page()
    second.goto(f"{env['base']}/pyodide-page.html")
    cached = wait_for(lambda: second.inner_text("#net") if "source=" in second.inner_text("#net") else None, 30)
    c.expect("second page served from the extension's cache",
             bool(cached) and "source=cache" in (cached or ""), f"net={second.inner_text('#net')!r}")
    c.expect("CDN was not contacted again", BLOBS.count("wheel") == 1, f"count={BLOBS.count('wheel')}")
    mp_first = wait_for(lambda: second.inner_text("#mp") if "source=" in second.inner_text("#mp") else None, 30)
    c.expect("a MicroPython blob was served from cache too",
             bool(mp_first) and "source=cache" in (mp_first or ""), f"mp={second.inner_text('#mp')!r}")
    c.expect("the MicroPython blob was not re-fetched", BLOBS.count("mp") == 1,
             f"count={BLOBS.count('mp')}")
    second.close()

    # Opt this host out through the service worker, then confirm the page goes
    # back to the network on every load.
    workers = ctx.service_workers
    if not workers:
        c.expect("service worker reachable", False, "no MV3 service worker found")
        return
    before = BLOBS.count("wheel")
    workers[0].evaluate("""async () => {
        await chrome.storage.local.set({ pyodex_no_cache_hosts: ["127.0.0.1"] });
        const cache = await caches.open("pyodex-blobs-v1");
        for (const req of await cache.keys()) await cache.delete(req);
    }""")
    third = ctx.new_page()
    third.goto(f"{env['base']}/pyodide-page.html")
    again = wait_for(lambda: third.inner_text("#net") if "source=" in third.inner_text("#net") else None, 30)
    c.expect("opted-out host goes back to the CDN",
             bool(again) and "source=network" in (again or ""), f"net={third.inner_text('#net')!r}")
    c.expect("opt-out caused a fetch", BLOBS.count("wheel") > before,
             f"before={before} after={BLOBS.count('wheel')}")
    third.close()


def check_l5(level, ctx, env, c: Checks) -> None:
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html?mode=fetch")
    first = wait_status(page, "fetch src=")
    c.expect("first load fetched from network", bool(first) and "src=network" in (first or ""),
             f"status={(first or '')[:120]!r}")
    c.expect("server saw exactly one request", BLOBS.count("a") == 1, f"count={BLOBS.count('a')}")

    page.reload()
    cached = wait_status(page, "src=cache")
    c.expect("reload served from cache", bool(cached), f"status={status_now(page)[:160]!r}")
    c.expect("no second network request", BLOBS.count("a") == 1, f"count={BLOBS.count('a')}")

    before = BLOBS.count("a")
    page.goto(f"{env['base']}/login.html?mode=tamper")
    tampered = wait_status(page, "tamper")
    c.expect("mismatched digest is refused, not served", bool(tampered) and "tamper err=" in (tampered or ""),
             f"status={status_now(page)[:200]!r}")
    c.expect("mismatch caused a refetch", BLOBS.count("a") > before,
             f"before={before} after={BLOBS.count('a')}")

    page.goto(f"{env['base']}/login.html?mode=optout")
    opted = wait_status(page, "optout src=")
    c.expect("opted-out host is not cached", bool(opted) and "src=network" in (opted or ""),
             f"status={status_now(page)[:200]!r}")
    c.expect("opt-out list is persisted", bool(opted) and "127.0.0.1" in (opted or ""),
             f"status={status_now(page)[:200]!r}")

    frame_url = (overlay_frame(page, 5000) or page).url if overlay_frame(page, 5000) else ""
    origin = frame_url.rsplit("/", 1)[0] if frame_url else ""
    if origin:
        opts = ctx.new_page()
        opts.goto(origin + "/options.html")
        opts.wait_for_timeout(1500)
        rows = opts.evaluate("() => document.querySelectorAll('#rows tr').length")
        heading = opts.evaluate("() => document.querySelector('h1')?.textContent || ''")
        c.expect("options page renders", "cache" in heading.lower(), f"heading={heading!r}")
        c.expect("options page lists the opt-out", rows >= 1, f"rows={rows}")
        opts.close()

        pop = ctx.new_page()
        pop.goto(origin + "/popup.html")
        pop.wait_for_timeout(1200)
        entries = pop.evaluate("() => document.getElementById('entries')?.textContent || ''")
        c.expect("popup renders cache stats", entries.strip().isdigit(), f"entries={entries!r}")
        pop.close()
    else:
        c.expect("options page reachable", False, "could not derive the extension origin")

    second_b = BLOBS.count("b")
    page.reload()
    wait_status(page, "optout src=")
    c.expect("opted-out host re-fetches every time", BLOBS.count("b") > second_b,
             f"before={second_b} after={BLOBS.count('b')}")


def check_l7(level, ctx, env, c: Checks) -> None:
    """Extension ships no wasm; boot streams it once, then serves from cache."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    booted = wait_status(page, "remote boot")
    c.expect("booted with a remote runtime", bool(booted), f"status={status_now(page)[:200]!r}")
    c.expect("wasm came from the network", BLOBS.count("pyodide.asm.wasm") == 1,
             f"count={BLOBS.count('pyodide.asm.wasm')}")
    c.expect("stdlib came from the network", BLOBS.count("python_stdlib.zip") == 1,
             f"count={BLOBS.count('python_stdlib.zip')}")
    c.expect("first load reports network", "wasm=network" in (booted or ""),
             f"status={(booted or '')[:200]!r}")

    page.reload()
    again = wait_status(page, "remote boot")
    c.expect("rebooted from cache", bool(again) and "cache" in (again or ""),
             f"status={status_now(page)[:200]!r}")
    c.expect("wasm was not re-fetched", BLOBS.count("pyodide.asm.wasm") == 1,
             f"count={BLOBS.count('pyodide.asm.wasm')}")
    c.expect("stdlib was not re-fetched", BLOBS.count("python_stdlib.zip") == 1,
             f"count={BLOBS.count('python_stdlib.zip')}")


def check_l8(level, ctx, env, c: Checks) -> None:
    """Two tabs must be served by the same engine instance."""
    import re

    def boot_id(page, timeout=120):
        text = wait_status(page, "shared boot=", timeout=timeout)
        if not text:
            return None, text
        m = re.search(r"boot=([0-9a-z-]+)", text)
        return (m.group(1) if m else None), text

    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    first_id, first_text = boot_id(page)
    c.expect("engine answered the first tab", bool(first_id), f"status={(first_text or '')[:200]!r}")
    c.expect("engine ran Python", "py=3." in (first_text or ""), f"status={(first_text or '')[:200]!r}")

    second = ctx.new_page()
    second.goto(f"{env['base']}/plain.html")
    second_id, second_text = boot_id(second)
    c.expect("engine answered the second tab", bool(second_id), f"status={(second_text or '')[:200]!r}")
    c.expect("both tabs share one interpreter", first_id is not None and first_id == second_id,
             f"first={first_id} second={second_id}")
    runs = [int(x) for x in re.findall(r"runs=(\d+)", (second_text or ""))]
    c.expect("engine counted more than one run", bool(runs) and runs[0] >= 2, f"runs={runs}")
    second.close()


def check_l9(level, ctx, env, c: Checks) -> None:
    """With ui = "engine" a tab has no interpreter at all."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    filled = wait_for(lambda: page.input_value("#user") == "l9@pytest", 120)
    c.expect("engine filled the first tab (no click, no overlay)",
             bool(filled), f"user={page.input_value('#user')!r}")
    c.expect("password filled too", page.input_value("#pw") == "l9-secret",
             f"pw={page.input_value('#pw')!r}")
    c.expect("no overlay iframe in the tab",
             not any("overlay.html" in (f.url or "") for f in page.frames),
             "an overlay iframe was created")

    second = ctx.new_page()
    second.goto(f"{env['base']}/login.html")
    c.expect("engine filled the second tab",
             bool(wait_for(lambda: second.input_value("#user") == "l9@pytest", 120)),
             f"user={second.input_value('#user')!r}")
    c.expect("second tab has no overlay iframe either",
             not any("overlay.html" in (f.url or "") for f in second.frames),
             "an overlay iframe was created")
    second.close()


def check_l10(level, ctx, env, c: Checks) -> None:
    """The same functional probes, executed in Chrome's V8."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from pyodex.compat import PROBES, status_of

    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    text = wait_status(page, "L10 {")
    if not c.expect("probes ran in the browser", bool(text), f"status={status_now(page)[:200]!r}"):
        return
    try:
        results = json.loads(text.split("L10 ", 1)[1].strip())
    except (json.JSONDecodeError, IndexError) as e:
        c.expect("probe output parsed", False, f"{e}: {(text or '')[:200]!r}")
        return

    failures = {k: v for k, v in results.items() if v != "ok"}
    c.expect("every probed package passes in Chrome", not failures, f"failures={failures}")

    # Node and Chrome must agree with the shipped registry; a disagreement means
    # one of the two environments is lying about a package.
    disagree = []
    for name, verdict in results.items():
        registered, _ = status_of(name)
        if verdict == "ok" and registered == "bad":
            disagree.append(f"{name}: Chrome ok, registry bad")
        if verdict != "ok" and registered == "good":
            disagree.append(f"{name}: Chrome {verdict[:40]}, registry good")
    c.expect("Chrome agrees with the Node-derived registry", not disagree, f"{disagree[:3]}")


def check_l11(level, ctx, env, c: Checks) -> None:
    """The MicroPython tier: a ~2 MB runtime that can still see the DOM."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    frame = overlay_frame(page, 60000)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    caps = wait_for(lambda: frame.evaluate("() => window.pyodexCaps || null"), 60)
    c.expect("runtime is micropython", bool(caps and caps.get("runtime") == "micropython"),
             f"caps={caps!r}")
    c.expect("python ran", "_builtins" in frame.evaluate("() => typeof window.micropython") or
             frame.evaluate("() => !!window.micropython"), "no micropython handle on window")
    marker = wait_for(lambda: frame.evaluate(
        "() => document.body.getAttribute('data-mp') || null"), 30)
    c.expect("python read the overlay DOM", bool(marker and marker.startswith("inputs=")),
             f"marker={marker!r}")
    has_btn = bool(frame.evaluate("() => !!document.getElementById('mp-btn')"))
    c.expect("python painted a widget", has_btn, "no #mp-btn")

    crypto = frame.evaluate("() => document.body.getAttribute('data-crypto') || ''")
    c.expect("sha256 works on the small tier", "sha=ba7816bf8f01" in (crypto or ""), f"crypto={crypto!r}")
    c.expect("hmac-sha256 works", "mac=" in (crypto or "") and "mac=err" not in (crypto or ""),
             f"crypto={crypto!r}")
    http = frame.evaluate("() => document.body.getAttribute('data-http') || ''")
    c.expect("synchronous http_get works", '"ok"' in (http or ""), f"http={http!r}")
    sock = frame.evaluate("() => document.body.getAttribute('data-sock') || ''")
    # The HTTP bridge is verified with curl; from an extension page the sync XHR is
    # blocked as mixed content, so we assert the *finding*, not a success.
    c.expect("real TCP from the 2 MB tier via the proxy HTTP bridge",
             "recv:echo:PING" in (sock or ""), f"sock={sock!r}")


def check_l12(level, ctx, env, c: Checks) -> None:
    """The RustPython tier: ~5 MB, wider stdlib, no DOM bridge of its own."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")

    def find(deadline=90000):
        import time as _t

        end = _t.time() + deadline / 1000
        while _t.time() < end:
            for f in page.frames:
                if any(k in (f.url or "") for k in ("sandbox.html", "overlay.html")):
                    return f
            page.wait_for_timeout(200)
        return None

    frame = find()
    if not c.expect("overlay booted", frame is not None, "no overlay/sandbox frame"):
        return
    caps = wait_for(lambda: frame.evaluate("() => window.pyodexCaps || null"), 90)
    c.expect("runtime is rustpython", bool(caps and caps.get("runtime") == "rustpython"),
             f"caps={caps!r}")
    marker = wait_for(lambda: frame.evaluate("() => document.body.getAttribute('data-rp') || null"), 60)
    c.expect("python reached the page through js_vars",
             bool(marker and marker.startswith("inputs=")), f"marker={marker!r}")


def check_l14(level, ctx, env, c: Checks) -> None:
    """Demo 01: the card renders, the badge counts, the click handler runs."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    text = wait_for(lambda: "Python is running" in frame_text(frame) and frame_text(frame), 60)
    c.expect("card rendered with body text", bool(text), f"text={frame_text(frame)[:120]!r}")
    c.expect("badge shows initial count", "0 clicks" in (text or ""), f"text={(text or '')[:120]!r}")

    frame.click("#demo-btn")
    after = wait_for(lambda: "1 click" in frame_text(frame) and frame_text(frame), 30)
    c.expect("click handler ran and updated badge", bool(after), f"text={frame_text(frame)[:160]!r}")
    c.expect("body text updated too", "You clicked 1 time" in (after or ""), f"text={(after or '')[:160]!r}")


def check_l15(level, ctx, env, c: Checks) -> None:
    """Demo 02: two shop pages, one persistent comparison."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/shop/a?price=42.00")
    frame = overlay_frame(page, 60000)
    if not c.expect("boots on a price page without a click", frame is not None, "no overlay frame"):
        return
    first = wait_for(lambda: "cheapest" in frame_text(frame) and frame_text(frame), 60)
    c.expect("first shop recorded", bool(first), f"text={frame_text(frame)[:160]!r}")
    c.expect("first price shown", "42.00" in (first or ""), f"text={(first or '')[:160]!r}")

    second = ctx.new_page()
    second.goto(f"{env['base']}/shop/b?price=30.00")
    sframe = overlay_frame(second, 60000)
    if not c.expect("boots on the second shop", sframe is not None, "no overlay frame"):
        return
    both = wait_for(lambda: "30.00" in frame_text(sframe) and "42.00" in frame_text(sframe)
                    and frame_text(sframe), 60)
    c.expect("both shops compared", bool(both), f"text={frame_text(sframe)[:200]!r}")
    c.expect("cheapest is the lower price", "cheapest: 30.00" in (both or ""), f"text={(both or '')[:200]!r}")
    second.close()


def check_l16(level, ctx, env, c: Checks) -> None:
    """Demo 03: scan a page of links and flag the lookalikes."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/phish")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    c.expect("prompts to scan", bool(wait_for(lambda: "Press Scan" in frame_text(frame), 60)),
             f"text={frame_text(frame)[:120]!r}")
    frame.click("#scan-btn")
    text = wait_for(lambda: "flagged" in frame_text(frame) and frame_text(frame), 60)
    c.expect("scan produced flags", bool(text), f"text={frame_text(frame)[:160]!r}")
    c.expect("digit-substitution domain caught", "paypa1.com" in (text or ""), f"text={(text or '')[:200]!r}")
    c.expect("near-miss domain caught", "gogle.com" in (text or ""), f"text={(text or '')[:200]!r}")
    c.expect("subdomain trick caught", "paypal.com.evil.example" in (text or ""), f"text={(text or '')[:240]!r}")
    c.expect("clean domain not flagged", "example.com\n" not in (text or ""), f"text={(text or '')[:240]!r}")


def check_l17(level, ctx, env, c: Checks) -> None:
    """Demo 04: save a credential through the UI, reload, fill it back in."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    frame = overlay_frame(page, 60000)
    if not c.expect("boots on a login form", frame is not None, "no overlay frame"):
        return
    c.expect("offers to save", bool(wait_for(lambda: "No credentials saved" in frame_text(frame), 60)),
             f"text={frame_text(frame)[:140]!r}")

    frame.fill("#vault-user", "demo@example.com")
    frame.fill("#vault-pass", "s3cret-value")
    frame.click("#save-btn")
    saved_text = wait_for(lambda: "saved for demo@example.com" in frame_text(frame) and frame_text(frame), 60)
    c.expect("save acknowledged", bool(saved_text), f"text={frame_text(frame)[:160]!r}")
    # the demo's whole point: what is stored is the reversed string, not crypto
    c.expect("stores the password reversed", "eulav-terc3s" in (saved_text or ""),
             f"text={(saved_text or '')[:200]!r}")
    c.expect("says it is not encrypted", "not encrypted" in (saved_text or ""),
             f"text={(saved_text or '')[:200]!r}")
    c.expect("persisted to both file and IndexedDB",
             "file + IndexedDB" in (saved_text or ""), f"text={(saved_text or '')[:200]!r}")
    # Both sinks report success; IndexedDB used to be False because its callback
    # was read synchronously.
    c.expect("file write reported ok", "file=True" in (saved_text or ""), f"text={(saved_text or '')[:200]!r}")
    c.expect("IndexedDB write reported ok", "indexeddb=True" in (saved_text or ""),
             f"text={(saved_text or '')[:220]!r}")

    page.reload()
    f2 = overlay_frame(page, 60000)
    if not c.expect("boots again after reload", f2 is not None, "no overlay frame"):
        return
    c.expect("vault survived reload",
             bool(wait_for(lambda: "saved for demo@example.com" in frame_text(f2), 60)),
             f"text={frame_text(f2)[:160]!r}")
    f2.click("#fill-btn")
    c.expect("password filled into the page",
             bool(wait_for(lambda: page.input_value("#pw") == "s3cret-value", 30)),
             f"pw={page.input_value('#pw')!r}")
    c.expect("username filled into the page", page.input_value("#user") == "demo@example.com",
             f"user={page.input_value('#user')!r}")


def check_l18(level, ctx, env, c: Checks) -> None:
    """Demo 05: three tabs, one interpreter, a shared counter.

    With ui="engine" the widgets are painted by the per-tab *agent*, not by the page,
    so the assertion reads the agent frame's shadow root — looking at the page finds
    nothing (a mistake worth remembering).
    """

    def agent_text(page, needle: str, timeout: float = 90):
        def probe():
            for f in page.frames:
                if "agent.html" not in (f.url or ""):
                    continue
                txt = f.evaluate(
                    """() => {
                        const h = document.getElementById('pyodex-agent-ui');
                        if (!h || !h.shadowRoot) return '';
                        return [...h.shadowRoot.querySelectorAll('.badge,.card-body')]
                               .map(e => e.textContent).join(' | ');
                    }""")
                if needle in (txt or ""):
                    return txt
            return None
        return wait_for(probe, timeout)

    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    first = agent_text(page, "tab(s) seen")
    c.expect("first tab paints its badge in the agent", bool(first), "no badge in the agent shadow root")
    c.expect("counts 1 tab", "1 tab(s) seen" in (first or ""), f"text={(first or '')[:140]!r}")
    c.expect("no overlay iframe (no per-tab interpreter)",
             not any("overlay.html" in (f.url or "") for f in page.frames), "overlay iframe present")

    second = ctx.new_page()
    second.goto(f"{env['base']}/login.html")
    two = agent_text(second, "2 tab(s) seen")
    c.expect("second tab sees the shared count of 2", bool(two), f"text={(two or '')[:160]!r}")
    second.close()


def check_l13(level, ctx, env, c: Checks) -> None:
    """RustPython without a sandbox: the glue's new Function is stubbed."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    frame = overlay_frame(page, 90000)
    if not c.expect("overlay booted (no sandbox page)", frame is not None, "no overlay frame"):
        return
    caps = wait_for(lambda: frame.evaluate("() => window.pyodexCaps || null"), 90)
    c.expect("runtime is rustpython", bool(caps and caps.get("runtime") == "rustpython"), f"caps={caps!r}")
    marker = wait_for(lambda: frame.evaluate("() => document.body.getAttribute('data-rp') || null"), 60)
    c.expect("python reached the DOM from a normal page",
             bool(marker and marker.startswith("inputs=")), f"marker={marker!r}")


def check_l19(level, ctx, env, c: Checks) -> None:
    """Demo 06: exercise the chrome.* namespaces and the DOM surface."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/login.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    c.expect("prompts to run", bool(wait_for(lambda: "Press Run" in frame_text(frame), 60)),
             f"text={frame_text(frame)[:120]!r}")
    frame.click("#tour-btn")
    text = wait_for(lambda: "call(s) exercised" in frame_text(frame) and frame_text(frame), 90)
    if not c.expect("tour produced a report", bool(text), f"text={frame_text(frame)[:200]!r}"):
        return
    for needle, label in (
        ("extension id:", "runtime.id"),
        ("permissions:", "manifest permissions"),
        ("tabs open:", "tabs.count"),
        ("windows:", "windows.all"),
        ("storage.local: local-ok", "storage.local round-trip"),
        ("action badge: set", "action badge"),
        ("dom element value: tour@example.com", "element handle set/get"),
        ("dom set_attr: body[data-tour]=ran", "set_attr on the page"),
    ):
        c.expect(label, needle in (text or ""), f"missing {needle!r} in {(text or '')[:260]!r}")


def check_l20(level, ctx, env, c: Checks) -> None:
    """Demo 07: a chrome event (storage.onChanged) is delivered into Python."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    c.expect("waits for events", bool(wait_for(lambda: "waiting for events" in frame_text(frame), 60)),
             f"text={frame_text(frame)[:120]!r}")

    frame.click("#fire-btn")
    got = wait_for(lambda: "storage.onChanged" in frame_text(frame) and frame_text(frame), 45)
    c.expect("event delivered to Python", bool(got), f"text={frame_text(frame)[:200]!r}")
    c.expect("handler ran and updated the UI", "1 event(s)" in (got or ""), f"text={(got or '')[:200]!r}")

    frame.click("#fire-btn")
    c.expect("second event counted",
             bool(wait_for(lambda: "2 event(s)" in frame_text(frame), 45)),
             f"text={frame_text(frame)[:200]!r}")


def check_l21(level, ctx, env, c: Checks) -> None:
    """Demo 08: the setters exist and their events come back into Python."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#setup-btn")
    text = wait_for(lambda: "alarms:" in frame_text(frame) and frame_text(frame), 60)
    if not c.expect("setters reported", bool(text), f"text={frame_text(frame)[:200]!r}"):
        return
    c.expect("alarm registered", "demo08-tick" in (text or ""), f"text={(text or '')[:200]!r}")
    c.expect("context menu round-trip", "contextMenus: added + removed" in (text or ""),
             f"text={(text or '')[:200]!r}")
    # It does work here, so require it: an "attempted something" assertion would pass
    # even with the API removed, which is the definition of a lying gate.
    c.expect("notification created", "notifications: created" in (text or ""),
             f"text={(text or '')[:200]!r}")
    # alarms have a 30s floor; give it room, then require the event to arrive
    fired = wait_for(lambda: "alarm fired" in frame_text(frame) and frame_text(frame), 90)
    c.expect("alarms.onAlarm reached Python", bool(fired), f"text={frame_text(frame)[:200]!r}")


def check_l22(level, ctx, env, c: Checks) -> None:
    """Demo 09: the permission-gated tooling APIs answer from Python."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#run-btn")
    text = wait_for(lambda: "cookies:" in frame_text(frame) and frame_text(frame), 60)
    if not c.expect("toolbox reported", bool(text), f"text={frame_text(frame)[:200]!r}"):
        return
    print(f"    toolbox -> {text!r}")
    c.expect("cookie round-trip", "cookies: hello" in (text or ""), f"text={(text or '')[:200]!r}")
    c.expect("download created and searchable", "downloads: id=" in (text or "") and "found=1" in (text or ""),
             f"text={(text or '')[:200]!r}")
    c.expect("bookmarks and history readable",
             "bookmarks:" in (text or "") and "history:" in (text or ""), f"text={(text or '')[:200]!r}")
    # identity is wrapped but unexercised as a flow; what IS testable is that the
    # failure is actionable rather than an opaque Chrome error.
    ident = wait_for(lambda: "identity:" in frame_text(frame) and frame_text(frame), 45)
    c.expect("identity failure is actionable",
             "oauth2" in (ident or "").lower() or "client_id" in (ident or "").lower(),
             f"text={(ident or '')[-200:]!r}")


def check_l23(level, ctx, env, c: Checks) -> None:
    """Demo 10: dNR rules are installable and testable; webRequest events arrive."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#run-btn")
    text = wait_for(lambda: "webRequest:" in frame_text(frame) and frame_text(frame), 60)
    if not c.expect("network reported", bool(text), f"text={frame_text(frame)[:200]!r}"):
        return
    print(f"    network -> {text!r}")
    c.expect("dynamic rule installed", "rules: [1]" in (text or ""), f"text={(text or '')[:200]!r}")
    # The rule has to actually block a live request, not merely exist. Retried, because
    # rule application can lag the updateDynamicRules promise under parallel load.
    def fetch_outcome(url: str) -> str:
        return page.evaluate("""async (u) => {
            try { const r = await fetch(u, {cache: 'no-store'}); return r.ok ? 'ok' : 'not-ok'; }
            catch (e) { return 'blocked'; }
        }""", url)

    blocked = "allowed"
    for attempt in range(20):
        blocked = fetch_outcome("/blocked.js?v=%d" % attempt)
        if blocked == "blocked":
            break
        time.sleep(0.5)
    c.expect("rule blocks a real request", blocked == "blocked", f"page fetch -> {blocked!r}")
    allowed = fetch_outcome("/plain.html")
    c.expect("other requests still work", allowed != "blocked", f"page fetch -> {allowed!r}")
    c.expect("webRequest observed a request",
             bool(wait_for(lambda: "webRequest: " in frame_text(frame)
                           and "webRequest: 0 observed" not in frame_text(frame), 30)),
             f"text={frame_text(frame)[:200]!r}")


def check_l24(level, ctx, env, c: Checks) -> None:
    """Demo 11: popup.py is what renders the toolbar popup."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    origin = (frame.url or "").rsplit("/", 1)[0]
    if not c.expect("extension origin derived", origin.startswith("chrome-extension://"), origin):
        return

    pop = ctx.new_page()
    pop.goto(origin + "/popup.html")
    status_text = wait_for(
        lambda: pop.evaluate("() => document.getElementById('status')?.textContent || ''")
        .replace("starting…", "") and pop.evaluate("() => document.getElementById('status')?.textContent || ''"),
        90)
    c.expect("popup rendered by Python", "popup rendered by Python" in (status_text or ""),
             f"status={status_text!r}")
    body = pop.evaluate("() => document.getElementById('app')?.textContent || ''")
    c.expect("popup shows live data from chrome.tabs", "Tabs" in body and "Python" in body,
             f"app={body[:120]!r}")
    pop.close()

    # Same machinery, second surface: panel.py renders the side panel.
    panel = ctx.new_page()
    panel.goto(origin + "/sidepanel.html")
    panel_status = wait_for(
        lambda: "side panel rendered by Python" in
        (panel.evaluate("() => document.getElementById('status')?.textContent || ''") or "")
        and panel.evaluate("() => document.getElementById('status')?.textContent || ''"),
        90)
    c.expect("side panel rendered by Python", bool(panel_status), f"status={panel_status!r}")
    panel_app = panel.evaluate("() => document.getElementById('app')?.textContent || ''")
    c.expect("side panel shows the current tab", "Side panel" in panel_app, f"app={panel_app[:120]!r}")
    panel.close()


def check_l25(level, ctx, env, c: Checks) -> None:
    """Demo 12: wasm exports, memory and Python-implemented imports all work."""
    page = ctx.new_page()
    page.goto(f"{env['base']}/plain.html")
    c.expect("launcher injected", click_launcher(page, level.slug), "launcher not found")
    frame = overlay_frame(page)
    if not c.expect("overlay booted", frame is not None, "no overlay frame"):
        return
    frame.click("#run-btn")
    text = wait_for(lambda: "exports:" in frame_text(frame) and frame_text(frame), 90)
    if not c.expect("wasm ran", bool(text), f"text={frame_text(frame)[:300]!r}"):
        return
    print(f"    wasm -> {text!r}")
    c.expect("export called like a Python function", "add(2, 3) = 5" in (text or ""),
             f"text={(text or '')[:300]!r}")
    c.expect("memory is addressed like bytes", "pyodex wasm" in (text or ""),
             f"text={(text or '')[:300]!r}")
    c.expect("module called a Python host function", "[42]" in (text or ""),
             f"text={(text or '')[:300]!r}")
    c.expect("every export call was instrumented", "'add'" in (text or "") and "'run'" in (text or ""),
             f"text={(text or '')[:300]!r}")


CHECKS = {
    "L1": check_l1,
    "L2": check_l2,
    "L3": check_l3,
    "L4.1": check_l41,
    "L4.2": check_l42,
    "L4.3": check_l43,
    "L5": check_l5,
    "L6": check_l6,
    "L7": check_l7,
    "L8": check_l8,
    "L9": check_l9,
    "L10": check_l10,
    "L11": check_l11,
    "L12": check_l12,
    "L13": check_l13,
    "L14": check_l14,
    "L15": check_l15,
    "L16": check_l16,
    "L17": check_l17,
    "L18": check_l18,
    "L19": check_l19,
    "L20": check_l20,
    "L21": check_l21,
    "L22": check_l22,
    "L23": check_l23,
    "L24": check_l24,
    "L25": check_l25,
}


# --------------------------------------------------------------------------- #
# one level, end to end (runs in a worker thread)
# --------------------------------------------------------------------------- #


CHROME_PROBE_PACKAGES = [
    "requests", "urllib3", "attrs", "packaging", "jinja2", "markupsafe",
    "pyyaml", "click", "python-dateutil", "six", "pydantic", "numpy",
]


def _write_compat_fixture(dst: Path) -> None:
    """Generate the probe fixture from pyodex.compat.PROBES.

    The registry is the single source of truth: if a probe changes, the browser
    run changes with it, so Node and Chrome can never diverge silently.
    """
    import json as _json
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from pyodex.compat import PROBES

    dst.mkdir(parents=True, exist_ok=True)
    deps = [p for p in CHROME_PROBE_PACKAGES if p in PROBES]
    (dst / "pyodex.toml").write_text(
        "[extension]\n"
        'name = "L10 Compat Chrome"\n'
        'matches = ["<all_urls>"]\n'
        "http = true\n"
        'boot = "auto"\n'
        "pyodide_packages = " + _json.dumps(deps) + "\n"
    )
    main = [
        '"""Run the compatibility probes in Chrome V8 (Level 10).',
        "",
        "Same probe strings the Node sweep uses, executed in the browser instead:",
        "that is where extension Python actually runs, and V8 differences (JIT,",
        "wasm memory limits, JSPI) are exactly what a Node-only sweep cannot see.",
        '"""',
        "",
        "import json",
        "import traceback",
        "",
        "from pyodex_runtime import status",
        "",
        "URL = '" + "__URL__" + "'",
        "PROBES = " + repr({p: PROBES[p] for p in deps}),
        "",
        "",
        "def run_probe(name, code):",
        "    ns = {'URL': URL}",
        "    try:",
        "        exec(compile(code, f'<{name}>', 'exec'), ns)",
        "        return 'ok'",
        "    except Exception as e:  # noqa: BLE001",
        "        return f'{type(e).__name__}: {e}'[:220]",
        "",
        "",
        "results = {name: run_probe(name, code) for name, code in PROBES.items()}",
        "status('L10 ' + json.dumps(results))",
        "",
    ]
    (dst / "main.py").write_text("\n".join(main))


def prepare(level: Level, work: Path, ports: dict) -> Path:
    from pyodex.builder import build_extension

    dst = work / level.fixture or (work / level.demo)
    if dst.exists():
        shutil.rmtree(dst)

    if level.demo:
        src = ROOT / "examples" / "demos" / level.demo
        shutil.copytree(src, dst)
        return build_extension(dst, work / "dist" / level.slug)
    if level.name == "L6":
        src = FIXTURES / level.fixture
        shutil.copytree(src, dst)
        toml = dst / "pyodex.toml"
        base = f"http://127.0.0.1:{ports['http']}"
        toml.write_text(
            toml.read_text()
            + '\n[runtime_pins]\n'
            + f'"{base}/mp/micropython.wasm" = "{MP_SHA}"\n'
        )
        return build_extension(dst, work / "dist" / level.slug)

    if level.name == "L11":
        src = FIXTURES / level.fixture
        shutil.copytree(src, dst)
        main = dst / "main.py"
        prelude = (
            f'HTTP_URL = "http://127.0.0.1:{ports["http"]}/json"\n'
            f'ECHO_PORT = {ports.get("echo", 0)}\n'
            f'BRIDGE = "http://127.0.0.1:{ports["proxy"] + 1}"\n'
        )
        main.write_text(prelude + main.read_text())
        return build_extension(dst, work / "dist" / level.slug)

    if level.name == "L10":
        _write_compat_fixture(dst)
        main = dst / "main.py"
        main.write_text(main.read_text().replace("__URL__", f"http://127.0.0.1:{ports['http']}/json"))
        return build_extension(dst, work / "dist" / level.slug)

    src = FIXTURES / level.fixture
    shutil.copytree(src, dst)
    toml = dst / "pyodex.toml"
    if toml.exists() and "%BASE%" in toml.read_text():
        # localhost, not 127.0.0.1: extension pages are secure contexts and Chrome
        # treats localhost as potentially trustworthy for mixed-content purposes.
        toml.write_text(toml.read_text().replace("%BASE%", f"http://localhost:{ports['http']}"))
    main = dst / "main.py"
    src_text = main.read_text()
    if "%PORTS%" in src_text:
        main.write_text(src_text.replace("%PORTS%", repr(ports)))
    if "%DIGEST_A%" in src_text:
        import hashlib

        digest_a = hashlib.sha256(b"blob:a:v1" + b"x" * 2048).hexdigest()
        digest_other = hashlib.sha256(b"blob:a:v2" + b"x" * 2048).hexdigest()
        main.write_text(
            src_text.replace("%BASE%", f"http://127.0.0.1:{ports['http']}")
            .replace("%DIGEST_A%", digest_a)
            .replace("%DIGEST_BTAIL%", digest_other)
        )
    return build_extension(dst, work / "dist" / level.slug)


def run_level(level: Level, env: dict, jobs_hint: str) -> tuple[bool, list[str]]:
    from playwright.sync_api import sync_playwright

    c = Checks(level.name)
    work = Path(tempfile.mkdtemp(prefix=f"pyodex-{level.slug}-"))
    errors: list[str] = []

    # Services this level needs.
    services: list = []
    ports = dict(env["ports"])
    if "echo" in level.needs:
        srv, port = echo_server()
        services.append(srv)
        ports["echo"] = port
    if "tls" in level.needs:
        srv, port = echo_server(env["tls_ctx"])
        services.append(srv)
        ports["tls"] = port
    if "ws" in level.needs:
        ws = WsEcho()
        ws.ready.wait(5)
        ports["ws"] = ws.port

    try:
        built = prepare(level, work, ports)
    except Exception as e:  # noqa: BLE001
        return False, [f"{level.name} build: {type(e).__name__}: {e}"]

    try:
        with sync_playwright() as pw:
            profile = Path(tempfile.mkdtemp(prefix=f"profile-{level.slug}-"))
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel="chromium",       # regular chromium: extension support
                headless=env["headless"],  # headless shell cannot load extensions
                args=[
                    f"--disable-extensions-except={built}",
                    f"--load-extension={built}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            try:
                page_errors: list[str] = []
                CHECKS[level.name](level, ctx, {**env, "ports": ports}, c)
                del page_errors
            except Exception as e:  # noqa: BLE001
                c.expect("harness completed", False, f"{type(e).__name__}: {e}")
            finally:
                ctx.close()
                shutil.rmtree(profile, ignore_errors=True)
    finally:
        for srv in services:
            try:
                srv.shutdown()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(work, ignore_errors=True)

    c.expect("no uncaught page errors", not errors, "; ".join(errors[:3]))
    return not c.failures, c.failures


# --------------------------------------------------------------------------- #
# machine-aware parallelism
# --------------------------------------------------------------------------- #


def available_mb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 4096


def default_jobs() -> int:
    cpu = os.cpu_count() or 4
    by_cpu = max(2, cpu // 2)
    by_mem = max(2, available_mb() // 900)  # browser + Pyodide + numpy budget
    return max(2, min(20, by_cpu, by_mem))


# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    args = argv[1:]
    headed = "--headed" in args
    jobs_flag = next((a.split("=", 1)[1] for a in args if a.startswith("--jobs=")), None)
    if "--jobs" in args:
        jobs_flag = args[args.index("--jobs") + 1]
    wanted = {a.upper() for a in args if a.upper().startswith("L")}
    jobs = int(jobs_flag or os.environ.get("PYODEX_HARNESS_JOBS") or default_jobs())

    include_experimental = "--experimental" in args
    levels = [
        lvl for lvl in LEVELS
        if (not wanted or lvl.name in wanted) and (include_experimental or not lvl.experimental)
    ]
    skipped = [lvl.name for lvl in LEVELS if lvl.experimental and lvl not in levels]
    if not levels:
        print(f"no levels matched {sorted(wanted)}")
        return 2

    if headed and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        print("headed mode without DISPLAY: re-executing under xvfb-run", flush=True)
        return subprocess.call(["xvfb-run", "-a", sys.executable, __file__, *args])

    work = Path(tempfile.mkdtemp(prefix="pyodex-harness-"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cert, key = make_cert(work)
    tls_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls_ctx.load_cert_chain(cert, key)

    proxy_port = free_port()
    proxy = subprocess.Popen(
        [sys.executable, str(ROOT / "proxy" / "python_websocket_proxy.py"),
         "--port", str(proxy_port), "--allow", "127.0.0.1:*"],
        env={**os.environ, "SSL_CERT_FILE": str(cert)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)

    env = {
        "base": f"http://127.0.0.1:{httpd.server_address[1]}",
        "ports": {"proxy": proxy_port, "http": httpd.server_address[1]},
        "tls_ctx": tls_ctx,
        "headless": not headed,
    }

    if skipped:
        print(f"skipping experimental: {', '.join(skipped)} (run with --experimental)")
    print(f"levels={len(levels)} jobs={jobs} headless={not headed} "
          f"(cpus={os.cpu_count()}, mem_available={available_mb()} MB)", flush=True)

    results: dict[str, bool] = {}
    failures: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(run_level, lvl, env, f"{jobs}"): lvl for lvl in levels}
            for fut in futures:
                lvl = futures[fut]
                try:
                    ok, fails = fut.result()
                except Exception as e:  # noqa: BLE001
                    ok, fails = False, [f"{lvl.name} crashed: {type(e).__name__}: {e}"]
                results[lvl.name] = ok
                failures.extend(fails)
                print(f"[{lvl.name}] {'PASS' if ok else 'FAIL'} — {lvl.description}", flush=True)
    finally:
        proxy.terminate()
        httpd.shutdown()

    # Monotonicity: a passing level implies every strictly lower rank passes.
    violations = []
    for name, ok in results.items():
        if not ok:
            continue
        rank = next(l.rank for l in LEVELS if l.name == name)
        for other in LEVELS:
            if other.rank < rank and other.name in results and not results[other.name]:
                violations.append(f"{name} passes but {other.name} (rank {other.rank}) fails")

    print("\n=== results ===")
    for lvl in LEVELS:
        if lvl.name in results:
            print(f"  {lvl.name:<6} {'PASS' if results[lvl.name] else 'FAIL'}  {lvl.description}")

    if failures:
        print("\nfailures:")
        for f in failures:
            print(f"  - {f}")
    if violations:
        print("\nmonotonicity violated (results are not trustworthy):")
        for v in violations:
            print(f"  - {v}")

    ok = bool(results) and all(results.values()) and not violations
    print(f"\n{sum(results.values())}/{len(results)} levels passed"
          + ("" if ok else "  <-- NOT a valid gate"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
