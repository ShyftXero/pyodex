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
