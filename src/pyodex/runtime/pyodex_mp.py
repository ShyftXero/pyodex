"""pyodex_mp: the small API surface an in-page MicroPython tier needs.

Deliberately tiny. No package loading, no async event loop, no sockets (those
belong to a heavier tier) — just enough to paint UI and read the page.
"""

import js

document = js.document
window = js.window


def status(text, error=False):
    el = document.getElementById("pyodex-status")
    if el is None:
        return
    if text is None:
        el.classList.add("hidden")
        return
    el.classList.remove("hidden")
    el.textContent = text
    if error:
        el.classList.add("error")


def el(tag, id=None, text=None, **style):
    e = document.createElement(tag)
    if id:
        e.id = id
    if text is not None:
        e.innerText = text
    for k, v in style.items():
        setattr(e.style, k.replace("_", "-") if False else k, v)
    document.body.appendChild(e)
    return e


def button(label, id=None, top=16, left=16):
    b = el("button", id=id, text=label)
    b.style.position = "fixed"
    b.style.top = f"{top}px"
    b.style.left = f"{left}px"
    b.style.pointerEvents = "auto"
    return b


def on(target, event, fn):
    """Attach a handler; keep it referenced so it is not collected."""
    target.addEventListener(event, fn)
    return fn


def count(sel):
    return document.querySelectorAll(sel).length


def text_of(sel):
    e = document.querySelector(sel)
    return e.textContent if e else None


# --------------------------------------------------------------------------- #
# crypto / http / sockets, such as they can be on a 2 MB tier
# --------------------------------------------------------------------------- #

try:
    import hashlib as _hashlib  # MicroPython ships a cut-down hashlib
except ImportError:  # pragma: no cover
    _hashlib = None


def sha256_hex(data):
    """SHA-256 of bytes or str, as hex.

    MicroPython's hashlib has no `.hexdigest()`, so hexlify the raw digest.
    """
    import binascii

    if isinstance(data, str):
        data = data.encode()
    return binascii.hexlify(_hashlib.sha256(data).digest()).decode()


def hmac_sha256_hex(key, msg):
    """HMAC-SHA256. Implemented here because MicroPython's is not always present."""
    import binascii

    if isinstance(key, str):
        key = key.encode()
    if isinstance(msg, str):
        msg = msg.encode()
    block = 64
    if len(key) > block:
        key = binascii.unhexlify(sha256_hex(key))
    key = key + b"\x00" * (block - len(key))
    inner = bytes((b ^ 0x36) for b in key) + msg
    outer = bytes((b ^ 0x5C) for b in key) + binascii.unhexlify(sha256_hex(inner))
    return sha256_hex(outer)


def http_get(url, timeout_ms=15000):
    """Synchronous GET.

    The browser offers no synchronous fetch, so this uses a synchronous
    XMLHttpRequest. That blocks the tab, which is acceptable for a 2 MB tier doing
    an occasional small request and unacceptable for anything heavier — heavy HTTP
    belongs on the Pyodide tier, which has real async clients.
    """
    xhr = js.XMLHttpRequest.new()
    xhr.open("GET", url, False)
    xhr.send()
    return xhr.responseText


def http_post_json(url, body, timeout_ms=15000):
    import json as _json

    xhr = js.XMLHttpRequest.new()
    xhr.open("POST", url, False)
    xhr.setRequestHeader("content-type", "application/json")
    xhr.send(_json.dumps(body))
    return xhr.responseText


class SocketUnsupported(NotImplementedError):
    pass


def socket(*args, **kwargs):
    """Sockets are not available on this tier.

    MicroPython's wasm port has no socket API, and faking one would only move the
    failure. Use the Pyodide tier (which routes through python_websocket_proxy) or
    a native messaging host.
    """
    raise SocketUnsupported(
        "MicroPython tier has no sockets; move this app to the Pyodide tier "
        "(runtime = 'pyodide') "
    )
