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
