"""Demo 01 — minimal UI.

What it shows: the whole loop an extension needs — render, react, count.
pyodex features: Overlay.card / badge / button, @on, and a dict-free event handler.
Nothing here touches the host page: it is a self-contained overlay app.
"""

from pyodex_runtime import Overlay, on, status

ui = Overlay()
card = ui.card("Demo 01 — minimal UI")
badge = ui.badge("0 clicks")
btn = ui.button("Click me", top=16, right=16, id="demo-btn")

card.body.innerText = "Python is running in this tab.\nClick the button."

clicks = 0


@on(btn, "click")
def clicked(_event):
    global clicks
    clicks += 1
    badge.innerText = f"{clicks} click{'s' if clicks != 1 else ''}"
    card.body.innerText = f"You clicked {clicks} time(s).\nStill Python, still in this tab."
    status(None)
