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
