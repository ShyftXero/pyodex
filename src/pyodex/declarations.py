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

KNOWN = {"on_load", "watch_for", "on_event", "on_page", "urlmatch"}

# Characters that only make sense in a regular expression. A pattern containing
# one is treated as a regex; otherwise `://` means URL prefix, and anything else
# is a hostname.
REGEX_HINTS = set("?()[]|^$\\+")
URL_PREFIX_HINT = "://"


def classify(pattern: str) -> dict:
    """Turn one developer-facing pattern into a structured, predictable rule.

    Accepts, in the order a developer is likely to write them:

        127.0.0.1                  host, exact
        *.mysite.com               host, domain and its subdomains
        *                          any host
        https://some.site.com      URL prefix
        https?://.*\\.php          regex (explicit `re:` also works)
    """
    raw = pattern.strip()
    if not raw:
        raise ValueError("empty urlmatch pattern")
    if raw.startswith("re:"):
        return {"kind": "regex", "pattern": raw[3:]}
    if any(ch in REGEX_HINTS for ch in raw):
        return {"kind": "regex", "pattern": raw}
    if URL_PREFIX_HINT in raw:
        # `https://*` and `https://*.site.com/*` are wildcards, not literals.
        if "*" in raw:
            return {"kind": "regex", "pattern": _url_glob_to_regex(raw)}
        return {"kind": "prefix", "pattern": raw}
    return {"kind": "host", "pattern": raw.lower()}


def _url_glob_to_regex(pattern: str) -> str:
    """Escape a URL pattern and turn `*` into `.*` (anchored full match)."""
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch in ".+?^${}()|[]\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "^" + "".join(out) + "$"


@dataclass
class Declarations:
    on_load: bool = False
    triggers: list[dict] = field(default_factory=list)  # {"selector": ..., "url": ...}
    events: list[dict] = field(default_factory=list)  # {"event": ..., "selector": ...}
    page_bindings: list[dict] = field(default_factory=list)  # @on_page locators
    url_rules: list[dict] = field(default_factory=list)  # classified @urlmatch patterns
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "on_load": self.on_load,
            "triggers": self.triggers,
            "events": self.events,
            "page_bindings": self.page_bindings,
            "url_rules": self.url_rules,
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
    """Read declarations out of one or more source files.

    Decorators are collected per function, so `@urlmatch` and `@watch_for` on
    the same function combine (URL gate AND element present) instead of becoming
    two unrelated rules.
    """
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
            selectors: list[str] = []
            url_rules: list[dict] = []
            events_here: list[dict] = []

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
                elif name == "watch_for":
                    selector = args.get("selector") or args.get("arg0")
                    if not selector and args.get("id"):
                        selector = f"#{args['id']}"
                    if not selector:
                        out.problems.append(f"{path.name}: @watch_for needs a selector or id")
                        continue
                    selectors.append(selector)
                elif name == "urlmatch":
                    urls = args.get("arg0", args.get("urls", args.get("patterns")))
                    if isinstance(urls, str):
                        urls = [urls]
                    if not urls:
                        out.problems.append(f"{path.name}: @urlmatch needs a pattern or list")
                        continue
                    for pattern in urls:
                        try:
                            rule = classify(str(pattern))
                        except ValueError as e:
                            out.problems.append(f"{path.name}: @urlmatch: {e}")
                            continue
                        if rule not in url_rules:
                            url_rules.append(rule)
                elif name == "on_page":
                    event = args.get("event") or args.get("arg0")
                    if not event:
                        out.problems.append(f"{path.name}: @on_page needs an event name")
                        continue
                    entry = {"event": str(event), "fn": getattr(node, "name", ""),
                             "line": getattr(node, "lineno", 0)}
                    for key in ("selector", "testid", "role", "name", "text", "any_of"):
                        if key in args:
                            entry[key] = args[key]
                    out.page_bindings.append(entry)
                else:  # on_event
                    event = args.get("event") or args.get("arg0")
                    if not event:
                        out.problems.append(f"{path.name}: @on_event needs an event name")
                        continue
                    entry = {"event": str(event)}
                    if args.get("selector"):
                        entry["selector"] = args["selector"]
                    if args.get("prevent_default"):
                        entry["prevent_default"] = True
                    events_here.append(entry)

            for rule in url_rules:
                if rule not in out.url_rules:
                    out.url_rules.append(rule)
            for entry in events_here:
                if entry not in out.events:
                    out.events.append(entry)

            # One trigger per (selector | url-gate) combination declared here.
            if selectors:
                for selector in selectors:
                    trigger: dict = {"selector": selector}
                    if url_rules:
                        trigger["url_rules"] = url_rules
                    if trigger not in out.triggers:
                        out.triggers.append(trigger)
            elif url_rules:
                trigger = {"url_rules": url_rules}
                if trigger not in out.triggers:
                    out.triggers.append(trigger)
    return out


def summarise(d: Declarations) -> str:
    bits = []
    if d.on_load:
        bits.append("on_load")
    if d.triggers:
        bits.append(f"{len(d.triggers)} watch_for")
    if d.events:
        bits.append(f"{len(d.events)} on_event")
    if d.url_rules:
        kinds = ", ".join(sorted({r["kind"] for r in d.url_rules}))
        bits.append(f"{len(d.url_rules)} urlmatch ({kinds})")
    return ", ".join(bits) or "none"
