# Network: declarativeNetRequest + webRequest

Rules the browser enforces itself, and observation that rides the event bridge.

**pyodex features.** `chrome.dnr.add_rule/clear/test`, `chrome.on("webRequest.onBeforeRequest")`, automatic `declarativeNetRequest` / `webRequest` permissions.

**How it is verified.** Harness L23 — the rule is installed, then a **live** page fetch of the blocked URL must fail while another URL still works, and a webRequest event must arrive.

```toml
[extension]
name = "Demo 10 Network"
version = ""
description = "Block requests with declarativeNetRequest and observe traffic with webRequest."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
