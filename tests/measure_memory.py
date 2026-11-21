"""Measure what a tab's interpreter actually costs.

The claim under test: one Pyodide instance per tab costs tens of MB, and moving
it into a shared engine removes that per-tab cost. Estimates are not evidence, so
this measures renderer RSS from the OS while adding tabs.

    uv run tests/measure_memory.py                 # bundled runtime, overlay per tab
    uv run tests/measure_memory.py --engine shared # compare against the shared engine

Renderer processes are attributed by their --user-data-dir, so the numbers are for
this browser only, not the rest of the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PAGE = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>m</title></head><body><h1>t</h1></body></html>"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


def rss_mb(profile: Path) -> float:
    """Sum RSS of Chromium processes belonging to this profile."""
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            if str(profile) not in cmd:
                continue
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) / 1024
                break
    return total


def fixture(work: Path, engine: str) -> Path:
    from pyodex.builder import build_extension

    d = work / f"mem-{engine}"
    d.mkdir(parents=True)
    (d / "pyodex.toml").write_text(
        f'[extension]\nname = "Mem {engine}"\nboot = "user"\nhttp = false\nengine = "{engine}"\n'
    )
    (d / "main.py").write_text(
        "from pyodex_runtime import Overlay, status\n"
        "ui = Overlay()\n"
        "ui.button('mem', top=16, left=16, id='mem-btn')\n"
        "status('mem ready')\n"
    )
    return build_extension(d, work / f"out-{engine}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="local", choices=["local", "shared"])
    ap.add_argument("--tabs", type=int, default=3)
    ap.add_argument("--out", help="write JSON results here")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    work = Path(tempfile.mkdtemp(prefix="pyodex-mem-"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    built = fixture(work, args.engine)
    profile = Path(tempfile.mkdtemp(prefix="mem-profile-"))

    rows: list[dict] = []
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel="chromium",
                headless=True,
                args=[f"--disable-extensions-except={built}", f"--load-extension={built}", "--no-first-run"],
            )

            def sample(label: str) -> None:
                time.sleep(2.0)  # let allocation settle
                mb = rss_mb(profile)
                rows.append({"label": label, "rss_mb": round(mb, 1)})
                print(f"{label:<34} {mb:8.1f} MB total")

            pages = []
            for i in range(args.tabs):
                p = ctx.new_page()
                p.goto(f"{base}/page{i}.html")
                pages.append(p)
                sample(f"{i + 1} tab(s), no overlay")

            for i, p in enumerate(pages):
                p.evaluate(
                    """() => {
                        const host = document.getElementById('pyodex-mem-%s-launcher');
                        host && host.shadowRoot.querySelector('button').click();
                    }"""
                    % args.engine
                )
                p.wait_for_timeout(4000)  # boot
                sample(f"{i + 1} tab(s), overlay booted")

            ctx.close()
    finally:
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)

    # Per-tab cost: slope of the "overlay booted" series.
    booted = [r for r in rows if "overlay booted" in r["label"]]
    if len(booted) >= 2:
        per_tab = (booted[-1]["rss_mb"] - booted[0]["rss_mb"]) / (len(booted) - 1)
        print(f"\nper-tab interpreter cost ({args.engine}): ~{per_tab:.0f} MB")
        rows.append({"label": "per-tab interpreter cost", "rss_mb": round(per_tab, 1)})

    if args.out:
        Path(args.out).write_text(json.dumps({"engine": args.engine, "rows": rows}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
