"""Scaffold a new pyodex project directory."""

from __future__ import annotations

from pathlib import Path

PYEXT_TOML = '''[extension]
name = "{name}"
version = "0.1.0"
description = "A Python-powered Chrome extension."
matches = {matches}
entry = "main.py"
# url_filter = "example"          # optional: only run when page URL contains this
# scripts = ["vendor.js"]         # classic JS loaded in overlay before Python
# python_files = ["helpers.py"]   # extra Python files / packages copied to app/
# assets = ["icon.png"]

[vendor]
# filename = "https://cdn.example.com/lib.umd.js"
'''

MAIN_PY = '''"""Entry point. Runs inside Pyodide in the extension overlay iframe."""

from pyodex_runtime import Overlay, on

ui = Overlay()
btn = ui.button("Hello from Python", top=16, left=16)


@on(btn, "click")
def clicked(event):
    btn.innerText = "Clicked!"
'''

OVERLAY_CSS = """/* Styles for the overlay page. Overlay body is click-through unless an element sets pointer-events: auto. */
"""

OVERLAY_BODY = """<!-- Markup injected into the overlay <body>. Optional. -->
"""


def init_project(target: Path, name: str, matches: list[str] | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    matches = matches or ["<all_urls>"]
    m = "[" + ", ".join(f'"{x}"' for x in matches) + "]"
    files = {
        "pyodex.toml": PYEXT_TOML.format(name=name, matches=m),
        "main.py": MAIN_PY,
        "overlay.css": OVERLAY_CSS,
        "overlay.body.html": OVERLAY_BODY,
    }
    for fn, content in files.items():
        p = target / fn
        if not p.exists():
            p.write_text(content)
