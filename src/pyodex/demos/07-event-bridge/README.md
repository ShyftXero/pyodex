# Browser events, in Python

**What it demonstrates.** Python receiving platform events rather than only calling
APIs: `storage.onChanged` and `tabs.onUpdated` update a live card. This is the piece
that makes an extension feel like an extension instead of a script that runs once.

**No boilerplate.** The decorator *is* the declaration:

```python
@chrome.on("storage.onChanged")
def storage_changed(args): ...
```

The build scans for `chrome.on("ns.event", ...)` and renders the list into the service
worker, because MV3 only delivers events to listeners registered at top level — a
worker will not wake for a listener added later. One line, both jobs.

**pyodex features.** `chrome.on()`, `chrome.local`, `main.html` + `ext_page` + `@handler`.

**How it is verified.** Harness level L20 — the card waits for events, a click fires
a real `chrome.storage` write, and the Python handler is asserted to have run and
counted twice.

```toml
[extension]
name = "Demo 07 Event Bridge"
version = ""
description = "Listens to browser events from Python: chrome.on(...) with a live event log."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```

**The new format.** The card, badge and button live in `main.html`, and the click is
bound by the markup (`onclick="fire"` → `pyodex-click` at build time). Python holds
behavior and state only:

```python
@handler  # main.html: onclick="fire"
async def fire():
    await chrome.local.set("demo07", str(count) + "-tick")
```

An `async def` handler is awaited by pyodex, so no `ensure_future` is needed.
