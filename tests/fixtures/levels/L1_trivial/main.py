"""L1 — plumbing only.

Proves: content script injects, launcher exists, Pyodide boots from the bundled
runtime, main.py runs, the overlay DOM is reachable. Nothing else.
"""

from pyodex import Overlay, document, on, start, status


@start
def main():
    ui = Overlay()
    btn = ui.button("L1 go", top=16, left=16, id="l1-btn")
    ui.panel(id="l1-out").innerText = "idle"

    @on(btn, "click")
    def go(_event):
        document.element("l1-out").innerText = "L1 ready"
        document.body.dataset.l1 = "ready"
        status(None)
