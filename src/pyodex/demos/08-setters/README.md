# Setters: alarms, context menus, notifications

The other half of the event bridge: Python creates the things that generate events.

**pyodex features.** `chrome.alarms`, `chrome.contextMenus`, `chrome.notifications`; permissions derived from code; `main.html` + `@handler` for the UI.

**How it is verified.** Harness L21 — the alarm is registered, `alarms.onAlarm` is asserted to reach Python, the context menu round-trips, and the notification result is reported honestly either way.

```toml
[extension]
name = "Demo 08 Setters"
version = ""
description = "Schedules alarms, adds a context menu and shows a notification — then reacts to them in Python."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
