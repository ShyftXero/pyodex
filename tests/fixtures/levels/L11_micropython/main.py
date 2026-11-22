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
    try:
        ui.socket()
        return "unexpected"
    except ui.SocketUnsupported:
        return "unsupported"


probe("data-sock", sock_probe)
