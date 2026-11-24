# The toolbar popup is Python too

A pyodex app is not "Python content script plus hand-written popup HTML". Put `popup.py`
next to `main.py` and the build routes the toolbar popup to it — same interpreter, same
runtime helpers, same `chrome.*` surface. `panel.py` does the same for the side panel,
and the build emits the `side_panel` manifest entry and the `sidePanel` permission.

**pyodex features.** `popup.py` as a first-class entry; the boot script infers the entry
from the page (`/popup.html` → `popup.py`) rather than injecting a flag, because MV3's
CSP forbids inline scripts.

**How it is verified.** Harness L24 — both pages are opened at the extension
origin: the popup must reach "popup rendered by Python" and show live `chrome.tabs` data,
and the side panel must reach "side panel rendered by Python". Mutating the entry
inference to skip `popup.html` makes the popup assertions fail, which is what keeps the
level honest.

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
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
