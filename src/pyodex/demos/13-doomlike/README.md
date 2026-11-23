# wasm paints a canvas in someone else's page

Two honest halves: the module is real (`color.wasm` computes packed RGBA and the page
loops pixels through it — swap in a game blob and the plumbing is the same shape), and the
**cheat is that we borrow the page's canvas**.

Where this demo cheats, explicitly:

* the page's own world instantiates the module, so it only works where the page's CSP
  permits wasm — a strict `script-src` blocks it and there is nothing to do about that
* it inherits the page's paint cost and the page's DevTools, and it is not our window
* no audio device, no input focus, no main loop we control (the JS client glue supplies
  those, which is what a real Doom build ships)

A non-cheating version owns its surface: an extension window with a canvas, the emscripten
SDL glue, and a frame pump we drive from Python with `Wasm` calls. That is the honest
description of the remaining work.

**How it is verified.** Harness L26 — the canvas must have non-black pixels that came from wasm, and the report must state the cheat.

```toml
[extension]
name = "Demo 13 Doomlike"
version = ""
description = "wasm paints a canvas in the host page. Cheating: it borrows the page's canvas (see README)."
matches = ["<all_urls>"]
boot = "user"
http = false
assets = ["wasmcanvas.js", "color.wasm"]
```

```python
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
```

```javascript
// wasmcanvas.js — runs in the page's world
// Runs in the PAGE's world, not the extension's — that is the cheat (see README).
// The page instantiates the module itself, so this only works where the page's CSP
// permits wasm and the extension exposes the files as web-accessible resources.
(async () => {
  const me = document.currentScript;
  const base = me.src.replace(/[^/]*$/, "");
  const canvas = document.getElementById("pyodex-canvas");
  const ctx = canvas.getContext("2d");
  const status = { frames: 0, checksum: 0, error: "" };

  try {
    const response = await fetch(base + "color.wasm");
    const bytes = await response.arrayBuffer();
    const { instance } = await WebAssembly.instantiate(bytes, {});
    const color = instance.exports.colorL === undefined ? instance.exports.color : instance.exports.color;
    const { width: w, height: h } = canvas;
    const img = ctx.createImageData(w, h);
    const data = new Uint32Array(img.data.buffer);
    let sum = 0;

    for (let frame = 0; frame < 3; frame++) {
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const packed = color(x + frame * 8, y) >>> 0;
          data[y * w + x] = packed;
          sum = (sum + packed) >>> 0;
        }
      }
      ctx.putImageData(img, 0, 0);
      await new Promise((r) => requestAnimationFrame(r));
      status.frames++;
    }
    status.checksum = sum;
  } catch (e) {
    status.error = String(e && e.message ? e.message : e).slice(0, 120);
  }

  canvas.dataset.frames = String(status.frames);
  canvas.dataset.checksum = String(status.checksum);
  canvas.dataset.error = status.error;
})();
```
