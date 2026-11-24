"""Demo 16 — the UI vocabulary: markup for structure, CSS for states.

The panel used to be built with `el()` calls; it is markup now. What that buys is the
thing the old version could not do: `:hover`, `:focus-visible` and `[data-active]` are
stylesheet rules, so states are declarative instead of inline styles that a class
silently loses to.

Nothing here is a component library: it is divs, a handful of classes, and one palette.
"""

import asyncio

from pyodex import chrome, ext_page, handler, start, status

MENU = ["Search this page", "Reading mode", "Capture tab", "Settings"]
state = {"active": 0, "query": ""}


@start
def main():
    asyncio.ensure_future(counts())


@handler  # main.html: onclick="activate" on each row
def activate(event):
    index = int(event.currentTarget.dataset.index)
    state["active"] = index
    rows = ext_page.get_all(".px-item")
    for position, row in enumerate(rows):
        row.dataset.active = "1" if position == index else "0"
    ext_page.right.text = MENU[index].lower().replace(" ", "-")


@handler  # main.html: oninput="typed"
def typed(_event):
    state["query"] = str(ext_page.search.value)
    ext_page.left.text = f"{len(state['query'])} chars"


@handler  # main.html: onclick="open_item"
def open_item():
    status(f"opened: {MENU[state['active']]}")


@handler  # main.html: onclick="dismiss"
def dismiss():
    status(None)


async def counts() -> None:
    # Its own element: an async init must not overwrite state the user is driving.
    ext_page.tabs.text = f"{await chrome.tabs.count()} tabs · {len(MENU)} items"
