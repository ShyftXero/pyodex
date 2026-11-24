"""`pyodex dev`: rebuild in place, so the browser reloads without a reinstall.

The one thing this must not do is delete the output directory. Chrome treats a
recreated unpacked directory as a different extension — new id, lost storage, a
second "Load unpacked". So the build writes to a temporary directory and the
result is synced over `dist/` file by file, leaving the directory itself alone.

No dev server and no websocket are injected into the page: MV3 forbids remote
code, and an extension that ships its own HMR runtime is exactly that. Chrome's
own reload of an unpacked extension does the work.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from .builder import build_extension, resolve_project
from .explain import render, report

IGNORED = {"dist", ".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}


def snapshot(project: Path) -> tuple[tuple[str, int, int], ...]:
    """Every source file's (path, mtime_ns, size), for change detection."""
    entries: list[tuple[str, int, int]] = []
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project)
        if rel.parts and rel.parts[0] in IGNORED:
            continue
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((str(rel), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def sync(source: Path, target: Path) -> None:
    """Copy `source` over `target` without removing `target` itself."""
    target.mkdir(parents=True, exist_ok=True)
    wanted: set[str] = set()
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        wanted.add(str(rel))
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            # Wheels are linked read-only from the store; copying over one needs
            # the write bit first.
            try:
                destination.chmod(0o644)
            except OSError:
                pass
        shutil.copy2(path, destination)
    # Remove what this build no longer produces, but never the directory.
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
            continue
        if str(path.relative_to(target)) not in wanted:
            path.unlink()


def rebuild(project: Path, out: Path | None = None) -> Path:
    """Build to a scratch directory, then sync it into the real output directory."""
    _project, cfg = resolve_project(project)
    target = out or (project / "dist" / cfg.slug)
    scratch = Path(tempfile.mkdtemp(prefix="pyodex-dev-"))
    try:
        built = build_extension(project, scratch / cfg.slug)
        sync(built, target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return target


def watch(project: Path, out: Path | None = None, *, interval: float = 0.5, once: bool = False) -> int:
    project = project.resolve()
    target = rebuild(project, out)
    print(render(report(project)))
    print()
    print(f"built {target}")
    print(f"load it once: chrome://extensions -> Developer mode -> Load unpacked -> {target}")
    if once:
        return 0

    state = snapshot(project)
    print(f"watching {len(state)} file(s); Ctrl-C to stop")
    try:
        while True:
            time.sleep(interval)
            current = snapshot(project)
            if current == state:
                continue
            state = current
            try:
                rebuild(project, out)
                print(f"[{time.strftime('%H:%M:%S')}] rebuilt {target}")
            except Exception as exc:  # noqa: BLE001 - keep watching through a bad edit
                print(f"[{time.strftime('%H:%M:%S')}] error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
