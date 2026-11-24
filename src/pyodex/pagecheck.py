"""What the page says it does, checked before it ships.

Two jobs, both static:

1. Rewrite the `on*` sugar (`onclick="connect"`) into the canonical binding
   (`pyodex-click="connect"`). The value is a handler *name*, never code — MV3
   forbids inline JavaScript, so an expression is rejected rather than shipped.
2. Cross-check the page against the Python: every bound handler exists, every
   `ext_page` reference names a real id, and no id is declared twice.

Both run at build time because the same mistake at runtime is a silent no-op:
a typo'd handler warns in a console nobody is reading, and an inline handler is
blocked by CSP with a message that does not mention pyodex.
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

CANONICAL = "pyodex-"

# The attribute is data, not code: a name, optionally called with (event) or
# (this), optionally followed by the old `return false` idiom.
_HANDLER = re.compile(
    r"""^\s*
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*
        (?:\(\s*(?P<args>(?:event|this)(?:\s*,\s*(?:event|this))*)?\s*\))?
        \s*$
    """,
    re.VERBOSE,
)
_RETURN_FALSE = re.compile(r"^(?P<prefix>.*?)\s*;\s*return\s+false\s*$", re.IGNORECASE | re.DOTALL)
_BARE_RETURN_FALSE = re.compile(r"^\s*return\s+false\s*$", re.IGNORECASE)
_ATTRIBUTE = re.compile(
    r"""(?P<space>\s)(?P<name>on[a-zA-Z]+)(?P<eq>\s*=\s*)(?P<quote>["'])(?P<value>[^"']*)(?P=quote)"""
)

# Regions whose text is content, not markup: an `onclick="x"` inside them is an
# example or a comment, not an attribute to rewrite. `script`/`style`/`textarea`
# are raw-text/escapable raw-text per the HTML spec; `pre`/`code` are where
# documentation shows a handler literally. Non-greedy to the first close so
# adjacent blocks stay independent; an unclosed block is left to the live sub.
_PROTECTED = re.compile(
    r"""(?:
        <!--.*?-->
      | <(?P<tag>pre|code|script|style|textarea|title)\b[^>]*>.*?</(?P=tag)\s*>
    )""",
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)

# A rewritten attribute is still a name; these need a warning because the event
# they imply is not the one the author means.
_WARN_ON = {
    "onkeypress": "@{attr} is deprecated; keydown is the event every browser still sends",
    "onload": "onload fires when this element's resource resolves, not when the app is ready"
    " (for the page itself use @start)",
    "onerror": "onerror fires only if the resource fails; it can fire before Python is running",
}
_REJECT_ON = {"onreadystatechange": "onreadystatechange is a legacy XHR idiom with no element mapping"}


class Binding:
    __slots__ = ("event", "line", "mods", "name")

    def __init__(self, event: str, mods: tuple[str, ...], name: str, line: int) -> None:
        self.event, self.mods, self.name, self.line = event, mods, name, line

    def __repr__(self) -> str:
        mods = "".join(f".{m}" for m in self.mods)
        return f"<Binding {self.event}{mods}={self.name!r} line {self.line}>"


@dataclass
class PageFacts:
    ids: dict[str, list[int]] = field(default_factory=dict)  # id -> line numbers
    bindings: list[Binding] = field(default_factory=list)


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.facts = PageFacts()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        for name, value in attrs:
            if name == "id" and value:
                self.facts.ids.setdefault(value, []).append(line)
            elif name.startswith(CANONICAL):
                event, *mods = name[len(CANONICAL):].split(".")
                if event and value:
                    self.facts.bindings.append(
                        Binding(event, tuple(mods), value.strip(), line)
                    )


def scan(body: str) -> PageFacts:
    scanner = _Scanner()
    scanner.feed(body)
    scanner.close()
    return scanner.facts


def parse_on_value(raw: str) -> tuple[str, tuple[str, ...]]:
    """`connect`/`connect()`/`connect(event); return false` -> (name, modifiers)."""
    text = raw.strip()
    if not text:
        raise ValueError("empty handler")
    if _BARE_RETURN_FALSE.match(text):
        raise ValueError("`return false` needs a handler name before it")
    mods: tuple[str, ...] = ()
    match = _RETURN_FALSE.match(text)
    if match:
        text = match.group("prefix").strip()
        # jQuery's meaning: stop the default action *and* the bubble.
        mods = ("prevent", "stop")
    if not text:
        raise ValueError("empty handler")
    handler = _HANDLER.match(text)
    if not handler:
        if "javascript:" in text:
            raise ValueError("the javascript: pseudo-scheme is not a handler")
        if "(" in text and ")" in text:
            raise ValueError("handler arguments may only be `event` or `this`")
        if ";" in text:
            raise ValueError("only one handler name is allowed")
        raise ValueError("not a function name")
    return handler.group("name"), mods


def rewrite(body: str) -> tuple[str, list[str], list[str]]:
    """Return (rewritten body, warnings, errors). Errors mean the build must stop."""
    warnings: list[str] = []
    errors: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name").lower()
        raw = match.group("value")
        if name in _REJECT_ON:
            errors.append(f'<{name}="{raw}"> — {_REJECT_ON[name]}')
            return match.group(0)
        try:
            handler, mods = parse_on_value(raw)
        except ValueError as exc:
            errors.append(
                f'<{name}="{raw}"> — handlers must be a function name, not JavaScript ({exc}).\n'
                f'    point it at a @handler function, e.g. {name}="connect"'
            )
            return match.group(0)
        if name in _WARN_ON:
            warnings.append(_WARN_ON[name].format(attr=name))
        suffix = "".join(f".{m}" for m in mods)
        quote = match.group("quote")
        return f'{match.group("space")}{CANONICAL}{name[2:]}{suffix}={quote}{handler}{quote}'

    # Rewrite only the text outside raw-text/comment regions; the regions
    # themselves are copied through byte-for-byte.
    out: list[str] = []
    last = 0
    for region in _PROTECTED.finditer(body):
        out.append(_ATTRIBUTE.sub(replace, body[last:region.start()]))
        out.append(region.group(0))
        last = region.end()
    out.append(_ATTRIBUTE.sub(replace, body[last:]))
    rewritten = "".join(out)
    return rewritten, warnings, errors


def _sources(project: Path, entry: str, python_files: list[str]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for name in [entry, *python_files]:
        path = project / name
        if path.is_file():
            try:
                out.append((path, path.read_text()))
            except OSError:
                continue
    return out


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def handlers_in(sources: list[tuple[Path, str]]) -> dict[str, int]:
    """Every name `@handler` registers, with the line it was declared on."""
    found: dict[str, int] = {}
    for _path, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            for dec in getattr(node, "decorator_list", []):
                if _decorator_name(dec) != "handler":
                    continue
                name = None
                if isinstance(dec, ast.Call) and dec.args:
                    arg = dec.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        name = arg.value
                else:
                    name = getattr(node, "name", None)
                if name:
                    found.setdefault(name, getattr(node, "lineno", 0))
    return found


# The runtime object `ext_page` is `_ExtensionPage` in
# runtime/pyodex.py; every public method there is an API call, and
# `__getattr__` turns any *other* name into an element-id lookup. We read that
# surface straight from the source instead of hardcoding it, so adding a method
# on the runtime side cannot silently turn into a "missing id" here.
# Keep the paired note on `_ExtensionPage` if this coupling ever changes.
_RUNTIME_SRC = Path(__file__).with_name("runtime") / "pyodex.py"
_DEFAULT_EXT_PAGE_METHODS = frozenset({"get", "get_all", "maybe"})


def _ext_page_methods() -> frozenset[str]:
    """Public method names on the runtime `_ExtensionPage`, read from source.

    AST, not import: the runtime module needs a live JS bridge to load, which
    the build host does not have. A fallback keeps the checker working if the
    source ever moves out of reach.
    """
    try:
        tree = ast.parse(_RUNTIME_SRC.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return _DEFAULT_EXT_PAGE_METHODS
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_ExtensionPage":
            names = {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            }
            if names:
                return frozenset(names)
    return _DEFAULT_EXT_PAGE_METHODS


def ext_page_refs(sources: list[tuple[Path, str]]) -> list[tuple[str, Path, int]]:
    """Selectors and attribute names the Python asks `ext_page` for."""
    # A public method is an API call; anything else is an element id via
    # `_ExtensionPage.__getattr__`. Both come from the runtime class itself.
    methods = _ext_page_methods()
    known = methods
    refs: list[tuple[str, Path, int]] = []
    for path, src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                func = node.func
                if (isinstance(func.value, ast.Name) and func.value.id == "ext_page"
                        and func.attr in methods and node.args):
                    arg = node.args[0]
                    # Only `#id` literals are statically checkable; a CSS selector
                    # is a legitimate runtime lookup.
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and arg.value.startswith("#")):
                        refs.append((arg.value, path, node.lineno))
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id != "ext_page" or node.attr in known:
                    continue
                if node.attr.startswith("__"):
                    continue
                refs.append((node.attr, path, node.lineno))
    # Selector forms found above are `#id`; anything else is a CSS selector and
    # is not statically checkable without a selector engine.
    return refs


def _selector_id(ref: str) -> str | None:
    """`#some-id` is an id, not a stylesheet: ids may contain hyphens."""
    if ref.startswith("#") and len(ref) > 1 and not any(ch.isspace() for ch in ref):
        return ref[1:]
    return None


def _attribute_id(ref: str) -> str:
    return ref.rstrip("_").replace("_", "-")


def check(body: str, sources: list[tuple[Path, str]]) -> list[str]:
    """Problems worth failing the build over, each naming a file and a line."""
    facts = scan(body)
    handlers = handlers_in(sources)
    problems: list[str] = []

    for name, lines in facts.ids.items():
        if len(lines) > 1:
            where = " and ".join(str(n) for n in lines)
            problems.append(
                f'duplicate id "{name}" on lines {where} — '
                "an id is how the Python finds one element; keep it unique"
            )

    for binding in facts.bindings:
        if not handlers:
            problems.append(
                f'main.html:{binding.line} — pyodex-{binding.event}="{binding.name}" '
                "but no @handler is declared in Python"
            )
            continue
        if binding.name in handlers:
            continue
        close = difflib.get_close_matches(binding.name, handlers, n=1)
        hint = f'\n  Did you mean "{close[0]}"?' if close else ""
        declared = ", ".join(sorted(handlers))
        problems.append(
            f'main.html:{binding.line} — pyodex-{binding.event}="{binding.name}": '
            f'no handler named "{binding.name}". Declared: {declared}.{hint}'
        )

    ids = set(facts.ids)
    for ref, path, line in ext_page_refs(sources):
        selector = _selector_id(ref)
        wanted = selector if selector is not None else _attribute_id(ref)
        if wanted in ids:
            continue
        close = difflib.get_close_matches(wanted, sorted(ids), n=1)
        hint = f'\n  Did you mean "{close[0]}"?' if close else ""
        shown = ref if selector is not None else f"ext_page.{ref}"
        problems.append(
            f"{path.name}:{line} — {shown}: no element id {wanted!r} in main.html.{hint}"
        )

    return problems


def prepare(body: str, project: Path, entry: str, python_files: list[str]) -> tuple[str, list[str]]:
    """Rewrite the sugar, then validate. Returns (body, problems)."""
    rewritten, warnings, errors = rewrite(body)
    for warning in warnings:
        print(f"warning: {warning}")
    problems = [*errors, *check(rewritten, _sources(project, entry, python_files))]
    return rewritten, problems
