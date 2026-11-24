"""Demo 07 — browser events, in Python.

What it shows: the piece that makes this feel like an extension rather than a script
— Python receiving platform events (`storage.onChanged`, `tabs.onUpdated`) instead of
only calling APIs.

pyodex features: `chrome.on(event, fn)`, `@handler`, `ext_page`. Declaring the
subscription at import time is what lets the build register the listener in the
service worker; MV3 will not wake a worker for listeners added later.

Fire one yourself: change any `chrome.storage` value (or open/close a tab) and watch
the card.
"""

import json

from pyodex import chrome, ext_page, handler, start, status

count = 0


@start
def main():
    status(None)


@chrome.on("storage.onChanged")
def storage_changed(args):
    global count
    count += 1
    detail = json.dumps(args)[:160] if args else "{}"
    ext_page.title.text = f"Demo 07 — {count} event(s)"
    ext_page.body.text = f"storage.onChanged\n{detail}"
    ext_page.badge.text = f"{count} events"


@chrome.on("tabs.onUpdated")
def tab_updated(args):
    ext_page.body.text = "tabs.onUpdated\n" + json.dumps(args)[:160]


@handler  # main.html: onclick="fire"
async def fire():
    # An async handler is awaited by pyodex; no ensure_future needed.
    await chrome.local.set("demo07", str(count) + "-tick")
