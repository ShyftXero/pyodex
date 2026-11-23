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
"""Demo 10 — network control, in Python.

Two halves that usually need two languages:

* `declarativeNetRequest` — the browser enforces your rules (it never asks your code).
  Rules are JSON, so this is a Python builder, not a bridge problem.
* `webRequest` — observation. Plain events, so it rides the same bridge as demo 07.

Why dNR instead of blocking in code: a declared rule survives your extension being
asleep, and never delays a request through a Python callback.
"""

import asyncio

from pyodex_runtime import Overlay, chrome, fetch_text, on, status

ui = Overlay()
card = ui.card("Demo 10 — network", width=360)
card.body.innerText = "click Run"
seen = {"n": 0, "lines": []}


def render() -> None:
    lines = seen["lines"] + [f"webRequest: {seen['n']} observed"]
    card.body.innerText = "\n".join(lines)


@chrome.on("webRequest.onBeforeRequest")
def request_seen(args):
    seen["n"] += 1
    ui.badge(f"{seen['n']} req", top=16, left=16)
    render()


@on(ui.button("Run", top=16, right=16, id="run-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(run())


async def run() -> None:
    lines = seen["lines"] = ["starting…"]
    render()
    try:
        await chrome.dnr.clear()
        seen["lines"].append("cleared")
        render()
        await chrome.dnr.add_rule(1, "/blocked.js", "block")
        rules = await chrome.dnr.rules()
        seen["lines"].append(f"rules: {[r.get('id') for r in rules]}")
        render()
        try:
            blocked = await chrome.dnr.test("https://example.com/blocked.js")
            lines.append(f"test /blocked.js -> matched={len(blocked.get('matchedRules') or [])}")
            allowed = await chrome.dnr.test("https://example.com/allowed.js")
            lines.append(f"test /allowed.js -> matched={len(allowed.get('matchedRules') or [])}")
        except Exception as e:  # noqa: BLE001  (testMatchOutcome is optional; the rule is not)
            lines.append(f"test: unavailable ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001
        lines.append(f"dnr: {type(e).__name__}: {str(e)[:220]}")

    try:
        tab = await chrome.tabs.current()
        url = str(tab.get("url") or "")
        if url.startswith("http"):
            body = await fetch_text(url + "?pyodex_probe=1")  # a real request webRequest sees
            await asyncio.sleep(0.8)  # let the worker forward the event
            seen["lines"] = lines + [f"fetch: {len(body)} chars"]
        lines.append(f"webRequest: {seen['n']} observed")
    except Exception as e:  # noqa: BLE001
        lines.append(f"webRequest: {type(e).__name__}: {str(e)[:220]}")

    seen["lines"] = lines
    render()
    status(None)
```
