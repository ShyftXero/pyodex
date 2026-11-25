"""Invariants that keep build paths from drifting apart.

These exist because the same bug class shipped three times: a manifest naming a file no
path wrote (Chrome rejects the whole extension silently), and a path writing a file it
never registered. Both are structural, so they get structural tests.
"""

import json
import re
from pathlib import Path

import pytest

from pyodex.builder import _referenced_files, build_extension

ROOT = Path(__file__).resolve().parents[1]
DEMOS = sorted((ROOT / "examples" / "demos").iterdir())


def _non_glob(entries):
    return [e for e in entries if not set("*[]") & set(e)]


def test_there_is_exactly_one_manifest_writer():
    """A second writer is how four paths stopped agreeing with each other."""
    source = (ROOT / "src" / "pyodex" / "builder.py").read_text()
    writers = source.count('manifest.json").write_text')
    assert writers == 1, f"{writers} places write manifest.json; keep one (_finish)"


@pytest.mark.parametrize("demo", DEMOS, ids=lambda p: p.name)
def test_every_demo_builds_a_self_consistent_package(tmp_path: Path, demo: Path):
    """Build every shipped demo and check the package against itself.

    - every file the manifest names exists (the silence-Chrome failure mode)
    - every registered resource exists (the reverse drift)
    - no build path leaves a stray manifest reference behind
    """
    project = tmp_path / demo.name
    project.mkdir()
    for item in demo.iterdir():
        if item.is_file():
            (project / item.name).write_bytes(item.read_bytes())

    out = build_extension(project)
    manifest = json.loads((out / "manifest.json").read_text())

    missing = sorted(n for n in _referenced_files(manifest) if not (out / n).exists())
    assert not missing, f"{demo.name}: manifest names files the build did not emit: {missing}"

    war = manifest.get("web_accessible_resources")
    if war:
        registered = _non_glob(war[0]["resources"])
        absent = sorted(n for n in registered if not (out / n).exists())
        assert not absent, f"{demo.name}: registered but absent: {absent}"


def test_each_build_path_calls_the_single_exit():
    """Every path that emits a package must finish through _finish()."""
    source = (ROOT / "src" / "pyodex" / "builder.py").read_text()
    for marker in ("def _mp_build(", "def _tier_build(", "def build_extension("):
        start = source.index(marker)
        # up to the next top-level def, or the end
        match = re.search(r"\ndef [a-z_]+\(|^    def _tier_build\(", source[start + 1:], re.MULTILINE)
        end = start + 1 + match.start() if match else len(source)
        assert re.search(r"_finish\(out, project, cfg, \w*resources\)", source[start:end]), (
            f"{marker} does not finish through _finish()"
        )


def test_content_scripts_are_declared_from_what_was_emitted(tmp_path: Path):
    """The manifest must not decide content scripts from a condition of its own.

    `page_cache = false` used to ship content_scripts for pagebridge.js and pagemain.js
    that were never written, because the manifest keyed off `permissions` instead of the
    emitted files. Keying off `resources` makes that impossible.
    """
    (tmp_path / "pyodex.toml").write_text(
        '[extension]\nname = "NoRelay"\npage_cache = false\nhttp = false\n')
    (tmp_path / "main.py").write_text("from pyodex_runtime import Overlay\n\nOverlay()\n")
    out = build_extension(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    scripts = [c["js"][0] for c in manifest["content_scripts"]]
    assert scripts == ["injector.js"], scripts
