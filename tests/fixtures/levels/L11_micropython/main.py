"""L11 — the MicroPython tier: ~2 MB, present in every tab, real DOM access.

No packages, no async event loop, subset of the language — that is the trade for
being small enough to sit in 40 tabs.
"""

import pyodex_mp as ui

ui.status("mp booted")
n = ui.count("input")
ui.button("mp", id="mp-btn", top=16, left=16)
doc = ui.document
doc.body.setAttribute("data-mp", f"inputs={n}")
