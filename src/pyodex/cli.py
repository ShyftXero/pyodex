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
    from .scaffold import available_demos, init_demo

    target = Path(args.dir).resolve()
    if args.demo:
        init_demo(target, args.demo)
        print(f"scaffolded demo {args.demo} into {target}")
        print(f"build it with: pyodex build {target}")
        return 0
    init_project(target, name=args.name or target.name, matches=args.match)
    print(f"scaffolded {target}")
    if args.list_demos:
        print("demos:", ", ".join(available_demos()))
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


def _cmd_explain(args: argparse.Namespace) -> int:
    import json as _json

    from .explain import render, report

    data = report(Path(args.dir).resolve())
    print(_json.dumps(data, indent=2) if args.json else render(data))
    return 1 if data["problems"] else 0


def _cmd_dev(args: argparse.Namespace) -> int:
    from .dev import watch

    return watch(Path(args.dir).resolve(), Path(args.out).resolve() if args.out else None,
                 interval=args.interval, once=args.once)


def _cmd_zip(args: argparse.Namespace) -> int:
    from .builder import store_report

    project = Path(args.dir).resolve()
    out = zip_extension(project, Path(args.out).resolve() if args.out else None)
    print(f"zipped {out}\n")

    built = next((p for p in (project / "dist").iterdir() if (p / "manifest.json").exists()), None)
    if built is None:
        return 0
    print("store preflight:")
    blocked = 0
    for ok, check, detail in store_report(built):
        print(f"  {'ok  ' if ok else 'FAIL'} {check:<34} {detail}")
        blocked += 0 if ok else 1
    print("\nready to upload" if not blocked else
          f"\nnot ready: {blocked} hard blocker(s) above — a zip of a rejected build is "
          "still rejected")
    return 0


def _cmd_compat(args: argparse.Namespace) -> int:
    from . import compat

    if args.action == "list":
        reg = compat.load_registry()
        if args.runtime == "micropython":
            from .compat_mp import OUT as MP_REGISTRY

            if not MP_REGISTRY.exists():
                print("no MicroPython registry yet; run `pyodex compat mp --top 30 --write`")
                return 1
            reg = json.loads(MP_REGISTRY.read_text())
        for name, e in sorted(reg.items()):
            if args.filter and e["status"] != args.filter:
                continue
            print(f"{e['status']:<8} {name:<32} {e.get('version', ''):<12} {e.get('reason', '')}")
        return 0
    if args.action == "mp":
        from .compat_mp import main as mp_main

        return mp_main([*args.packages, *(["--top", str(args.top)] if args.top else []),
                        *(["--write"] if args.shipped else [])])

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


def _cmd_iwa(args: argparse.Namespace) -> int:
    from . import iwa

    target = Path(args.dir).resolve()
    if args.action == "build":
        out = iwa.build_iwa(target, Path(args.out).resolve() if args.out else None,
                            direct_sockets=args.direct_sockets)
        print(f"built IWA at {out}")
        flag = " --direct-sockets" if args.direct_sockets else ""
        print(f"serve it with: pyodex iwa dev {target}{flag}")
        return 0
    iwa.dev(target, port=args.port, direct_sockets=args.direct_sockets)
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
    s.add_argument("--demo", help="scaffold from a bundled demo, e.g. --demo 01")
    s.add_argument("--list-demos", action="store_true", help="print the bundled demos")
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

    s = sub.add_parser("explain", help="print what the build decides, with file:line")
    s.add_argument("dir")
    s.add_argument("--json", action="store_true", help="machine-readable receipt")
    s.set_defaults(fn=_cmd_explain)

    s = sub.add_parser("dev", help="rebuild in place on change (load unpacked once)")
    s.add_argument("dir")
    s.add_argument("--out")
    s.add_argument("--interval", type=float, default=0.5, help="seconds between change checks")
    s.add_argument("--once", action="store_true", help="rebuild once and exit")
    s.set_defaults(fn=_cmd_dev)

    s = sub.add_parser("zip", help="build and zip extension")
    s.add_argument("dir")
    s.add_argument("--out")
    s.set_defaults(fn=_cmd_zip)

    s = sub.add_parser("compat", help="test / list Pyodide package compatibility")
    s.add_argument("action", choices=["test", "list", "mp"])
    s.add_argument("packages", nargs="*")
    s.add_argument("--top", type=int, help="also test top-N PyPI packages")
    s.add_argument("--filter", choices=["good", "import-only", "bad"], help="for list")
    s.add_argument("--shipped", action="store_true", help="write results into the shipped registry")
    s.add_argument("--runtime", choices=["pyodide", "micropython"], default="pyodide")
    s.add_argument("--pyodide-version", default=PYODIDE_VERSION)
    s.set_defaults(fn=_cmd_compat)

    s = sub.add_parser("iwa", help="package/serve an Isolated Web App (experimental)")
    s.add_argument("action", choices=["build", "dev"])
    s.add_argument("dir")
    s.add_argument("--out")
    s.add_argument("--port", type=int, default=0, help="for dev: loopback port (default: random)")
    s.add_argument("--direct-sockets", action="store_true",
                   help="SECURITY: grant the IWA Direct Sockets + local/loopback "
                        "network access (raw TCP/UDP to localhost and your LAN "
                        "from the web app). Dev-only; do not ship.")
    s.set_defaults(fn=_cmd_iwa)

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
