"""Download and cache the Pyodide core runtime and other vendored assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import PYODIDE_VERSION

PYODIDE_RELEASE_URL = "https://github.com/pyodide/pyodide/releases/download/{v}/pyodide-core-{v}.tar.bz2"

# Minimal file set needed to boot Pyodide inside an extension.
PYODIDE_CORE_FILES = (
    "pyodide.mjs",
    "pyodide.asm.js",  # pre-314 name
    "pyodide.asm.mjs",  # 314+ name
    "pyodide.asm.wasm",
    "pyodide-lock.json",
    "python_stdlib.zip",
)


def cache_dir() -> Path:
    base = os.environ.get("PYODEX_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"), "pyodex"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "pyodex"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:  # noqa: S310
        while chunk := r.read(1 << 16):
            f.write(chunk)


def ensure_pyodide(version: str = PYODIDE_VERSION, force: bool = False) -> Path:
    """Return a directory containing the extracted Pyodide core for `version`."""
    target = cache_dir() / f"pyodide-{version}"
    marker = target / ".complete"
    if marker.exists() and not force:
        return target

    url = PYODIDE_RELEASE_URL.format(v=version)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "pyodide-core.tar.bz2"
        print(f"downloading {url}")
        _download(url, archive)
        extract = Path(td) / "x"
        extract.mkdir()
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(extract, filter="data")
        # archive has a single top-level dir ("pyodide/")
        roots = [p for p in extract.iterdir() if p.is_dir()]
        src = roots[0] if len(roots) == 1 else extract
        if target.exists():
            import shutil

            shutil.rmtree(target)
        target.mkdir(parents=True)
        import shutil

        for p in src.iterdir():
            shutil.move(str(p), target / p.name)
    marker.write_text(url)
    return target


# Pure-Python wheels that make `requests` and `urllib.request` work in the browser.
# urllib3 >= 2.2 ships a native Emscripten backend (fetch / XHR), so `requests`
# works unpatched. `pyodide-http` patches `urllib.request` to use XHR.
HTTP_SHIM_PACKAGES = (
    "pyodide-http",
    "requests",
    "urllib3",
    "charset-normalizer",
    "idna",
    "certifi",
)

MICROPYTHON_NPM = "https://cdn.jsdelivr.net/npm/@micropython/micropython-webassembly-pyscript@1.29.0-6"
MICROPYTHON_FILES = ("micropython.mjs", "micropython.wasm")


def ensure_micropython(force: bool = False) -> Path:
    """Fetch the prebuilt MicroPython wasm distribution into the cache."""
    target = cache_dir() / "micropython-1.29.0-6"
    marker = target / ".complete"
    if marker.exists() and not force:
        return target
    target.mkdir(parents=True, exist_ok=True)
    for name in MICROPYTHON_FILES:
        dest = target / name
        if not dest.exists() or force:
            print(f"downloading {MICROPYTHON_NPM}/{name}")
            _download(f"{MICROPYTHON_NPM}/{name}", dest)
    marker.write_text(MICROPYTHON_NPM)
    return target


RUSTPYTHON_NPM = "https://cdn.jsdelivr.net/npm/rustpython_wasm@0.1.0-pre-alpha.2"
RUSTPYTHON_FILES = ("rustpython_wasm.js", "rustpython_wasm_bg.wasm")


def ensure_rustpython(force: bool = False) -> Path:
    """Fetch the prebuilt RustPython wasm distribution into the cache."""
    target = cache_dir() / "rustpython-0.1.0-pre-alpha.2"
    marker = target / ".complete"
    if marker.exists() and not force:
        return target
    target.mkdir(parents=True, exist_ok=True)
    for name in RUSTPYTHON_FILES:
        dest = target / name
        if not dest.exists() or force:
            print(f"downloading {RUSTPYTHON_NPM}/{name}")
            _download(f"{RUSTPYTHON_NPM}/{name}", dest)
    _patch_rustpython_esm(target / "rustpython_wasm.js")
    marker.write_text(RUSTPYTHON_NPM)
    return target


def _patch_rustpython_esm(path: Path) -> None:
    """Make the vendored ESM accept an instance instead of importing one.

    The package ships `rustpython_wasm.js` but no `rustpython_wasm_bg.js`: it
    expects WebAssembly ESM integration (importing a .wasm as a module), which
    Chrome gates behind a flag. The wasm's own imports come from the ESM
    (`./rustpython_wasm`), so the cycle closes by passing the instance in after
    both sides exist.
    """
    src = path.read_text()
    if "__pyodex_setWasm" in src:
        return
    src, n1 = re.subn(r"import \* as wasm from './rustpython_wasm_bg';",
                      "let wasm = {};\nexport function __pyodex_setWasm(w) { wasm = w; }", src)
    src, n2 = re.subn(r"^wasm\.__wbindgen_start\(\);", "export function __pyodex_start() { wasm.__wbindgen_start(); }",
                      src, flags=re.M)
    # The glue's only eval-class call is wasm-bindgen's JS `Function` constructor:
    #
    #     return addHeapObject(new Function(varg0));
    #
    # MV3 extension pages cannot use it, and nothing in pyEval needs JS functions
    # built from strings. Replacing it with a throwing stub lets the module load in a
    # normal extension page; if a build ever does need it the failure is loud.
    src, n3 = re.subn(
        r"return addHeapObject\(new Function\(varg0\)\);",
        'throw new Error("new Function is unavailable under extension CSP");',
        src,
    )
    if n1 != 1 or n2 != 1:
        raise RuntimeError(f"rustpython ESM patch did not apply (import={n1}, start={n2})")
    path.write_text(src)
    if n3 != 1:
        print(f"warning: `new Function` stub applied {n3} time(s); the glue may have changed")


def ensure_url_asset(url: str, filename: str, force: bool = False) -> Path:
    """Download an arbitrary URL (e.g. a UMD bundle) into the cache, keyed by URL hash."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    dest = cache_dir() / "assets" / key / filename
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    _download(url, dest)
    return dest
