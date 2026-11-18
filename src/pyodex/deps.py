"""Download and cache the Pyodide core runtime and other vendored assets."""

from __future__ import annotations

import hashlib
import json
import os
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
