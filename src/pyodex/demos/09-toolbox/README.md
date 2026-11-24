# Toolbox: cookies, screenshots, downloads, bookmarks

Narrow, permission-gated APIs that are useful together.

**pyodex features.** `chrome.cookies`, `chrome.tabs.capture_visible_tab`, `chrome.downloads`, `chrome.bookmarks`, `chrome.history`.

**How it is verified.** Harness L22 — a cookie round-trips, a screenshot comes back as a ~17 KB data URL, and a download is created then found by `search()`.

```toml
[extension]
name = "Demo 09 Toolbox"
version = ""
description = "Cookies, screenshots, downloads, bookmarks and history from Python."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
