"""Measure demo 06 (Python) against its JavaScript equivalent.

For each extension, in a fresh profile, on the same page:
  * time from navigation to a usable launcher (the boot cost)
  * time from clicking the tour to a rendered report (the work)
  * renderer RSS for the whole browser

    uv run tests/compare_js_python.py
"""

from __future__ import annotations

import hashlib, json, os, shutil, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGE = """<!DOCTYPE html><html><body><h1>Shop</h1>
<input name="user"><p>host page</p></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass


def rss_mb(profile: Path) -> float:
    total = 0.0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            if str(profile) not in cmd:
                continue
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) / 1024
                    break
        except OSError:
            continue
    return total


def measure(label: str, ext: Path, launcher_id: str | None, port: int) -> dict:
    from playwright.sync_api import sync_playwright

    profile = Path(tempfile.mkdtemp(prefix=f"cmp-{label}-"))
    out: dict = {"label": label}
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-first-run"])
            page = ctx.new_page()
            t0 = time.time()
            page.goto(f"http://127.0.0.1:{port}/")
            # launcher usable?
            if launcher_id:
                clicked = page.evaluate(
                    """(id) => {
                        const h = document.getElementById(id);
                        const b = h && h.shadowRoot && h.shadowRoot.querySelector('button');
                        if (!b) return false;
                        b.click(); return true;
                    }""", launcher_id)
            else:
                clicked = page.evaluate("""() => {
                    const h = document.getElementById('js-tour-host');
                    const b = h && h.shadowRoot && h.shadowRoot.querySelector('#tour-btn');
                    if (!b) return false; b.click(); return true; }""")
                out["t_launcher_ms"] = round((time.time() - t0) * 1000)
            out["t_launcher_ms"] = round((time.time() - t0) * 1000)
            if not clicked:
                out["error"] = "launcher not found"
                return out

            # wait for the tour button, click it, then wait for the report
            if launcher_id:
                deadline = time.time() + 120
                frame = None
                while time.time() < deadline and frame is None:
                    for f in page.frames:
                        if "overlay.html" in (f.url or ""):
                            frame = f
                            break
                    page.wait_for_timeout(100)
                if frame is None:
                    out["error"] = "no overlay"
                    return out
                t_boot = time.time()
                frame.wait_for_selector("#tour-btn", timeout=120_000)
                out["t_boot_ms"] = round((time.time() - t_boot) * 1000)  # Pyodide boot
                t1 = time.time()
                frame.click("#tour-btn")
                frame.wait_for_function(
                    "() => document.body.innerText.includes('call(s) exercised')", timeout=120_000)
                out["t_tour_ms"] = round((time.time() - t1) * 1000)
                text = frame.evaluate("() => document.body.innerText")
                out["calls"] = text.split("—")[1].strip().split("\n")[0][:40]
            else:
                page.wait_for_selector("#js-tour-host", timeout=30_000)
                t1 = time.time()
                page.evaluate("""() => {
                    const h = document.getElementById('js-tour-host');
                    h.shadowRoot.querySelector('#tour-btn').click(); }""")
                page.wait_for_function(
                    "() => document.documentElement.dataset.jsTour", timeout=30_000)
                data = json.loads(page.evaluate("() => document.documentElement.dataset.jsTour"))
                out["t_tour_ms"] = round((time.time() - t1) * 1000)
                out["calls"] = f"{data['ok']} calls"
            time.sleep(1.5)
            out["rss_mb"] = round(rss_mb(profile), 1)
            ctx.close()
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return out


def main() -> int:
    from pyodex.builder import build_extension

    work = Path(tempfile.mkdtemp(prefix="cmp-build-"))
    py_ext = build_extension(ROOT / "examples" / "demos" / "06-api-tour", work / "py")
    js_ext = ROOT / "examples" / "js-equivalent" / "06-api-tour"

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        py = measure("python", py_ext, "pyodex-demo-06-api-tour-launcher", port)
        js = measure("javascript", js_ext, None, port)
    finally:
        srv.shutdown()
        shutil.rmtree(work, ignore_errors=True)

    print(f"{'':<12}{'python':>14}{'javascript':>14}")
    for key, label in (("t_launcher_ms", "launcher ready (ms)"), ("t_boot_ms", "interpreter boot (ms)"),
                       ("t_tour_ms", "tour (ms)"),
                       ("rss_mb", "browser RSS (MB)"), ("calls", "work done")):
        print(f"{label:<12}{str(py.get(key, py.get('error', '-'))):>14}{str(js.get(key, js.get('error', '-'))):>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
