"""L31 — attach when an element appears, release when it goes.

Proves: `@attach_when` runs for elements added after boot, the returned handle is
released on removal, and attaching again after a removal works (the watcher is
not one-shot).
"""

from pyodex import attach_when, ctx, ext_page, handler, js, start, status


class Panel:
    def __init__(self, element):
        self.element = element
        element.text = "panel"

    def close(self):
        ext_page.log.text = "released"


@start
def main():
    ext_page.log.text = "ready"
    status(None)


@attach_when("#host .panel")
def attach_panel(element):
    ext_page.log.text = "attached"
    return Panel(element)


@handler  # main.html: onclick="add_panel"
def add_panel():
    node = js.document.createElement("div")
    node.className = "panel"
    ctx.last = node  # owned by ctx as well: released on teardown
    ext_page.get("#host").unwrap().appendChild(node)


@handler  # main.html: onclick="remove_panel"
def remove_panel():
    last = getattr(ctx, "last", None)
    if last is not None:
        last.remove()
