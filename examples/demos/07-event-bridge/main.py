"""Demo 07 — browser events, in Python.

What it shows: the piece that makes this feel like an extension rather than a script
— Python receiving platform events (`storage.onChanged`, `tabs.onUpdated`) instead of
only calling APIs.

pyodex features: `chrome.on(event, fn)`. Declaring the subscription at import time is
what lets the build register the listener in the service worker; MV3 will not wake a
worker for listeners added later.

Fire one yourself: change any `chrome.storage` value (or open/close a tab) and watch
the card.
"""

import asyncio
import json

from pyodex_runtime import Overlay, chrome, on, status

ui = Overlay()
card = ui.card("Demo 07 — event bridge")
card.body.innerText = "waiting for events…"
count = {"n": 0}
btn = ui.button("Fire a storage event", top=16, right=16, id="fire-btn")


@chrome.on("storage.onChanged")
def storage_changed(args):
    count["n"] += 1
    detail = json.dumps(args)[:160] if args else "{}"
    card.head.innerText = f"Demo 07 — {count['n']} event(s)"
    card.body.innerText = f"storage.onChanged\n{detail}"
    ui.badge(f"{count['n']} events", top=16, left=16)
    status(None)


@chrome.on("tabs.onUpdated")
def tab_updated(args):
    card.body.innerText = "tabs.onUpdated\n" + json.dumps(args)[:160]


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(trigger())


async def trigger() -> None:
    await chrome.local.set("demo07", str(count["n"]) + "-tick")
