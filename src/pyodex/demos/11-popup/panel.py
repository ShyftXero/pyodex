"""panel.py — the side panel, also Python.

Same contract as popup.py: presence of the file is the wiring.
"""

import asyncio

from js import document

from pyodex_runtime import chrome


async def render() -> None:
    current = await chrome.tabs.current()
    title = str(current.get("title") or "(no title)")[:60]
    document.getElementById("app").innerHTML = (
        "<div style='border-bottom:1px solid #27272a;padding:4px 0'>Side panel</div>"
        f"<div style='padding:4px 0'>{title}</div>"
    )
    document.getElementById("status").innerText = "side panel rendered by Python"


asyncio.ensure_future(render())
