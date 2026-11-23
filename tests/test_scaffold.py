from pathlib import Path

import pytest

def test_bundled_demos_match_examples():
    """The shipped demos must not drift from the ones the harness exercises.

    They did: panel.py existed in examples/demos/11-popup but the bundled copy was made
    before the file was created, so `pyodex init --demo 11` silently scaffolded a
    project with no side panel.
    """
    repo = Path(__file__).resolve().parents[1]
    examples = repo / "examples" / "demos"
    bundled = repo / "src" / "pyodex" / "demos"
    if not examples.is_dir() or not bundled.is_dir():
        pytest.skip("demos not present in this checkout")

    drift = {}
    for demo in sorted(p.name for p in examples.iterdir() if p.is_dir()):
        want = {p.name for p in (examples / demo).iterdir() if p.is_file()}
        have = {p.name for p in (bundled / demo).iterdir() if p.is_file()} if (bundled / demo).is_dir() else set()
        if want != have:
            drift[demo] = {"missing_from_bundle": sorted(want - have),
                           "extra_in_bundle": sorted(have - want)}
    assert not drift, f"bundled demos drifted from examples/: {drift}"
