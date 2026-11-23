"""Demo 11 — the popup is Python as well.

A pyodex app is not "a content script in Python plus hand-written popup HTML". Drop a
`popup.py` next to `main.py` and the build routes the toolbar popup to it: same
interpreter, same runtime helpers, same chrome.* surface.
"""

from pyodex_runtime import Overlay, chrome

ui = Overlay()
card = ui.card("Demo 11 — click the toolbar icon")
card.body.innerText = "the popup is Python (popup.py)"
