"""Demo 11 — the popup is Python as well.

A pyodex app is not "a content script in Python plus hand-written popup HTML". Drop a
`popup.py` next to `main.py` and the build routes the toolbar popup to it: same
interpreter, same runtime helpers, same chrome.* surface.

The popup and the side panel predate the markup surface: they own dedicated templates
(`popup_py.html`), so their Python still writes `innerHTML`. That is the escape hatch,
and it is honest to leave it visible here.
"""

from pyodex import start, status


@start
def main():
    status(None)
