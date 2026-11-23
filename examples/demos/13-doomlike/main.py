"""Demo 13 — wasm painting a canvas inside someone else's page.

Two honest halves:

* the module is real: `color.wasm` computes packed RGBA, and the page loops pixels
  through it. Swap it for a game blob and the plumbing is the same shape.
* the cheating part: we borrow the page's canvas. The page's own world instantiates the
  module, so this works only where the page's CSP allows wasm, it inherits the page's
  paint cost, and it is not our window. A non-cheating build owns its surface (an
  extension window with a canvas and an SDL frame pump).

We are calling that out rather than shipping a screenshot and letting you assume
otherwise.
"""

import asyncio

from pyodex_runtime import Overlay, Page, on, runtime_url, status

page = Page()
ui = Overlay()
card = ui.card("Demo 13 — wasm in the page", width=380)
card.body.innerText = "click Run"

STAGE = """
<div id="pyodex-stage" style="position:fixed;right:16px;bottom:16px;z-index:2147483646;
     background:#0b0b0d;padding:8px;border:2px solid #f43f5e;border-radius:8px">
  <canvas id="pyodex-canvas" width="160" height="120"
          style="image-rendering:pixelated;display:block"></canvas>
  <script src="__RUNNER__"></script>
</div>
"""


@on(ui.button("Run", top=16, right=16, id="run-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(run())


async def run() -> None:
    await page.append_html("body", STAGE.replace("__RUNNER__", runtime_url("wasmcanvas.js")))

    frames = "0"
    for _ in range(80):  # the page paints a few frames; give it real time
        value = await page.attr("#pyodex-canvas", "data-frames")
        frames = (value[0] if value else None) or "0"
        if frames not in ("0", "None", ""):
            break
        await asyncio.sleep(0.25)

    checksum_values = await page.attr("#pyodex-canvas", "data-checksum")
    error_values = await page.attr("#pyodex-canvas", "data-error")
    checksum = (checksum_values[0] if checksum_values else "") or "0"
    error = (error_values[0] if error_values else "") or ""

    card.head.innerText = f"Demo 13 — {frames} frame(s) from wasm"
    card.body.innerText = "\n".join([
        f"canvas: 160×120, painted by color.wasm",
        f"checksum: {checksum}",
        f"error: {error or 'none'}",
        "",
        "borrowed the page's canvas — that is the cheat",
    ])
    status(None)
