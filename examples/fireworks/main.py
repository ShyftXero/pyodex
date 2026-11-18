"""Fireworks example: Python drives fireworks-js, plus optional remote triggers.

Demonstrates:
  * vendored JS lib called from Python (fireworks-js)
  * HTTP from Python via `requests` (bundled shim)
  * optional WebSocket control channel through python_websocket_proxy
"""

import asyncio

from pyodex_runtime import Overlay, TcpSocket, document, jsobj, on, status, vendored

ui = Overlay()
container = document.getElementById("fireworks-container")
btn = ui.button("Launch Fireworks 🎇", top=16, left=16, id="py-fireworks-btn")

config = jsobj({
    "autoresize": True,
    "opacity": 0.5,
    "acceleration": 1.05,
    "friction": 0.97,
    "gravity": 1.5,
    "particles": 60,
    "explosion": 6,
    "intensity": 35,
})
Fireworks = vendored("Fireworks")
fireworks = Fireworks.new(container, config)

running = False


def set_running(value: bool) -> None:
    global running
    running = value
    if running:
        fireworks.start()
        btn.innerText = "Stop Fireworks 🎆"
    else:
        fireworks.stop()
        btn.innerText = "Launch Fireworks 🎇"


@on(btn, "click")
def toggle(_event):
    set_running(not running)


# --- HTTP from Python: `requests` works because urllib3 has an Emscripten backend.
def demo_http() -> None:
    try:
        import requests

        r = requests.get("https://httpbin.org/get", timeout=10)
        print("[fireworks] requests ok:", r.status_code, r.json().get("origin"))
    except Exception as e:  # noqa: BLE001
        print("[fireworks] requests failed:", e)


# --- Remote control over raw TCP via the proxy. Run:
#       uv run proxy/python_websocket_proxy.py --port 1234 --allow localhost:*
#     then `nc -l 9999` and type `launch` / `stop`.
async def remote_control() -> None:
    try:
        sock = await TcpSocket.connect("localhost", 9999, timeout=3)
    except Exception:  # noqa: BLE001
        return  # proxy not running; feature is optional
    print("[fireworks] remote control connected")
    while True:
        line = await sock.recv_until(b"\n")
        if not line:
            break
        cmd = line.strip().lower()
        if cmd == b"launch":
            set_running(True)
        elif cmd == b"stop":
            set_running(False)


demo_http()
asyncio.ensure_future(remote_control())
status(None)
