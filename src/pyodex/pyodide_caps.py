"""Pyodide capability snapshots.

Runs `pyodex_natural.detect()` plus a JS-side API probe inside a real Pyodide
under Node and stores the result in `src/pyodex/capabilities/<version>.json`.
`diff` shows what a newer Pyodide adds/removes so shims can be extended.

    pyodex pyodide probe                  # current PYODIDE_VERSION
    pyodex pyodide probe --version 315.0.0
    pyodex pyodide latest                 # newest GitHub release tag
    pyodex pyodide diff 314.0.8 315.0.0
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from . import PYODIDE_VERSION
from .deps import ensure_pyodide

CAPS_DIR = Path(__file__).parent / "capabilities"
RUNTIME = Path(__file__).parent / "runtime"

PROBE_JS = r"""
const [, , indexURL, naturalPath] = process.argv;
const { loadPyodide } = await import(indexURL + "/pyodide.mjs");
const fs = await import("node:fs");
const py = await loadPyodide({ indexURL, stdout: () => {}, stderr: () => {} });
py.FS.writeFile("/home/pyodide/pyodex_natural.py", fs.readFileSync(naturalPath, "utf8"));
await py.runPythonAsync("import sys; sys.path.insert(0, '/home/pyodide')");
globalThis.pyodide = py;  // natural.py reads js.pyodide.FS
const pyCaps = JSON.parse(await py.runPythonAsync("import pyodex_natural, json; json.dumps(pyodex_natural.detect())"));
const lock = JSON.parse(fs.readFileSync(indexURL + "/pyodide-lock.json", "utf8"));
const out = {
  ...pyCaps,
  js_api: Object.keys(py).filter((k) => !k.startsWith("_")).sort(),
  fs_types: Object.keys(py.FS.filesystems || {}).sort(),
  lock_info: lock.info,
  packages: Object.fromEntries(Object.values(lock.packages).map((p) => [p.name, p.version])),
  stdlib_unvendored: Object.values(lock.packages).filter((p) => p.package_type === "cpython_module").map((p) => p.name).sort(),
};
console.log(JSON.stringify(out));
"""


def probe(version: str = PYODIDE_VERSION, *, save: bool = True) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found")
    pyo = ensure_pyodide(version)
    with tempfile.TemporaryDirectory() as td:
        runner = Path(td) / "probe.mjs"
        runner.write_text(PROBE_JS)
        proc = subprocess.run([node, "--experimental-wasm-stack-switching", str(runner), str(pyo),
                               str(RUNTIME / "pyodex_natural.py")], capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode:
        raise RuntimeError(f"probe failed:\n{proc.stderr[-800:]}")
    caps = json.loads(proc.stdout.strip().splitlines()[-1])
    caps["pyodide"] = version
    if save:
        CAPS_DIR.mkdir(exist_ok=True)
        (CAPS_DIR / f"{version}.json").write_text(json.dumps(caps, indent=1, sort_keys=True) + "\n")
    return caps


def load(version: str) -> dict:
    p = CAPS_DIR / f"{version}.json"
    if not p.exists():
        return probe(version)
    return json.loads(p.read_text())


def latest() -> str:
    req = urllib.request.Request("https://api.github.com/repos/pyodide/pyodide/releases/latest",
                                 headers={"User-Agent": "pyodex"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return json.load(r)["tag_name"]


def diff(old: str, new: str) -> dict:
    a, b = load(old), load(new)
    out: dict = {"old": old, "new": new, "changed": {}, "added": {}, "removed": {}}
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if isinstance(va, list) and isinstance(vb, list):
            add, rem = sorted(set(vb) - set(va)), sorted(set(va) - set(vb))
            if add:
                out["added"][k] = add
            if rem:
                out["removed"][k] = rem
        elif isinstance(va, dict) and isinstance(vb, dict):
            add = {x: vb[x] for x in vb if x not in va}
            rem = {x: va[x] for x in va if x not in vb}
            chg = {x: (va[x], vb[x]) for x in va if x in vb and va[x] != vb[x]}
            if add:
                out["added"][k] = add
            if rem:
                out["removed"][k] = rem
            if chg:
                out["changed"][k] = chg
        else:
            out["changed"][k] = (va, vb)
    return out


def format_diff(d: dict) -> str:
    lines = [f"pyodide {d['old']} -> {d['new']}"]
    for sec in ("changed", "added", "removed"):
        for k, v in d[sec].items():
            if isinstance(v, (list, dict)) and len(v) > 25:
                v = f"{len(v)} items"
            lines.append(f"  {sec:<8} {k}: {v}")
    if len(lines) == 1:
        lines.append("  no differences")
    return "\n".join(lines)
