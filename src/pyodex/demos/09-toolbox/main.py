"""Demo 09 — the permission-gated toolbox.

Cookies, screenshots, downloads, bookmarks, history: narrow APIs that are boring on
their own and useful together (export what you are looking at, remember where you were).

The `cookies`, `downloads`, `bookmarks` and `history` permissions are added by the build
because this file mentions them. Nothing in a manifest was edited by hand.
"""

import asyncio

from pyodex_runtime import Overlay, chrome, on, status

ui = Overlay()
card = ui.card("Demo 09 — toolbox", width=360)
card.body.innerText = "click Run"
lines: list[str] = []


@on(ui.button("Run", top=16, right=16, id="run-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(run())


async def run() -> None:
    lines.clear()
    url = await js_url()

    try:
        await chrome.cookies.set(url, "pyodex_demo", "hello")
        cookie = await chrome.cookies.get(url, "pyodex_demo")
        lines.append(f"cookies: {cookie and cookie.get('value')}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"cookies: {type(e).__name__}: {str(e)[:60]}")

    try:
        shot = await chrome.tabs.capture_visible_tab()
        lines.append(f"capture: {len(shot)} chars")
    except Exception as e:  # noqa: BLE001
        lines.append(f"capture: {type(e).__name__}: {str(e)[:60]}")

    try:
        download_id = await chrome.downloads.download(
            "data:text/plain;base64,cHlvZGV4IHRvb2xib3g=", filename="pyodex-demo.txt")
        found = await chrome.downloads.search({"id": download_id})
        lines.append(f"downloads: id={download_id} found={len(found)}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"downloads: {type(e).__name__}: {str(e)[:60]}")

    try:
        marks = await chrome.bookmarks.search("")
        visited = await chrome.history.search("", limit=5)
        lines.append(f"bookmarks: {len(marks)}  history: {len(visited)}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"bookmarks/history: {type(e).__name__}: {str(e)[:60]}")

    try:
        await chrome.identity.get_auth_token()
        lines.append("identity: token")
    except Exception as e:  # noqa: BLE001
        # Unconfigured OAuth must say so in words a developer can act on.
        lines.append(f"identity: {str(e)[:90]}")

    card.body.innerText = "\n".join(lines)
    status(None)


async def js_url() -> str:
    tab = await chrome.tabs.current()
    return str(tab.get("url") or "https://example.com")
