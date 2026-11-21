"""L9 — tabs hold no interpreter at all.

Python runs once, in the shared engine. Each tab that attaches gets a Page bound
to it, and the DOM work is executed back in that tab by the content script.
"""

from pyodex_runtime import Overlay, Page, on_attach, status

ui = Overlay()
ui.button("L9", id="l9-btn", top=16, left=16)


@on_attach
async def attached(page: Page, url: str) -> None:
    if "login" in url:
        await page.set_value("input[name=user]", "l9@pytest")
        await page.set_value("input[type=password]", "l9-secret")
        await page.set_text("#l3-report", "l9 filled")
