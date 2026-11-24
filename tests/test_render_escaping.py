"""Author config values must not break out of generated JS/HTML.

`builder._render` substitutes config into generated code. `cfg.name` reaches the
`{{NAME}}` placeholder, which only ever lands in HTML text (page titles, the popup
heading). A name containing `</script>` or quotes used to pass through raw, so it
could close a tag or inject markup into the generated package. These tests pin the
escaping so that regression is loud.
"""

import json
from pathlib import Path

from pyodex.builder import build_extension

# Closes a script tag, opens a new one, and carries quotes and an ampersand so both
# HTML-text and quote escaping are exercised at once.
HOSTILE = 'Evil</script><script>alert(1)</script> "q" & x'
INJECTED = "<script>alert(1)</script>"
ESCAPED = "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;"


def _toml(name: str, extra: str = "") -> str:
    # TOML basic string: the hostile name has no backslash/newline, only a double
    # quote, so a single escape of `"` is enough.
    safe = name.replace('"', '\\"')
    return f'[extension]\nname = "{safe}"\nhttp = false\n{extra}'


def test_hostile_name_does_not_inject_into_generated_html(tmp_path: Path):
    (tmp_path / "pyodex.toml").write_text(_toml(HOSTILE))
    (tmp_path / "main.py").write_text(
        "from pyodex_runtime import Overlay\n\nOverlay()\n")

    out = build_extension(tmp_path)

    offenders = []
    for page in out.glob("*.html"):
        text = page.read_text()
        if INJECTED in text:
            offenders.append(page.name)
    assert not offenders, f"raw name injected script into: {offenders}"

    overlay = (out / "overlay.html").read_text()
    assert ESCAPED in overlay, "name was not HTML-escaped in overlay.html"
    assert HOSTILE not in overlay, "raw unescaped name present in overlay.html"


def test_hostile_name_stays_a_json_string_in_the_manifest(tmp_path: Path):
    (tmp_path / "pyodex.toml").write_text(_toml(HOSTILE))
    (tmp_path / "main.py").write_text(
        "from pyodex_runtime import Overlay\n\nOverlay()\n")

    out = build_extension(tmp_path)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["name"] == HOSTILE  # json.dumps carried it verbatim, no break-out


def test_hostile_name_in_python_popup_is_escaped(tmp_path: Path):
    (tmp_path / "pyodex.toml").write_text(_toml(HOSTILE))
    (tmp_path / "main.py").write_text(
        "from pyodex_runtime import Overlay\n\nOverlay()\n")
    (tmp_path / "popup.py").write_text(
        "from pyodex_runtime import Overlay\n\nOverlay()\n")

    out = build_extension(tmp_path)

    popup = out / "popup.html"
    if popup.exists():
        text = popup.read_text()
        assert INJECTED not in text, "raw name injected script into popup.html"
        assert ESCAPED in text, "name was not HTML-escaped in popup.html"
