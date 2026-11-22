"""Demo 05 — always-on tab watcher on the shared interpreter.

What it shows: the flagship mode. One Pyodide lives in a SharedWorker for the whole
browser; tabs hold a tiny agent and no interpreter. Each tab that opens is reported
to the same engine, so the count is shared state — and adds ~1 MB, not ~55 MB.

pyodex features: engine = "shared" + ui = "engine", @on_attach, per-tab widgets
(Overlay.badge paints into the tab that just attached), and shared Python state
across tabs.
"""

from pyodex_runtime import Overlay, Page, on_attach, status

ui = Overlay()

# One interpreter, so this list is genuinely shared across every tab.
seen: list[str] = []


def host_of(url: str) -> str:
    return url.split("://")[-1].split("/")[0] or "page"


@on_attach
async def attached(page: Page, url: str) -> None:
    site = host_of(url)
    if url not in seen:
        seen.append(url)

    ui.badge(f"{len(seen)} tab(s) seen", top=16, right=16)
    card = ui.card("Demo 05 — tab watcher")
    card.body.innerText = (
        f"This tab: {site}\n"
        f"Tabs seen by the engine: {len(seen)}\n\n"
        "One interpreter serves every tab — this counter is shared state."
    )
    status(None)
