"""pyodex command line.

Commands:
  pyodex init <dir>        scaffold a new extension project
  pyodex build <dir>       assemble a loadable extension into dist/<name>/
  pyodex zip <dir>         build and produce dist/<name>.zip
  pyodex fetch             download / refresh the Pyodide runtime cache
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import PYODIDE_VERSION
from .builder import build_extension, zip_extension
from .deps import ensure_pyodide
from .scaffold import init_project


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir).resolve()
    init_project(target, name=args.name or target.name, matches=args.match)
    print(f"scaffolded {target}")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    path = ensure_pyodide(args.pyodide_version, force=args.force)
    print(f"pyodide {args.pyodide_version} ready at {path}")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    out = build_extension(Path(args.dir).resolve(), Path(args.out).resolve() if args.out else None,
                          allow_bad=args.allow_bad)
    print(f"built {out}")
    return 0


def _cmd_zip(args: argparse.Namespace) -> int:
    out = zip_extension(Path(args.dir).resolve(), Path(args.out).resolve() if args.out else None)
    print(f"zipped {out}")
    return 0


def _cmd_compat(args: argparse.Namespace) -> int:
    from . import compat

    if args.action == "list":
        reg = compat.load_registry()
        for name, e in sorted(reg.items()):
            if args.filter and e["status"] != args.filter:
                continue
            print(f"{e['status']:<8} {name:<32} {e.get('version', ''):<12} {e.get('reason', '')}")
        return 0
    names = list(args.packages)
    if args.top:
        names += [n for n in compat.top_packages(args.top) if n not in names]
    if not names:
        print("nothing to test", file=sys.stderr)
        return 2
    results = []
    for n in names:
        r = compat.test_package(n, args.pyodide_version)
        results.append(r)
        print(f"{r.status:<5} {n:<32} {r.version:<12} {r.reason}")
    compat.record(results, into_shipped=args.shipped)
    print("\n" + ", ".join(f"{s}={sum(r.status == s for r in results)}" for s in ("good", "import-only", "bad")))
    return 0


def _cmd_cache(args: argparse.Namespace) -> int:
    from .deps import cache_dir
    from .packages import prune_store, store_dir

    if args.action == "dir":
        print(cache_dir())
        return 0
    keep = set()
    for lk in Path(".").rglob("*pyodex.lock"):
        try:
            keep |= {f"{p['name'].lower().replace('_','-')}=={p['version']}" for p in json.loads(lk.read_text())["pins"]}
        except (json.JSONDecodeError, KeyError):
            pass
    removed = prune_store(keep=keep or None, dry=args.dry)
    for r in removed:
        print(("would remove " if args.dry else "removed ") + str(r.relative_to(store_dir())))
    print(f"{len(removed)} entries, kept {len(keep)} (from lock files under cwd)")
    return 0


def _cmd_pyodide(args: argparse.Namespace) -> int:
    from . import pyodide_caps as pc

    if args.action == "probe":
        caps = pc.probe(args.version)
        skip = {"packages", "ffi", "js_api", "stdlib_unvendored"}
        for k, v in sorted(caps.items()):
            if k not in skip:
                print(f"{k:<18} {v}")
        print(f"packages          {len(caps['packages'])}")
        print(f"saved {pc.CAPS_DIR / (args.version + '.json')}")
    elif args.action == "latest":
        print(pc.latest())
    elif args.action == "diff":
        if len(args.versions) != 2:
            print("diff needs OLD NEW", file=sys.stderr)
            return 2
        print(pc.format_diff(pc.diff(*args.versions)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pyodex", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="scaffold a new extension project")
    s.add_argument("dir")
    s.add_argument("--name", help="extension display name (default: dir name)")
    s.add_argument("--match", action="append", help="URL match pattern (repeatable). Default: <all_urls>")
    s.set_defaults(fn=_cmd_init)

    s = sub.add_parser("fetch", help="download Pyodide runtime into the cache")
    s.add_argument("--pyodide-version", default=PYODIDE_VERSION)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=_cmd_fetch)

    s = sub.add_parser("build", help="assemble extension into dist/")
    s.add_argument("dir")
    s.add_argument("--out")
    s.add_argument("--allow-bad", action="store_true", help="build even with known-incompatible packages")
    s.set_defaults(fn=_cmd_build)

    s = sub.add_parser("zip", help="build and zip extension")
    s.add_argument("dir")
    s.add_argument("--out")
    s.set_defaults(fn=_cmd_zip)

    s = sub.add_parser("compat", help="test / list Pyodide package compatibility")
    s.add_argument("action", choices=["test", "list"])
    s.add_argument("packages", nargs="*")
    s.add_argument("--top", type=int, help="also test top-N PyPI packages")
    s.add_argument("--filter", choices=["good", "import-only", "bad"], help="for list")
    s.add_argument("--shipped", action="store_true", help="write results into the shipped registry")
    s.add_argument("--pyodide-version", default=PYODIDE_VERSION)
    s.set_defaults(fn=_cmd_compat)

    s = sub.add_parser("cache", help="inspect / prune the wheel store")
    s.add_argument("action", choices=["dir", "prune"])
    s.add_argument("--dry", action="store_true")
    s.set_defaults(fn=_cmd_cache)

    s = sub.add_parser("pyodide", help="probe / diff Pyodide runtime capabilities")
    s.add_argument("action", choices=["probe", "latest", "diff"])
    s.add_argument("versions", nargs="*", help="for diff: OLD NEW")
    s.add_argument("--version", default=PYODIDE_VERSION, help="for probe")
    s.set_defaults(fn=_cmd_pyodide)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
