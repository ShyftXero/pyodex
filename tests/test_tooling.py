"""`pyodex explain` and `pyodex dev`: the build's receipt and its edit loop."""

import json
from pathlib import Path

from pyodex.dev import rebuild, snapshot, sync, watch
from pyodex.explain import render, report
from pyodex.scaffold import init_project


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "app"
    init_project(project, "Tooling Demo", None)
    return project


def test_explain_describes_the_scaffold(tmp_path):
    data = report(_project(tmp_path))
    assert data["handlers"] == ["clicked"]
    assert data["page"]["bindings"] == [
        {"event": "click", "modifiers": [], "handler": "clicked", "line": 7}
    ]
    assert data["page"]["ids"] == ["go", "greeting"]
    assert data["boot"] == "user"
    assert data["runtime"] == {"tier": "pyodide", "default": True}
    assert data["problems"] == []


def test_explain_reports_a_typo_with_a_suggestion(tmp_path):
    project = _project(tmp_path)
    (project / "main.html").write_text('<button id="go" onclick="cliked">Go</button>\n')
    data = report(project)
    assert data["problems"], "a typo must be reported, not ignored"
    assert 'Did you mean "clicked"' in data["problems"][0]


def test_explain_render_is_readable(tmp_path):
    text = render(report(_project(tmp_path)))
    assert "pyodex-click" in text
    assert "-> clicked" in text
    assert "main.html:7" in text


def test_explain_json_is_parseable(tmp_path):
    data = report(_project(tmp_path))
    assert json.loads(json.dumps(data))["handlers"] == ["clicked"]


def test_explain_lists_page_bindings(tmp_path):
    project = _project(tmp_path)
    (project / "main.py").write_text(
        'from pyodex import on_page\n\n\n@on_page("click", role="button", name="Buy now")\n'
        "def buy(payload):\n    pass\n"
    )
    (project / "main.html").write_text("<div id='x'></div>\n")
    data = report(project)
    assert data["page_bindings"] and data["page_bindings"][0]["role"] == "button"
    assert "Buy now" in render(data)


def test_dev_rebuild_writes_in_place(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "dist"
    rebuild(project, out)
    inode = out.stat().st_ino
    assert (out / "manifest.json").is_file()

    (out / "stale.txt").write_text("left over")
    (project / "main.html").write_text('<div class="card"><p id="greeting">changed</p></div>\n')
    rebuild(project, out)

    assert out.stat().st_ino == inode, "the output directory must not be recreated"
    assert not (out / "stale.txt").exists(), "files this build does not produce are removed"
    assert "changed" in (out / "overlay.html").read_text()


def test_dev_sync_keeps_the_directory(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "a").mkdir(parents=True)
    (source / "a" / "one.txt").write_text("1")
    target.mkdir()
    inode = target.stat().st_ino

    sync(source, target)

    assert target.stat().st_ino == inode
    assert (target / "a" / "one.txt").read_text() == "1"


def test_dev_once_builds_and_reports(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "dist-once"
    assert watch(project, out, once=True) == 0
    assert (out / "manifest.json").is_file()


def test_dev_snapshot_sees_edits(tmp_path):
    project = _project(tmp_path)
    before = snapshot(project)
    (project / "main.html").write_text("<div id='greeting'></div>\n")
    assert snapshot(project) != before
