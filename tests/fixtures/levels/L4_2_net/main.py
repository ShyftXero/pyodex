# /// script
# dependencies = ["requests"]
# ///
"""L4.2 — networking capabilities.

Covers: requests (HTTP), blocking raw TCP through the proxy, TLS terminated by
the proxy via the stdlib ssl shim, and the async WebSocket client.
"""

import asyncio
import socket

from pyodex_runtime import Overlay, WebSocketClient, on, status
import pyodex_natural as nat

PORTS = %PORTS%
nat.config.proxy = f"ws://127.0.0.1:{PORTS['proxy']}"

ui = Overlay()
btn = ui.button("L4.2 run", top=16, left=16, id="l42-btn")
out = ui.panel(id="l42-out")
out.innerText = "idle"


async def run(_event=None):
    res: dict[str, str] = {}

    try:
        import requests

        r = requests.get(f"http://127.0.0.1:{PORTS['http']}/json", timeout=15)
        res["http"] = "ok" if r.json().get("ok") else "bad"
    except Exception as e:  # noqa: BLE001
        res["http"] = f"err:{type(e).__name__}"

    try:  # blocking stdlib socket, routed over /tcp by pyodex_natural
        sock = socket.create_connection(("127.0.0.1", PORTS["echo"]), timeout=15)
        sock.sendall(b"PING\n")
        res["tcp"] = sock.recv(200).decode().strip()
        sock.close()
    except Exception as e:  # noqa: BLE001
        res["tcp"] = f"err:{type(e).__name__}"

    try:  # TLS is terminated by the proxy, so the shim only routes to /tls
        import ssl

        raw = socket.create_connection(("127.0.0.1", PORTS["tls"]), timeout=20)
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname="127.0.0.1")
        tls.sendall(b"TLS\n")
        res["tls"] = tls.recv(200).decode().strip()
        tls.close()
    except Exception as e:  # noqa: BLE001
        res["tls"] = f"err:{type(e).__name__}"

    try:
        ws = await WebSocketClient.connect(f"ws://127.0.0.1:{PORTS['ws']}/", timeout=15)
        ws.send("PING\n")
        res["ws"] = (await ws.recv() or "").strip()
        ws.close()
    except Exception as e:  # noqa: BLE001
        res["ws"] = f"err:{type(e).__name__}"

    out.innerText = "\n".join(f"{k}={v}" for k, v in res.items())
    status("L4.2 done")


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(run())
