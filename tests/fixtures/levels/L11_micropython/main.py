"""L11 — the MicroPython tier: ~2 MB, present in every tab, real DOM access.

Exercises the small-tier shims individually so a missing one does not hide the
others: sha256/hmac (hashlib), synchronous HTTP (XHR), and sockets, which this
tier refuses on purpose.
"""

import pyodex_mp as ui

ui.status("mp booted")
n = ui.count("input")
ui.button("mp", id="mp-btn", top=16, left=16)
ui.document.body.setAttribute("data-mp", f"inputs={n}")


def probe(name, fn):
    try:
        value = fn()
        ui.document.body.setAttribute(name, str(value))
    except Exception as e:
        ui.document.body.setAttribute(name, f"err:{type(e).__name__}:{str(e)[:40]}")


probe("data-crypto", lambda: f"sha={ui.sha256_hex('abc')[:12]} mac={ui.hmac_sha256_hex('k', 'm')[:12]}")
probe("data-http", lambda: ui.http_get(HTTP_URL)[:40])
def sock_probe():
    """Record how far the sequence gets; each step persists on the DOM so the
    first fatal call is identifiable after the interpreter dies."""
    stage = []

    def mark(v):
        stage.append(v)
        ui.set_attr("body", "data-sock", "/".join(stage))

    try:
        mark("ctor")
        sock = ui.Socket("127.0.0.1", ECHO_PORT, bridge=BRIDGE)
        mark("open")
        sock.connect()
        mark("connected")
        sock.send(b"PING\n")
        mark("sent")
        data = sock.recv()
        mark("recv:%s" % data.decode().strip())
        sock.close()
        mark("closed")
    except Exception as e:
        mark("err:%s:%s" % (type(e).__name__, str(e)[:60]))
    return "/".join(stage)


probe("data-sock", sock_probe)
