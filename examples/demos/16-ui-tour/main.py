"""Demo 16 — the UI vocabulary, laid out in Python and styled in CSS.

Layout is Python: `el()` takes snake_case style kwargs, `panel()` mounts a container, and
widgets are ordinary objects you can append to. *States* (hover, focus, active) cannot be
inline styles, so they live in `overlay.css` — which the build inlines into the overlay.

Nothing here is a component library: it is divs, a handful of classes, and one palette.
"""

from pyodex_runtime import Overlay, chrome, el, on, status

ui = Overlay()
state = {"active": 0, "query": ""}

shell = ui.panel(cls="px-shell", position="fixed", top="16px", left="16px", pointer_events="auto")

head = el("div", cls="px-head")
head.appendChild(el("span", cls="px-dot"))
head.appendChild(el("span", cls="px-title", text="pyodex"))
head.appendChild(el("span", cls="px-pill", text="ui tour"))
shell.appendChild(head)

MENU = [
    ("Search this page", "⌘K"),
    ("Reading mode", "⌘R"),
    ("Capture tab", "⌘⇧4"),
    ("Settings", "⌘,"),
]

menu = el("div", cls="px-menu")
items = []
for index, (label, key) in enumerate(MENU):
    row = el("div", cls="px-item", tabindex="0")
    row.dataset.active = "1" if index == 0 else "0"
    row.appendChild(el("span", text=label))
    row.appendChild(el("span", cls="px-k", text=key))
    menu.appendChild(row)
    items.append((row, label))
shell.appendChild(menu)

body = el("div", cls="px-body")
search = el("input", cls="px-input", placeholder="Filter…", type="search")
body.appendChild(search)

row = el("div", cls="px-row")
primary = el("button", cls="px-btn px-btn--primary", text="Open")
secondary = el("button", cls="px-btn", text="Dismiss")
row.appendChild(primary)
row.appendChild(secondary)
body.appendChild(row)
shell.appendChild(body)

foot = el("div", cls="px-foot")
left = el("span", text="0 tabs seen")
right = el("span", text="python · dom")
foot.appendChild(left)
foot.appendChild(right)
shell.appendChild(foot)


def activate(index: int) -> None:
    state["active"] = index
    for i, (row_, label) in enumerate(items):
        row_.dataset.active = "1" if i == index else "0"
    right.innerText = items[index][1].lower().replace(" ", "-")


for i, (row_, _label) in enumerate(items):
    on(row_, "click")(lambda _e, i=i: activate(i))


@on(search, "input")
def _typed(event):
    state["query"] = str(search.value)
    left.innerText = f"{len(state['query'])} chars"


@on(primary, "click")
def _open(_e):
    status(f"opened: {items[state['active']][1]}")


@on(secondary, "click")
def _dismiss(_e):
    status(None)


async def counts() -> None:
    left.innerText = f"{await chrome.tabs.count()} tabs · {len(MENU)} items"


import asyncio  # noqa: E402  (after the UI is built, so a slow tab query cannot delay paint)

asyncio.ensure_future(counts())
