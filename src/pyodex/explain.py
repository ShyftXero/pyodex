"""What the build decided, with the line that decided it.

`pyodex explain` exists because inference without a receipt is indistinguishable
from magic: `main.html` becomes manifest entries, handler names become bindings,
`chrome.*` mentions become permissions. This prints that mapping and, unlike the
build, reports problems instead of stopping.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from . import pagecheck
from .builder import PAGE_BODY_NAMES, ExtConfig, _derived_permissions, resolve_project
from .declarations import extract


def _sources(project: Path, cfg: ExtConfig) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for name in [cfg.entry, *cfg.python_files]:
        path = project / name
        if path.is_file():
            try:
                out.append((path, path.read_text()))
            except OSError:
                continue
    return out


def _page(project: Path, cfg: ExtConfig, sources: list[tuple[Path, str]]) -> dict:
    raw = cfg.overlay_body
    file_name = "pyodex.toml (inline)" if raw else None
    if not raw:
        for name in PAGE_BODY_NAMES:
            path = project / name
            if path.is_file():
                raw = path.read_text()
                file_name = name
                break
    if not raw:
        return {"file": None, "ids": [], "bindings": [], "problems": [], "warnings": []}

    rewritten, warnings, errors = pagecheck.rewrite(raw)
    facts = pagecheck.scan(rewritten)
    return {
        "file": file_name,
        "ids": sorted(facts.ids),
        "duplicate_ids": sorted({i for i, lines in facts.ids.items() if len(lines) > 1}),
        "bindings": [
            {"event": b.event, "modifiers": list(b.mods), "handler": b.name, "line": b.line}
            for b in facts.bindings
        ],
        "problems": errors + pagecheck.check(rewritten, sources),
        "warnings": warnings,
    }


def report(target: Path | str) -> dict:
    """Everything the build would decide, as plain data."""
    # resolve_project prints its boot-policy reasoning; the receipt is the output
    # here, and --json has to stay parseable.
    with contextlib.redirect_stdout(io.StringIO()):
        project, cfg = resolve_project(Path(target))
    sources = _sources(project, cfg)
    decls = extract([path for path, _src in sources])
    page = _page(project, cfg, sources)
    return {
        "name": cfg.name,
        "slug": cfg.slug,
        "entry": cfg.entry,
        "page": page,
        "handlers": sorted(pagecheck.handlers_in(sources)),
        "page_bindings": decls.page_bindings,
        "events": decls.events,
        "triggers": decls.triggers,
        "boot": cfg.boot,
        "matches": cfg.matches,
        "runtime": {"tier": cfg.runtime or "pyodide", "default": not cfg.runtime},
        "permissions": _derived_permissions(project, cfg),
        "problems": page["problems"] + decls.problems,
        "warnings": page["warnings"],
    }


def render(data: dict) -> str:
    """The human form: one line per decision, each pointing at its source."""
    lines: list[str] = [f'{data["name"]}  ({data["slug"]})', ""]
    page = data["page"]

    def row(label: str, value: str) -> None:
        lines.append(f"{label:<11} {value}")

    if page["file"]:
        row("markup", page["file"])
    for binding in page["bindings"]:
        mods = "".join(f".{m}" for m in binding["modifiers"])
        row("bindings", f'{page["file"]}:{binding["line"]:<4} pyodex-{binding["event"]}{mods}'
                        f'  -> {binding["handler"]}')
    row("handlers", ", ".join(data["handlers"]) or "none")
    if page["ids"]:
        shown = ", ".join(page["ids"][:12]) + (" …" if len(page["ids"]) > 12 else "")
        row("elements", f'{len(page["ids"])} id(s): {shown}')

    if data["page_bindings"]:
        for binding in data["page_bindings"]:
            where = ", ".join(
                f"{k}={v}" for k, v in binding.items() if k in ("selector", "testid", "role", "name", "text")
            ) or "any_of"
            row("page", f'{binding["event"]}  {where}  -> {binding["fn"]}  (line {binding["line"]})')
    else:
        row("page", "no @on_page bindings")

    if data["triggers"]:
        for trigger in data["triggers"]:
            row("trigger", json.dumps(trigger))
    row("boot", data["boot"])
    row("matches", ", ".join(data["matches"]))
    tier = data["runtime"]["tier"] + (" (default)" if data["runtime"]["default"] else "")
    row("runtime", tier)
    row("manifest", ", ".join(data["permissions"]) or "none")

    for warning in data["warnings"]:
        lines.append(f"warning: {warning}")
    for problem in data["problems"]:
        lines.append(f"problem: {problem}")
    return "\n".join(lines)
