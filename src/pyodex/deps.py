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

MICROPYTHON_VERSION = "1.29.0-6"
RUSTPYTHON_VERSION = "0.1.0-pre-alpha.2"

# Committed sha256 pins for the wasm/JS runtime cores, keyed by (runtime, version,
# filename). Verified after download; a mismatch (or a missing pin) fails closed so
# that poisoned release assets / CDN paths / a bad cert store cannot inject code into
# a built extension. The pyodide entry pins the downloaded release archive (verified
# before extraction); the others pin the individual files as downloaded, BEFORE any
# local patching. Hashes are of the exact bytes served by the upstream URLs.
# To add a new version: download the asset, compute `sha256sum`, and add it here.
RUNTIME_HASHES: dict[str, dict[str, dict[str, str]]] = {
    "pyodide": {
        "314.0.8": {
            "pyodide-core-314.0.8.tar.bz2": "ff3e5f9d9bc8e98ff30b99515535fc24d375994f47a978c61975d653e162ca37",
        },
    },
    "micropython": {
        "1.29.0-6": {
            "micropython.mjs": "26cc7a99064a16518ab9a695e848efb248bfce504aee56ae73b2af35f3dcbf8b",
            "micropython.wasm": "d5913c9327072e71d3e7a2e9384ea326f7d784bcc2f50a01b602c52bd2bdcafd",
        },
    },
    "rustpython": {
        "0.1.0-pre-alpha.2": {
            "rustpython_wasm.js": "eafce793663d67ce1210a29573fb630807cfa8a795bff98d2fa36dd99c717bd4",
            "rustpython_wasm_bg.wasm": "dc2e37697e98517e4beca6a99ea16bad7ed7866465d67d4bc79dd5596ef6d680",
        },
    },
}


class IntegrityError(RuntimeError):
    """Raised when a downloaded runtime core fails its pinned sha256 check."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def _expected_hash(runtime: str, version: str, filename: str) -> str:
    """Return the pinned sha256 for a runtime file, or fail closed if unpinned."""
    try:
        return RUNTIME_HASHES[runtime][version][filename]
    except KeyError:
        raise IntegrityError(
            f"no pinned sha256 for {runtime} {version} file {filename!r}; refusing to "
            "use unverified runtime bytes. Add the hash to RUNTIME_HASHES in deps.py."
        ) from None


def _verify(path: Path, runtime: str, version: str, filename: str) -> None:
    """Verify `path` against the committed pin; delete it and raise on mismatch."""
    expected = _expected_hash(runtime, version, filename)
    actual = _sha256_file(path)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise IntegrityError(
            f"integrity check failed for {runtime} {version} {filename!r}: expected "
            f"sha256 {expected}, got {actual}. Refusing to use unverified runtime bytes."
        )

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
        # Verify the release archive against its committed pin before extracting:
        # never let unauthenticated bytes reach tarfile or the cache.
        _verify(archive, "pyodide", version, f"pyodide-core-{version}.tar.bz2")
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
        # Always (re)download when the completion marker is absent so we verify
        # fresh bytes rather than trusting a possibly-stale cache file.
        print(f"downloading {MICROPYTHON_NPM}/{name}")
        _download(f"{MICROPYTHON_NPM}/{name}", dest)
        _verify(dest, "micropython", MICROPYTHON_VERSION, name)
    marker.write_text(MICROPYTHON_NPM)
    return target


def stub_new_function(glue: Path) -> int:
    """Replace wasm-bindgen's `new Function` with a throwing stub.

    Only valid when the runtime is NOT hosted in a sandbox page: sandboxed pages may
    use eval, where the real constructor is what makes Python->JS calls work at all.
    Applying it unconditionally is what broke the sandbox tier.
    """
    src = glue.read_text()
    patched, n = re.subn(
        r"return addHeapObject\(new Function\(varg0\)\);",
        'throw new Error("new Function is unavailable under extension CSP");',
        src,
    )
    if n:
        glue.write_text(patched)
    return n


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
        # Always (re)download when the completion marker is absent, and verify the
        # pristine upstream bytes BEFORE the local ESM patch rewrites the .js.
        print(f"downloading {RUSTPYTHON_NPM}/{name}")
        _download(f"{RUSTPYTHON_NPM}/{name}", dest)
        _verify(dest, "rustpython", RUSTPYTHON_VERSION, name)
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
    if n1 != 1 or n2 != 1:
        raise RuntimeError(f"rustpython ESM patch did not apply (import={n1}, start={n2})")
    path.write_text(src)



def ensure_url_asset(
    url: str, filename: str, force: bool = False, expected_sha256: str | None = None
) -> Path:
    """Download an arbitrary URL (e.g. a UMD bundle) into the cache, keyed by URL hash.

    These are user-configured vendor assets with no committed pin, so integrity is
    opt-in: pass `expected_sha256` to verify the download (fails closed on mismatch).
    """
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    dest = cache_dir() / "assets" / key / filename
    if dest.exists() and not force:
        if expected_sha256 and _sha256_file(dest) != expected_sha256:
            dest.unlink(missing_ok=True)
        else:
            return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    _download(url, dest)
    if expected_sha256:
        actual = _sha256_file(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise IntegrityError(
                f"integrity check failed for vendor asset {filename!r} from {url}: "
                f"expected sha256 {expected_sha256}, got {actual}."
            )
    return dest
