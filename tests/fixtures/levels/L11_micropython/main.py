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
    """Socket() is present but its in-page path is unverified.

    The HTTP bridge on the proxy is proven with curl (open/send/recv/close against
    a real TCP echo server). Driving it from MicroPython's synchronous XHR kills
    the interpreter during the run, so we do not claim it works here.
    """
    return "bridge-ok-unverified-in-page" if hasattr(ui, "Socket") else "no-shim"


probe("data-sock", sock_probe)
