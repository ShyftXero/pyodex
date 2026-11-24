"""Integrity verification for the wasm/JS runtime cores (finding C1).

These tests prove that `deps.py` fails closed when a downloaded runtime core does
not match its committed sha256 pin, and that an unpinned (runtime, version) is
refused rather than trusted. No network: `_download` is monkeypatched.
"""

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "proxy"))

from pyodex import deps


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PYODEX_CACHE", str(tmp_path / "cache"))
    yield


def _fake_download(mapping: dict[str, bytes]):
    """Return a _download replacement that writes bytes chosen per-URL/filename."""

    def _dl(url: str, dest: Path) -> None:
        for key, data in mapping.items():
            if url.endswith(key) or key == "*":
                dest.write_bytes(data)
                return
        raise AssertionError(f"unexpected download url: {url}")

    return _dl


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_expected_hash_present():
    h = deps._expected_hash("micropython", deps.MICROPYTHON_VERSION, "micropython.wasm")
    assert len(h) == 64


def test_expected_hash_unpinned_fails_closed():
    with pytest.raises(deps.IntegrityError):
        deps._expected_hash("pyodide", "0.0.0-nonexistent", "x.tar.bz2")


def test_verify_deletes_file_on_mismatch(tmp_path):
    f = tmp_path / "micropython.wasm"
    f.write_bytes(b"tampered")
    with pytest.raises(deps.IntegrityError):
        deps._verify(f, "micropython", deps.MICROPYTHON_VERSION, "micropython.wasm")
    assert not f.exists()  # fails closed: poisoned bytes are removed


# --------------------------------------------------------------------------- #
# ensure_micropython
# --------------------------------------------------------------------------- #


def test_micropython_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(deps, "_download", _fake_download({"*": b"evil"}))
    with pytest.raises(deps.IntegrityError):
        deps.ensure_micropython()
    target = deps.cache_dir() / f"micropython-{deps.MICROPYTHON_VERSION}"
    assert not (target / ".complete").exists()


def test_micropython_happy_path(monkeypatch):
    good = {n: ("mp-" + n).encode() for n in deps.MICROPYTHON_FILES}
    monkeypatch.setattr(deps, "_download", _fake_download(good))
    monkeypatch.setitem(
        deps.RUNTIME_HASHES["micropython"],
        deps.MICROPYTHON_VERSION,
        {n: _sha(b) for n, b in good.items()},
    )
    target = deps.ensure_micropython()
    assert (target / ".complete").exists()
    for n, b in good.items():
        assert (target / n).read_bytes() == b


# --------------------------------------------------------------------------- #
# ensure_rustpython  (verify must happen BEFORE the ESM patch)
# --------------------------------------------------------------------------- #


def test_rustpython_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(deps, "_download", _fake_download({"*": b"evil"}))
    with pytest.raises(deps.IntegrityError):
        deps.ensure_rustpython()
    target = deps.cache_dir() / f"rustpython-{deps.RUSTPYTHON_VERSION}"
    assert not (target / ".complete").exists()


def test_rustpython_verifies_pristine_bytes_before_patch(monkeypatch):
    # The real ESM patch rewrites rustpython_wasm.js; verification must run on the
    # pristine upstream bytes. Supply a .js the patcher accepts, pin its raw hash.
    js = (
        b"import * as wasm from './rustpython_wasm_bg';\n"
        b"wasm.__wbindgen_start();\n"
    )
    wasm = b"\x00asm-bytes"
    good = {"rustpython_wasm.js": js, "rustpython_wasm_bg.wasm": wasm}
    monkeypatch.setattr(deps, "_download", _fake_download(good))
    monkeypatch.setitem(
        deps.RUNTIME_HASHES["rustpython"],
        deps.RUSTPYTHON_VERSION,
        {n: _sha(b) for n, b in good.items()},
    )
    target = deps.ensure_rustpython()
    assert (target / ".complete").exists()
    # Patch applied after verification succeeded.
    assert "__pyodex_setWasm" in (target / "rustpython_wasm.js").read_text()


# --------------------------------------------------------------------------- #
# ensure_pyodide  (archive verified before extraction)
# --------------------------------------------------------------------------- #


def _tar_bz2(top: str, name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        info = tarfile.TarInfo(f"{top}/{name}")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_pyodide_mismatch_fails_closed(monkeypatch):
    archive = _tar_bz2("pyodide", "pyodide.mjs", b"real")
    monkeypatch.setattr(deps, "_download", _fake_download({".tar.bz2": archive}))
    # Pin a wrong hash for this version.
    monkeypatch.setitem(
        deps.RUNTIME_HASHES["pyodide"], "9.9.9", {"pyodide-core-9.9.9.tar.bz2": _sha(b"nope")}
    )
    with pytest.raises(deps.IntegrityError):
        deps.ensure_pyodide("9.9.9")
    assert not (deps.cache_dir() / "pyodide-9.9.9" / ".complete").exists()


def test_pyodide_happy_path(monkeypatch):
    archive = _tar_bz2("pyodide", "pyodide.mjs", b"real-core")
    monkeypatch.setattr(deps, "_download", _fake_download({".tar.bz2": archive}))
    monkeypatch.setitem(
        deps.RUNTIME_HASHES["pyodide"], "9.9.9", {"pyodide-core-9.9.9.tar.bz2": _sha(archive)}
    )
    target = deps.ensure_pyodide("9.9.9")
    assert (target / ".complete").exists()
    assert (target / "pyodide.mjs").read_bytes() == b"real-core"


def test_pyodide_unpinned_version_fails_closed(monkeypatch):
    archive = _tar_bz2("pyodide", "pyodide.mjs", b"x")
    monkeypatch.setattr(deps, "_download", _fake_download({".tar.bz2": archive}))
    with pytest.raises(deps.IntegrityError):
        deps.ensure_pyodide("0.0.0-unpinned")


# --------------------------------------------------------------------------- #
# ensure_url_asset  (opt-in pin for user vendor assets)
# --------------------------------------------------------------------------- #


def test_url_asset_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(deps, "_download", _fake_download({"*": b"served"}))
    with pytest.raises(deps.IntegrityError):
        deps.ensure_url_asset("https://x/a.js", "a.js", expected_sha256=_sha(b"wanted"))


def test_url_asset_pin_ok(monkeypatch):
    monkeypatch.setattr(deps, "_download", _fake_download({"*": b"served"}))
    p = deps.ensure_url_asset("https://x/a.js", "a.js", expected_sha256=_sha(b"served"))
    assert p.read_bytes() == b"served"


def test_url_asset_no_pin_downloads(monkeypatch):
    monkeypatch.setattr(deps, "_download", _fake_download({"*": b"served"}))
    p = deps.ensure_url_asset("https://x/a.js", "a.js")
    assert p.read_bytes() == b"served"
