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


def _xhr(method, url, body=None):
    xhr = js.XMLHttpRequest.new()
    xhr.open(method, url, False)
    if body is not None:
        xhr.send(body)
    else:
        xhr.send()
    return xhr.responseText


class Socket:
    """Real TCP, via the proxy's HTTP bridge.

    This tier has no WebSocket client, so the transport is request/response: each
    recv() is one long-poll. A latency trade, not a capability one — the proxy
    opens a real TCP connection either way.

        s = ui.Socket("127.0.0.1", 6379, bridge="http://127.0.0.1:1235")
        s.connect(); s.send(b"PING\r\n"); print(s.recv())
    """

    def __init__(self, host, port, bridge="http://127.0.0.1:1235", token=None, timeout=5):
        self.base = "%s/tcp/%s/%s" % (bridge.rstrip("/"), host, port)
        self.token = token
        self.timeout = timeout
        self.id = None

    def _url(self, tail):
        sep = "&" if "?" in tail else "?"
        return self.base + tail + (sep + "token=" + str(self.token) if self.token else "")

    def connect(self):
        import json

        self.id = json.loads(_xhr("POST", self._url("/open")))["id"]
        return self

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        _xhr("POST", self._url("/send?id=" + self.id), bytes(data))
        return len(data)

    def sendall(self, data):
        self.send(data)

    def recv(self, timeout=None):
        import binascii
        import json

        t = self.timeout if timeout is None else timeout
        out = json.loads(_xhr("GET", self._url("/recv?id=%s&timeout=%s" % (self.id, t))))
        return binascii.a2b_base64(out["data"])

    def recv_until(self, sep=b"\n", limit=1 << 20):
        buf = b""
        while sep not in buf:
            if len(buf) > limit:
                raise ValueError("recv_until limit exceeded")
            chunk = self.recv()
            if not chunk:
                break
            buf += chunk
        return buf

    def close(self):
        if self.id:
            try:
                _xhr("POST", self._url("/close?id=" + self.id))
            finally:
                self.id = None


def socket(*args, **kwargs):
    """Kept for symmetry: returns a Socket bound to the arguments given."""
    return Socket(*args, **kwargs)
