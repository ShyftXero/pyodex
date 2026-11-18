# /// script
# dependencies = ["numpy"]
# ///
"""L2 — the quickstart: dependencies, compute, HTTP, UI, async."""

import asyncio

import numpy as np

from pyodex_runtime import Overlay, Page, fetch_json, on, status

ui = Overlay()
page = Page()
btn = ui.button("L2 compute", top=16, left=16, id="l2-btn")
out = ui.panel(id="l2-out")
out.innerText = "idle"


async def run(_event=None):
    arr = np.random.rand(1000)
    mean = float(arr.mean())
    info = await page.info()
    base = info["url"].split("?")[0].rsplit("/", 1)[0]
    try:
        data = await fetch_json(base + "/json")
        http = "ok" if data.get("ok") else "bad"
    except Exception as e:  # noqa: BLE001
        http = f"err:{type(e).__name__}"
    out.innerText = f"mean={mean:.4f}\nhttp={http}\nnumpy={np.__version__}"
    status(None)


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(run())


async def auto():
    info = await page.info()
    if "autorun" in info["url"]:
        await run()


asyncio.ensure_future(auto())
