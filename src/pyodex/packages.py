"""PEP 723 dependency resolution + global read-only wheel store.

`main.py` may declare dependencies inline:

    # /// script
    # dependencies = ["requests", "numpy>=2"]
    # ///

Resolution uses `uv pip compile --python-platform wasm32-pyodide2025`, so only
wheels that actually run in Pyodide (pure `py3-none-any` or
`pyodide_2025_0_wasm32`) are accepted. Pins are written to `pyodex.lock` beside
the project and reused until the dependency block changes.

Wheels live once in a global store (`~/.cache/pyodex/store/<name>/<version>/`)
with files chmod 0444 and dirs 0555, mirroring uv's shared package cache. Each
built extension gets a copy-on-write link into the store: reflink when the
filesystem supports it, else a hardlink (store file is read-only so writes
fail), else a plain copy. The store is never mutated after a wheel lands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .deps import cache_dir

PEP723_RE = re.compile(r"^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$", re.M)
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
DEFAULT_ABI = "2025_0"


def abi_from_lock(pyodide_lock: dict) -> str:
    """'2025_0' style ABI string from pyodide-lock.json."""
    return pyodide_lock.get("info", {}).get("abi_version", DEFAULT_ABI)


def wheel_tag(abi: str) -> str:
    """Platform tag PyPI wheels use for this ABI (both spellings seen in the wild)."""
    return f"{abi}_wasm32"


def uv_platform(abi: str) -> str:
    """uv --python-platform value. uv knows pyodide2024/2025; newer ABIs fall back to
    the newest uv supports (resolution still constrained by pyodide-lock versions)."""
    year = abi.split("_")[0]
    known = _uv_platforms()
    want = f"wasm32-pyodide{year}"
    if want in known:
        return want
    return sorted(known)[-1] if known else "wasm32-pyodide2025"


def _uv_platforms() -> list[str]:
    try:
        out = subprocess.run([_uv(), "pip", "compile", "--help"], capture_output=True, text=True, check=False).stdout
    except Exception:  # noqa: BLE001
        return []
    return sorted(set(re.findall(r"wasm32-pyodide\d{4}", out)))


@dataclass(frozen=True)
class Pin:
    name: str
    version: str

    @property
    def key(self) -> str:
        return self.name.lower().replace("_", "-")


# --------------------------------------------------------------------------- #
# PEP 723
# --------------------------------------------------------------------------- #


def read_pep723(script: Path) -> dict:
    """Return the parsed `# /// script` metadata block, or {} if absent."""
    text = script.read_text()
    hits = [m for m in PEP723_RE.finditer(text) if m.group("type") == "script"]
    if not hits:
        return {}
    if len(hits) > 1:
        raise ValueError(f"{script}: multiple `# /// script` blocks")
    body = "".join(line[2:] if line.startswith("# ") else line[1:] for line in hits[0].group("content").splitlines(keepends=True))
    return tomllib.loads(body)


def script_dependencies(script: Path) -> list[str]:
    return list(read_pep723(script).get("dependencies", []))


# --------------------------------------------------------------------------- #
# Resolution (uv)
# --------------------------------------------------------------------------- #


def _uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found on PATH; install from https://docs.astral.sh/uv/")
    return uv


def resolve(requirements: list[str], python_version: str, constraints: dict[str, str] | None = None,
            platform: str = "wasm32-pyodide2025") -> list[Pin]:
    """Pin `requirements` for the Pyodide platform via `uv pip compile`.

    `constraints` (name -> version) pins packages that Pyodide ships itself
    (numpy, pandas, ...) to the exact build Pyodide provides, since PyPI has no
    wasm32 wheel for those.
    """
    if not requirements:
        return []
    import tempfile

    cmd = [_uv(), "pip", "compile", "-", "--python-platform", platform, "--python-version",
           python_version, "--no-header", "--no-annotate", "--quiet"]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as cf:
        for name, ver in (constraints or {}).items():
            cf.write(f"{name}=={ver}\n")
        cpath = cf.name
    if constraints:
        cmd += ["--constraint", cpath]
    try:
        proc = subprocess.run(cmd, input="\n".join(requirements) + "\n", capture_output=True, text=True, check=False)
    finally:
        os.unlink(cpath)
    if proc.returncode:
        raise RuntimeError(f"uv pip compile failed:\n{proc.stderr.strip()}")
    pins = []
    for line in proc.stdout.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, ver = line.partition("==")
        pins.append(Pin(name.strip(), ver.strip()))
    return pins


def _deps_hash(requirements: list[str], python_version: str) -> str:
    return hashlib.sha256(("\n".join(sorted(requirements)) + "\n" + python_version).encode()).hexdigest()[:16]


def pyodide_constraints(pyodide_lock: dict) -> dict[str, str]:
    """name -> version for every package Pyodide distributes itself."""
    return {p["name"]: p["version"] for p in pyodide_lock.get("packages", {}).values()
            if p.get("file_name", "").endswith(".whl")}


def lock(lock_path: Path, requirements: list[str], python_version: str, *,
         constraints: dict[str, str] | None = None, refresh: bool = False,
         platform: str = "wasm32-pyodide2025") -> list[Pin]:
    """Resolve with caching in `lock_path` keyed by a hash of the inputs."""
    h = _deps_hash(requirements, python_version + platform + json.dumps(constraints or {}, sort_keys=True))
    if lock_path.exists() and not refresh:
        try:
            data = json.loads(lock_path.read_text())
            if data.get("hash") == h:
                return [Pin(p["name"], p["version"]) for p in data["pins"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    pins = resolve(requirements, python_version, constraints, platform)
    lock_path.write_text(json.dumps({
        "hash": h, "python": python_version, "platform": platform,
        "requirements": requirements, "pins": [p.__dict__ for p in pins],
    }, indent=2) + "\n")
    return pins


def record_digests(lock_path: Path, wheels: list[Path]) -> None:
    """After wheels are in the store, write their sha256 + source URL into the lock."""
    if not lock_path.exists():
        return
    data = json.loads(lock_path.read_text())
    by_name = {p["name"].lower().replace("_", "-"): p for p in data.get("pins", [])}
    for w in wheels:
        src = w.parent / "source.json"
        meta = json.loads(src.read_text()) if src.exists() else {}
        key = w.parent.parent.name  # store/<name>/<version>/<wheel>
        if key in by_name:
            by_name[key]["sha256"] = meta.get("sha256") or hashlib.sha256(w.read_bytes()).hexdigest()
            by_name[key]["url"] = meta.get("url")
            by_name[key]["file"] = w.name
    lock_path.write_text(json.dumps(data, indent=2) + "\n")


def verify_against_lock(lock_path: Path, wheels: list[Path]) -> list[str]:
    """Return mismatches between lock sha256 and store wheels (empty = ok)."""
    if not lock_path.exists():
        return []
    data = json.loads(lock_path.read_text())
    want = {p["name"].lower().replace("_", "-"): p.get("sha256") for p in data.get("pins", [])}
    bad = []
    for w in wheels:
        key = w.parent.parent.name
        exp = want.get(key)
        if exp and hashlib.sha256(w.read_bytes()).hexdigest() != exp:
            bad.append(f"{key}: lock {exp[:12]} != store")
    return bad


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def store_dir() -> Path:
    p = cache_dir() / "store"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "pyodex"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:  # noqa: S310
        while chunk := r.read(1 << 16):
            f.write(chunk)


def _pick_wheel(files: list[dict], abi: str = DEFAULT_ABI) -> dict | None:
    pure = [f for f in files if f["filename"].endswith("py3-none-any.whl")]
    wasm = [f for f in files if wheel_tag(abi) in f["filename"]]
    return (wasm or pure or [None])[0]


def _seal(path: Path) -> None:
    """Make a store entry read-only: files 0444, dirs 0555."""
    for p in path.rglob("*"):
        p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if p.is_dir() else 0))
    path.chmod(0o555)


def ensure_in_store(pin: Pin, *, pyodide_lock: dict | None = None, pyodide_version: str | None = None) -> Path:
    """Return the wheel path for `pin` inside the global store, downloading on miss.

    Sources, in order: Pyodide's own distribution (if the pinned version matches
    pyodide-lock.json), then PyPI (`pyodide_2025_0_wasm32` or `py3-none-any`).
    """
    entry = store_dir() / pin.key / pin.version
    if entry.exists():
        whl = next(entry.glob("*.whl"), None)
        if whl:
            return whl

    url = sha = None
    if pyodide_lock and pyodide_version:
        meta = pyodide_lock.get("packages", {}).get(pin.key) or pyodide_lock.get("packages", {}).get(pin.name)
        if meta and meta.get("version") == pin.version and meta["file_name"].endswith(".whl"):
            url = f"https://cdn.jsdelivr.net/pyodide/v{pyodide_version}/full/{meta['file_name']}"
            sha = meta.get("sha256")
            if not sha:
                # Fail closed: a Pyodide-CDN wheel with no digest in the lock
                # cannot be verified, so never accept it (would skip the
                # `if sha and ...` check below and land an unchecked wheel).
                raise RuntimeError(
                    f"{pin.name}=={pin.version}: pyodide-lock entry for "
                    f"{meta['file_name']} has no sha256; refusing unverified CDN wheel"
                )
    if not url:
        req = urllib.request.Request(PYPI_JSON.format(name=pin.name), headers={"User-Agent": "pyodex"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            meta = json.load(r)
        rel = meta["releases"].get(pin.version) or []
        f = _pick_wheel(rel, abi_from_lock(pyodide_lock) if pyodide_lock else DEFAULT_ABI)
        if not f:
            raise RuntimeError(f"{pin.name}=={pin.version}: no Pyodide-compatible wheel on PyPI")
        url, sha = f["url"], f["digests"]["sha256"]

    tmp = entry.with_suffix(".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    dest = tmp / url.rsplit("/", 1)[-1]
    print(f"store: fetching {pin.name}=={pin.version}")
    _download(url, dest)
    if sha and hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
        shutil.rmtree(tmp)
        raise RuntimeError(f"sha256 mismatch for {dest.name}")
    (tmp / "source.json").write_text(json.dumps({"url": url, "sha256": sha}))
    os.replace(tmp, entry)
    _seal(entry)
    return next(entry.glob("*.whl"))


def prune_store(*, keep: set[str] | None = None, dry: bool = False) -> list[Path]:
    """Delete store entries not in `keep` (set of 'name==version'). Returns removed paths."""
    import stat as _stat

    removed = []
    for name_dir in store_dir().iterdir():
        if not name_dir.is_dir():
            continue
        for ver_dir in name_dir.iterdir():
            key = f"{name_dir.name}=={ver_dir.name}"
            if keep and (key in keep or name_dir.name == "micropip"):
                continue
            removed.append(ver_dir)
            if not dry:
                for p in ver_dir.rglob("*"):
                    p.chmod(p.stat().st_mode | _stat.S_IWUSR)
                ver_dir.chmod(0o755)
                shutil.rmtree(ver_dir)
        if not dry and name_dir.exists() and not any(name_dir.iterdir()):
            name_dir.rmdir()
    return removed


# --------------------------------------------------------------------------- #
# Linking (copy-on-write)
# --------------------------------------------------------------------------- #


def link_cow(src: Path, dst: Path) -> str:
    """Place `src` at `dst` without a writable shared copy. Returns method used."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    # 1. reflink: true CoW on btrfs / xfs / apfs (GNU cp only; skip on Windows)
    if os.name == "posix" and shutil.which("cp"):
        r = subprocess.run(["cp", "--reflink=always", str(src), str(dst)], capture_output=True, check=False)
        if r.returncode == 0:
            return "reflink"
        if dst.exists():
            dst.unlink()
    # 2. hardlink: shares inode; store file is 0444 so writes fail with EACCES
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        pass
    # 3. plain copy, kept read-only
    shutil.copy2(src, dst)
    dst.chmod(0o444)
    return "copy"
