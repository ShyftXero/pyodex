# /// script
# requires-python = ">=3.12"
# dependencies = ["pyext"]
#
# [tool.uv.sources]
# pyext = { path = ".", editable = true }
# ///
"""Bundle a single PEP 723 Python script into an installable Chrome extension.

    uv run shyft_ext_bundle.py my_cool_ext.py
    uv run shyft_ext_bundle.py my_cool_ext.py --out ~/Desktop --no-zip

The script declares its Python deps and extension config inline:

    # /// script
    # dependencies = ["requests", "numpy"]
    #
    # [tool.pyext]
    # name = "My Cool Ext"
    # version = "0.1.0"
    # matches = ["*://*.example.com/*"]
    # url_filter = "example"
    # scripts = ["lib.js"]              # vendored JS loaded before Python
    # overlay_css = "#x { color: red }"
    # overlay_body = "<div id='x'></div>"
    #
    # [tool.pyext.vendor]
    # "lib.js" = "https://cdn.jsdelivr.net/npm/some-lib/dist/index.umd.js"
    # ///

Output: <script_dir>/dist/<slug>/ (unpacked) and <script_dir>/dist/<slug>.zip.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("script", help="PEP 723 Python script (entry point of the extension)")
    p.add_argument("--out", help="output directory (default: <script_dir>/dist)")
    p.add_argument("--no-zip", action="store_true", help="only build the unpacked dir")
    p.add_argument("--refresh-lock", action="store_true", help="re-resolve dependencies")
    args = p.parse_args(argv)

    from pyext.builder import build_extension, resolve_project, zip_dir

    script = Path(args.script).resolve()
    if not script.is_file() or script.suffix != ".py":
        print(f"error: {script} is not a .py file", file=sys.stderr)
        return 2

    project, cfg = resolve_project(script)
    if args.refresh_lock:
        for lk in project.glob("*.pyext.lock"):
            lk.unlink()
        (project / "pyext.lock").unlink(missing_ok=True)

    out_root = Path(args.out).resolve() if args.out else project / "dist"
    built = build_extension(script, out_root / cfg.slug)
    print(f"built  {built}")
    if not args.no_zip:
        z = zip_dir(built, out_root / f"{cfg.slug}.zip")
        print(f"zipped {z}")
    print("\nInstall: chrome://extensions -> Developer mode -> Load unpacked -> pick the built dir")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
