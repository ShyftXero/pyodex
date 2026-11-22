"""L13 — the Brython tier (~2 MB, no wasm): Python in the page with a real DOM.

The shim is concatenated ahead of this file by the bootstrap, so its names are
already in scope (Brython has no module loader wired up for us here).
"""

status("brython booted")
set_attr("body", "data-br", "inputs=%d" % count("input"))
button("br", id="br-btn", top=16, left=16)
