# The toolbar popup is Python too

A pyodex app is not "Python content script plus hand-written popup HTML". Put `popup.py`
next to `main.py` and the build routes the toolbar popup to it — same interpreter, same
runtime helpers, same `chrome.*` surface.

**pyodex features.** `popup.py` as a first-class entry; the boot script infers the entry
from the page (`/popup.html` → `popup.py`) rather than injecting a flag, because MV3's
CSP forbids inline scripts.

**How it is verified.** Harness L24 — the popup page is opened at the extension origin,
and it must reach "popup rendered by Python" and show live data from `chrome.tabs`
(a tab count and the current title), which proves the interpreter actually ran there.

```toml
[extension]
name = "Demo 11 Popup"
version = ""
description = "The toolbar popup is Python too: popup.py runs in the same interpreter."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# main.py
"""Demo 11 — the popup is Python as well.

A pyodex app is not "a content script in Python plus hand-written popup HTML". Drop a
`popup.py` next to `main.py` and the build routes the toolbar popup to it: same
interpreter, same runtime helpers, same chrome.* surface.
"""

from pyodex_runtime import Overlay, chrome

ui = Overlay()
card = ui.card("Demo 11 — click the toolbar icon")
card.body.innerText = "the popup is Python (popup.py)"
```

```python
# popup.py
"""popup.py — runs when the toolbar icon is clicked.

No build step, no bundling, no second language: this file is the popup.
"""

import asyncio

from js import document

from pyodex_runtime import chrome


async def render() -> None:
    out = document.getElementById("app")
    try:
        tabs = await chrome.tabs.count()
        current = await chrome.tabs.current()
        title = str(current.get("title") or "(no title)")[:40]
        out.innerHTML = (
            "<div style='border-bottom:1px solid #27272a;padding:4px 0'>"
            f"<span>Tabs</span> <b>{tabs}</b></div>"
            "<div style='border-bottom:1px solid #27272a;padding:4px 0'>"
            f"<span>Current</span> <b>{title}</b></div>"
            "<div style='padding:4px 0'><span>Python</span> <b>running</b></div>"
        )
        document.getElementById("status").innerText = "popup rendered by Python"
    except Exception as e:  # noqa: BLE001
        document.getElementById("status").innerText = f"error: {type(e).__name__}: {str(e)[:60]}"


asyncio.ensure_future(render())
```
