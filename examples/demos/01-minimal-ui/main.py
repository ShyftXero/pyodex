"""Demo 01 — minimal UI.

What it shows: the whole loop an extension needs — declare markup, handle an
event, update what the user sees.
pyodex features: main.html, ext_page (strict element access), @handler.
Nothing here touches the host page: it is a self-contained app page.
"""

from pyodex import ext_page, handler, start, status

clicks = 0


@start  # called once, after the page and Python are ready
def main():
    status(None)  # hide the boot chip


@handler  # main.html: onclick="clicked"
def clicked():
    global clicks
    clicks += 1
    ext_page.badge.text = f"{clicks} click{'s' if clicks != 1 else ''}"
    ext_page.body.text = f"You clicked {clicks} time(s).\nStill Python, still in this tab."
