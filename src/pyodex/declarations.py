"""Static extraction of pyodex declarations from application source.

The entry script states what it needs, in Python, with decorators:

    from pyodex_runtime import on_load, watch_for, on_event, events

    @on_load
    def begin(): ...

    @watch_for("input[type=password]")
    def on_login_form(): ...

    @on_event(events.SUBMIT, selector="form")
    def on_submit(): ...

The build reads those declarations *without executing* the app (it cannot: the
app imports pyodide). Extraction is syntactic — decorator names and literal
arguments only — and anything it cannot resolve is reported, never dropped.

Parsing uses the AST rather than a regex so text inside docstrings and comments
cannot masquerade as a declaration.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

# Mirrors pyodex_runtime.events; attribute args like `events.CLICK` resolve here
# so extraction never has to import the app.
EVENT_NAMES = {
    "PAGE_LOAD": "page.load",
    "DOM_READY": "page.dom_ready",
    "NAVIGATE": "page.navigate",
    "CLICK": "page.click",
    "INPUT": "page.input",
    "SUBMIT": "page.submit",
    "FORM_FOUND": "page.form_found",
    "TAB_VISIBLE": "tab.visible",
    "TAB_HIDDEN": "tab.hidden",
}

KNOWN = {"on_load", "watch_for", "on_event"}


@dataclass
class Declarations:
    on_load: bool = False
    triggers: list[dict] = field(default_factory=list)  # {"selector": ..., "url": ...}
    events: list[dict] = field(default_factory=list)  # {"event": ..., "selector": ...}
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "on_load": self.on_load,
            "triggers": self.triggers,
            "events": self.events,
            "problems": self.problems,
        }


def _decorator_name(node: ast.AST) -> str | None:
    """`on_load`, `pyodex.on_load`, and `watch_for("x")` all resolve to a name."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _value(node: ast.AST):
    if isinstance(node, ast.Attribute) and node.attr in EVENT_NAMES:
        return EVENT_NAMES[node.attr]  # events.CLICK
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise ValueError(f"non-literal decorator argument: {ast.unparse(node)}") from None


def _args(call: ast.Call) -> dict:
    out: dict = {}
    for i, arg in enumerate(call.args):
        out[f"arg{i}"] = _value(arg)
    for kw in call.keywords:
        if kw.arg is not None:
            out[kw.arg] = _value(kw.value)
    return out


def extract(paths: list[Path]) -> Declarations:
    """Read declarations out of one or more source files."""
    out = Declarations()
    for path in paths:
        try:
            src = path.read_text()
        except OSError as e:
            out.problems.append(f"{path.name}: {e}")
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            out.problems.append(f"{path.name}: cannot parse ({e.msg})")
            continue

        for node in ast.walk(tree):
            decorators = getattr(node, "decorator_list", None)
            if not decorators:
                continue
            for dec in decorators:
                name = _decorator_name(dec)
                if name not in KNOWN:
                    continue
                call = dec if isinstance(dec, ast.Call) else None
                try:
                    args = _args(call) if call else {}
                except ValueError as e:
                    out.problems.append(f"{path.name}: @{name}: {e}")
                    continue

                if name == "on_load":
                    out.on_load = True
                    continue

                # @watch_for("sel") -> positional; @on_event(E, selector="s") -> kwarg only
                selector = args.get("selector") or args.get("arg0")
                url = args.get("url")

                if name == "watch_for":
                    if not selector and args.get("id"):
                        selector = f"#{args['id']}"
                    if not selector:
                        out.problems.append(f"{path.name}: @watch_for needs a selector or id")
                        continue
                    entry = {"selector": selector}
                    if url:
                        entry["url"] = url
                    if entry not in out.triggers:
                        out.triggers.append(entry)
                else:  # on_event
                    event = args.get("event") or args.get("arg0")
                    if not event:
                        out.problems.append(f"{path.name}: @on_event needs an event name")
                        continue
                    entry = {"event": str(event)}
                    if args.get("selector"):
                        entry["selector"] = args["selector"]
                    if entry not in out.events:
                        out.events.append(entry)
    return out


def summarise(d: Declarations) -> str:
    bits = []
    if d.on_load:
        bits.append("on_load")
    if d.triggers:
        bits.append(f"{len(d.triggers)} watch_for")
    if d.events:
        bits.append(f"{len(d.events)} on_event")
    return ", ".join(bits) or "none"
