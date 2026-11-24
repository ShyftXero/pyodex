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
# python_files = ["helpers.py"]   # extra Python files / packages copied to app/
# assets = ["icon.png"]
'''

MAIN_PY = '''"""Behavior only: main.html says what runs, this says what it does."""

from pyodex import ext_page, handler, start, status


@start
def main():
    status(None)  # hide the boot chip


@handler  # main.html: onclick="clicked"
def clicked():
    ext_page.greeting.text = "Clicked!"
'''

OVERLAY_CSS = """/* Layout and appearance. The overlay body is click-through; the app's own
   top-level blocks take the pointer, so the page underneath keeps working. */
.card { position: fixed; top: 16px; left: 16px; padding: 12px 16px; border-radius: 10px;
  background: #18181b; color: #f5f7fa; border: 2px solid #f43f5e;
  font: 14px/1.45 system-ui, sans-serif; }
#go { position: fixed; top: 16px; right: 16px; padding: 10px 16px; border-radius: 8px; }
"""

OVERLAY_BODY = """<!-- main.html: the page names the handler; the build checks the name exists. -->
<div class="card">
  <strong>Hello from Python</strong>
  <p id="greeting">Nothing clicked yet.</p>
</div>

<button id="go" onclick="clicked">Click me</button>
"""


DEMOS = Path(__file__).parent / "demos"


def available_demos() -> list[str]:
    if not DEMOS.exists():
        return []
    return sorted(d.name for d in DEMOS.iterdir() if d.is_dir())


def init_demo(target: Path, demo: str) -> None:
    """Scaffold from a bundled demo (examples/demos, shipped inside the package)."""
    import shutil

    names = available_demos()
    match = next((n for n in names if n == demo or n.startswith(demo + "-")), None)
    if match is None:
        raise SystemExit(f"unknown demo {demo!r}; available: {names}")
    target.mkdir(parents=True, exist_ok=True)
    for item in (DEMOS / match).iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)


def init_project(target: Path, name: str, matches: list[str] | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    matches = matches or ["<all_urls>"]
    m = "[" + ", ".join(f'"{x}"' for x in matches) + "]"
    files = {
        "pyodex.toml": PYEXT_TOML.format(name=name, matches=m),
        "main.py": MAIN_PY,
        "overlay.css": OVERLAY_CSS,
        "main.html": OVERLAY_BODY,
    }
    for fn, content in files.items():
        p = target / fn
        if not p.exists():
            p.write_text(content)
