"""Optional real-Chrome smoke for Demo 17; see examples/experimental/17-ssh/README.md."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import queue
import re
import shutil
import socketserver
import tempfile
import threading
from pathlib import Path

import asyncssh
from playwright.sync_api import expect, sync_playwright

from pyodex.iwa import build_iwa, serve_iwa

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/experimental/17-ssh"
PASSWORD = "fixture-password"


@contextlib.contextmanager
def ssh_server():
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    port = queue.Queue()
    evidence = queue.Queue()
    stop = threading.Event()

    class Server(asyncssh.SSHServer):
        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            ok = (username, password) == ("demouser", PASSWORD)
            evidence.put(("auth", ok))
            return ok

    async def shell(process):
        process.stdout.write("PYODEX_AUTHENTICATED\r\n")
        data = (await process.stdin.readline()).strip()
        evidence.put(("input", data))
        process.stdout.write(f"PYODEX_ECHO:{data}\r\n")
        process.exit(0)

    async def run():
        server = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=shell,
        )
        port.put(server.get_port())
        await asyncio.to_thread(stop.wait)
        server.close()
        await server.wait_closed()

    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    try:
        yield port.get(timeout=10), host_key.get_fingerprint(), evidence
    finally:
        stop.set()
        thread.join(timeout=5)


@contextlib.contextmanager
def tcp_server():
    class Echo(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(self.rfile.read(4))

    with socketserver.TCPServer(("127.0.0.1", 0), Echo) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join()


def app_origin(browser):
    page = browser.pages[0]
    for _ in range(15):
        page.goto("chrome://web-app-internals/")
        page.wait_for_timeout(700)
        dump = page.evaluate("document.querySelector('web-app-internals-app')?.shadowRoot?.textContent || ''")
        installed = dump.split('"LockManager"')[0]
        match = re.search(r"isolated-app://[a-z2-7]+", installed)
        if match:
            return match.group(0)
    raise AssertionError("Chrome did not install the IWA; check dev-mode support and the server log")


def check_ssh(page, port, fingerprint, evidence):
    page.locator("#host").fill("127.0.0.1")
    page.locator("#user").fill("demouser")
    page.locator("#port").fill(str(port))
    page.locator("#connect").click()
    prompt = page.locator("#pyodex-ssh-prompt")
    answer = page.locator("#pyodex-ssh-prompt-answer")
    terminal = page.frame_locator("#terminal iframe").locator("body")
    expect(prompt).to_be_visible(timeout=20_000)
    expect(answer).to_have_attribute("type", "text")
    expect(terminal).to_contain_text(fingerprint)
    assert evidence.empty(), "authentication started before host verification"
    answer.fill(fingerprint)
    page.get_by_role("button", name="Continue").click()
    expect(prompt).to_be_visible(timeout=12_000)
    expect(answer).to_have_attribute("type", "password")
    answer.fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(terminal).to_contain_text("PYODEX_AUTHENTICATED", timeout=12_000)
    page.keyboard.type("terminal-roundtrip")
    page.keyboard.press("Enter")
    expect(terminal).to_contain_text("PYODEX_ECHO:terminal-roundtrip")
    expect(page.locator("#connection-status")).to_contain_text("status 0")
    assert evidence.get(timeout=5) == ("auth", True)
    assert evidence.get(timeout=5) == ("input", "terminal-roundtrip")
    print("SSH: host verification, password auth, and bidirectional PTY passed.")


def check_asset_boundaries(page):
    page.evaluate("py => pyodide.runPythonAsync(py)", """
from pyodex import asset_url, import_js_module, worker_script_url

assert asset_url("vendor/plugin/wasm/ssh.wasm").startswith("isolated-app://")
for load, path in (
    (asset_url, "../vendor/plugin/wasm/ssh.wasm"),
    (import_js_module, "https://example.invalid/worker.js"),
    (worker_script_url, "bootstrap.js"),
    (worker_script_url, "vendor/plugin/wasm/ssh.wasm"),
):
    try:
        result = load(path)
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:
        assert "bundled JavaScript module" in str(exc) or "[extension].assets" in str(exc)
    else:
        raise AssertionError(f"Unexpectedly admitted {path}")
""")


def check_async_handler(page):
    page.evaluate("py => pyodide.runPythonAsync(py)", """
import asyncio
from pyodex import document, on

form = document.createElement("form")
finished = asyncio.get_running_loop().create_future()

@on(form, "submit", prevent_default=True)
async def submit(event):
    await asyncio.sleep(0)
    finished.set_result(event.defaultPrevented)

event = document.createEvent("Event")
event.initEvent("submit", True, True)
assert not form.dispatchEvent(event), "default was not cancelled during dispatch"
assert await asyncio.wait_for(finished, 5)
""")


def check_python_tcp(page, port):
    python = f"""
import socket
from pyodex_natural import config
config.proxy = "ws://127.0.0.1:1"
with socket.create_connection(("127.0.0.1", {port}), timeout=8) as sock:
    assert sock._sock is not None and sock._ws is None
    sock.settimeout(0.05)
    try:
        sock.recv(1)
    except TimeoutError:
        pass
    else:
        raise AssertionError("recv did not time out")
    sock.settimeout(5)
    payload = bytes([0, 255, 4, 10])
    sock.sendall(payload)
    data = bytearray()
    while chunk := sock.recv(4):
        data.extend(chunk)
    assert data == payload
    assert sock.recv(4) == sock.recv(4) == b""
"""
    page.evaluate("py => pyodide.runPythonAsync(py)", python)
    print("Python Direct Sockets: binary echo, read timeout, and repeated EOF passed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="chrome", help="Playwright channel (default: chrome)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pyodex-iwa-smoke-") as temp:
        root = Path(temp)
        project = root / "project"
        shutil.copytree(DEMO, project, ignore=shutil.ignore_patterns("dist", "__pycache__"))
        built = build_iwa(project, root / "app", direct_sockets=True)
        with serve_iwa(built) as server, sync_playwright() as playwright:
            try:
                with contextlib.closing(playwright.chromium.launch_persistent_context(
                    str(root / "profile"), channel=args.channel, headless=False, args=[
                        "--enable-features=IsolatedWebApps,IsolatedWebAppDevMode",
                        f"--install-isolated-web-app-from-url=http://127.0.0.1:{server.server_port}",
                    ],
                )) as browser:
                    origin = app_origin(browser)
                    page = browser.new_page()
                    # Accept only the fixture's loopback permission in this disposable profile.
                    cdp = browser.new_cdp_session(page)
                    cdp.send("Browser.setPermission", {
                        "origin": origin, "permission": {"name": "loopback-network"}, "setting": "granted",
                    })
                    page_errors = []
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on("console", lambda msg: print(msg.text, flush=True) if msg.type == "error" else None)
                    page.goto(origin + "/index.html")
                    expect(page.locator("#connect")).to_be_enabled(timeout=45_000)
                    assert page.evaluate("crossOriginIsolated && typeof TCPSocket === 'function' && !!window.pyodide")
                    check_asset_boundaries(page)
                    check_async_handler(page)
                    with ssh_server() as (port, fingerprint, evidence):
                        check_ssh(page, port, fingerprint, evidence)
                    with tcp_server() as port:
                        check_python_tcp(page, port)
                    assert not page_errors, page_errors
            finally:
                server.shutdown()


if __name__ == "__main__":
    main()
