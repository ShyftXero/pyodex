"""Which top PyPI packages work in MicroPython? (evidence, not vibes)

MicroPython has no package installer, so "compatibility" here means something
specific and useful: take the pure-Python wheel, drop its modules onto
MicroPython's filesystem, and see whether it imports. That is exactly what an
extension developer would do to vendor a package into a 2 MB tier, so the answer
transfers directly.

    uv run tests/compat_micropython.py --top 25 --write
    uv run tests/compat_micropython.py click attrs six

Writes `src/pyodex/compat_mp.json` with --write. Runs under Node (fast, one
process for every package); the Pyodide registry does the same trick, and harness
L10 keeps the browser honest for the subset that matters.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from .compat import IMPORT_NAMES, top_packages
from .deps import MICROPYTHON_NPM, cache_dir, ensure_micropython

OUT = Path(__file__).resolve().parent / "compat_mp.json"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"


class Skip(Exception):
    """Not testable in this tier (no pure wheel, or needs native code)."""


def pure_wheel(name: str) -> tuple[Path, str]:
    """Download the newest pure-Python wheel for `name`."""
    req = urllib.request.Request(PYPI_JSON.format(name=name), headers={"User-Agent": "pyodex"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        meta = json.load(r)
    version = meta["info"]["version"]
    files = [f for f in meta["releases"].get(version, [])
             if f["filename"].endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))]
    if not files:
        raise Skip("no pure-Python wheel")
    f = files[0]
    dest = cache_dir() / "mp-wheels" / f["filename"]
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f["url"], headers={"User-Agent": "pyodex"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:  # noqa: S310
            shutil.copyfileobj(r, fh)
    return dest, version


def stage(wheel: Path, dest: Path) -> None:
    """Unpack just the Python sources: MicroPython cannot use .so or metadata."""
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if not info.filename.endswith(".py"):
                continue
            target = dest / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))


RUNNER = r"""
const [, , mpDir, root, namesJson] = process.argv;
const fs = await import("node:fs");
const path = await import("node:path");
const { loadMicroPython } = await import(mpDir + "/micropython.mjs");

const mp = await loadMicroPython();
const names = JSON.parse(namesJson);
const importOf = JSON.parse(process.argv[5] || "{}");

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith(".py")) out.push(p);
  }
  return out;
}

for (const name of names) {
  const dir = path.join(root, name);
  if (!fs.existsSync(dir)) {
    console.log(`MPRESULT|${name}|skip: not staged`);
    continue;
  }
  for (const file of walk(dir)) {
    const rel = path.relative(dir, file);
    const target = "/" + rel;
    try {
      mp.FS.mkdirTree(path.dirname(target));
    } catch {}
    try {
      mp.FS.writeFile(target, fs.readFileSync(file));
    } catch {}
  }
  const mod = importOf[name] || name.replace(/-/g, "_");
  // Print a marker rather than returning: runPythonAsync gives None for
  // multi-line blocks, so a JSON hand-off silently produced nulls.
  await mp.runPythonAsync(`
try:
    __import__(${JSON.stringify(mod)})
    print("MPRESULT|${name}|ok")
except Exception as e:
    print("MPRESULT|${name}|err: " + type(e).__name__ + ": " + str(e)[:120])
`);
}
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("packages", nargs="*")
    ap.add_argument("--top", type=int, default=0, help="also sweep this many top PyPI packages")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    names = list(args.packages)
    if args.top:
        names += [n for n in top_packages(args.top) if n not in names]
    if not names:
        print("nothing to test")
        return 2

    node = shutil.which("node")
    if not node:
        print("skip: node not available")
        return 0

    mp = ensure_micropython()
    work = Path(tempfile.mkdtemp(prefix="mp-compat-"))
    stageable: list[str] = []
    skipped: dict[str, str] = {}

    for name in names:
        try:
            wheel, _version = pure_wheel(name)
        except Skip as e:
            skipped[name] = str(e)
            continue
        except Exception as e:  # noqa: BLE001
            skipped[name] = f"fetch failed: {type(e).__name__}"
            continue
        stage(wheel, work / name)
        stageable.append(name)

    import_map = json.dumps({n: IMPORT_NAMES.get(n, n.replace("-", "_")) for n in stageable})
    runner = work / "run.mjs"
    runner.write_text("const names_import = " + import_map + ";\n" + RUNNER)

    proc = subprocess.run([node, "--experimental-wasm-stack-switching", str(runner), str(mp), str(work),
                           json.dumps(stageable), import_map], capture_output=True, text=True, timeout=900)
    results = {}
    for line in proc.stdout.splitlines():
        if line.startswith("MPRESULT|"):
            _, name, verdict = line.split("|", 2)
            results[name] = verdict
    if not results:
        print(proc.stdout[-1200:], file=sys.stderr)
        print(proc.stderr[-1200:], file=sys.stderr)
        print("no MPRESULT lines")
        return 1
    results.update({k: f"skip: {v}" for k, v in skipped.items()})

    registry: dict[str, dict] = {}
    if OUT.exists():
        registry = json.loads(OUT.read_text())

    good = bad = skip = 0
    for name, verdict in sorted(results.items()):
        if verdict == "ok":
            good += 1
            status, reason = "good", ""
        elif verdict.startswith("skip:"):
            skip += 1
            status, reason = "untested", verdict[6:]
        else:
            bad += 1
            status, reason = "bad", verdict[:160]
        print(f"{status:<9} {name:<28} {reason}")
        registry[name] = {"status": status, "reason": reason, "runtime": "micropython",
                          "source": "test", "micropython": MICROPYTHON_NPM.rsplit("@", 1)[-1]}

    if args.write:
        OUT.write_text(json.dumps(dict(sorted(registry.items())), indent=1) + "\n")
        print(f"\nwrote {OUT} ({len(registry)} entries)")

    print(f"\ngood={good} bad={bad} untested={skip}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
