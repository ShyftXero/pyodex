"""L1 — plumbing only.

Proves: content script injects, launcher exists, Pyodide boots from the bundled
runtime, main.py runs, the overlay DOM is reachable. Nothing else.
"""

from pyodex_runtime import Overlay, document, on, status

ui = Overlay()
btn = ui.button("L1 go", top=16, left=16, id="l1-btn")
out = ui.panel(id="l1-out")
out.innerText = "idle"


@on(btn, "click")
def go(_event):
    out.innerText = "L1 ready"
    document.body.dataset.l1 = "ready"
    status(None)
