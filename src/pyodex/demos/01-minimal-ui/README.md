# Minimal UI

**What it demonstrates.** The smallest useful pyodex app: render a card, count clicks, react to them. No host-page access at all — this is the overlay alone.

**pyodex features.** `main.html`, `ext_page` (strict element access, attribute form), `@handler`, and the `onclick` sugar rewritten at build time

**How it is verified.** Harness level L14 — builds, boots, paints, and the click handler updates the badge.

```toml
[extension]
name = "Demo 01 Minimal UI"
version = ""
description = "The smallest useful pyodex app: a card, a badge, a button, a click handler."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```html
<!-- main.html: the page says what it does; the value is a handler name -->
<article class="card">
  <h1>Demo 01 — minimal UI</h1>
  <p id="badge" class="badge">0 clicks</p>
  <p id="body">Python is running in this tab.<br>Click the button.</p>
</article>

<button id="demo-btn" onclick="clicked">Click me</button>
```

```python
"""main.py — behavior only; no layout and no element ids to repeat."""

from pyodex import ext_page, handler, start, status

clicks = 0


@start
def main():
    status(None)


@handler  # main.html: onclick="clicked"
def clicked():
    global clicks
    clicks += 1
    ext_page.badge.text = f"{clicks} click{'s' if clicks != 1 else ''}"
    ext_page.body.text = f"You clicked {clicks} time(s).\nStill Python, still in this tab."
```

`ext_page.badge` is the element with `id="badge"`; `-` becomes `_` in the attribute
form and `ext_page["class"]` is the escape for anything that mangles. Layout and
appearance live in `overlay.css`, not in the Python.

Build and load it:

```
uv run pyodex build examples/demos/01-minimal-ui
# chrome://extensions -> Developer mode -> Load unpacked -> dist/<slug>/
```
