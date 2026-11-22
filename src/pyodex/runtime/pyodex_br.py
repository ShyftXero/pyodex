"""pyodex_br: the Brython tier's API surface.

Brython runs Python in the page with a real DOM, so this stays thin: it is a
convenience layer over `browser.document`, not a bridge.
"""

# Brython exposes `document` as a builtin in scripts; importing `browser` would
# require brython_stdlib.js (4.4 MB), which this tier exists to avoid.
document = document


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


def _camel(name):
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def el(tag, id=None, text=None, **style):
    e = document.createElement(tag)
    if id:
        e.id = id
    if text is not None:
        e.innerText = text
    for k, v in style.items():
        setattr(e.style, _camel(k), v)
    document.body.appendChild(e)
    return e


def button(label, id=None, top=16, left=16):
    b = el("button", id=id, text=label)
    b.style.position = "fixed"
    b.style.top = "%dpx" % top
    b.style.left = "%dpx" % left
    b.style.pointerEvents = "auto"
    b.bind("click", lambda ev: None)  # placeholder so binds can be added by the app
    return b


def on(target, event, fn):
    target.bind(event, fn)
    return fn


def count(sel):
    return len(document.select(sel))


def text_of(sel):
    nodes = document.select(sel)
    return nodes[0].textContent if nodes else None


def set_attr(selector, name, value):
    nodes = document.select(selector)
    if not nodes:
        return False
    nodes[0].setAttribute(name, str(value))
    return True
