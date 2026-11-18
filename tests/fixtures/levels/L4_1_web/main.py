"""L4.1 — production web behaviour.

Covers: URL gate (@urlmatch), trigger boot (@watch_for), two tabs filled
independently, keep_alive, page events, and traceback surfacing.
"""

import asyncio

from pyodex_runtime import Page, events, keep_alive, on_event, status, urlmatch, watch_for

page: Page | None = None


@urlmatch(["http://*", "https://*"])
@watch_for("input[type=password]")
def armed():
    asyncio.ensure_future(setup())


@on_event(events.SUBMIT, selector="form")
def submitted(_payload):
    status("L4.1 submit observed")


async def setup():
    global page
    page = Page()
    keep_alive(120)
    info = await page.info()

    if "boom" in info["url"]:
        raise RuntimeError("deliberate failure for the traceback test")

    await page.set_value("input[name=user]", "l41@pytest")
    await page.set_value("input[type=password]", "l41-secret")
    await page.set_text("#l3-report", "l41 filled")
    status("L4.1 ready")
