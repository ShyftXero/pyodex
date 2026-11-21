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
<body><h1>app that uses pyodide</h1><div id="net">pending</div>
<script>
  // Mimics what the Pyodide loader does: fetch a blob from the CDN path.
  (async () => {
    const url = "/pyodide/v314.0.8/full/demo-1.0.0-py3-none-any.whl";
    try {
      const r = await fetch(url);
      const buf = await r.arrayBuffer();
      const src = r.headers.get("x-pyodex-source") || "cdn";
      document.getElementById("net").textContent = `source=${src} len=${buf.byteLength}`;
    } catch (e) {
      document.getElementById("net").textContent = "error=" + e.message;
    }
  })();
</script>
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


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/blob/"):
            body = BLOBS.payload(self.path.rsplit("/", 1)[-1].split("?")[0])
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("cache-control", "no-store")
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
}


# --------------------------------------------------------------------------- #
# one level, end to end (runs in a worker thread)
# --------------------------------------------------------------------------- #


def prepare(level: Level, work: Path, ports: dict) -> Path:
    from pyodex.builder import build_extension

    src = FIXTURES / level.fixture
    dst = work / level.fixture
    if dst.exists():
        shutil.rmtree(dst)
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

    levels = [lvl for lvl in LEVELS if not wanted or lvl.name in wanted]
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
