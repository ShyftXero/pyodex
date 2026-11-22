"""Runtime tiers.

A pyodex app does not have to run on Pyodide. The tier list is ordered cheapest
first, and the build picks the first tier that can satisfy what the app declared.
Developers can reorder or narrow the list; the default is the one we recommend.

    [extension]
    runtimes = ["micropython", "rustpython", "pyodide"]   # default order
    runtime = "micropython"                               # force one

Why tiers at all: a tab that only needs to notice a login form should not pay
55 MB for CPython. A tier is a *capability* claim, not just a size, so selection
is mechanical: an app that lists dependencies, or needs a full stdlib, cannot be
put on MicroPython, and the build says so instead of failing at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Capability vocabulary. Keep it small and concrete; a tier either has it or not.
DOM = "dom"  # can touch the page's DOM in-realm
PACKAGES = "packages"  # can install/load third-party packages
STDLIB_FULL = "stdlib-full"  # has enough stdlib that odd code just works
SOCKETS = "sockets"  # stdlib socket semantics (via our proxy shim)
CRYPTO = "crypto"  # hashlib/hmac class crypto available
WASM = "wasm"  # runs compiled extensions


@dataclass(frozen=True)
class Runtime:
    name: str
    approx_mb: int
    capabilities: frozenset[str]
    # Regexes for blob URLs, used by the page-level cache so *any* page running
    # this runtime benefits from our cache (not just our own extension).
    cdn_patterns: tuple[str, ...] = ()
    # Loader file shipped with the extension; "" means nothing is bundled.
    loader: str = ""
    implemented: bool = False
    notes: str = ""


RUNTIMES: dict[str, Runtime] = {
    "micropython": Runtime(
        name="micropython",
        approx_mb=2,
        capabilities=frozenset({DOM, SOCKETS, CRYPTO}),
        cdn_patterns=(
            r"/micropython(?:-wasm)?/.*\.(?:wasm|mjs|js)$",
            r"/micropython/[^/]+/ports/webassembly/.*\.(?:wasm|js)$",
        ),
        loader="micropython/micropython.mjs",
        implemented=True,
        notes="tiny; subset of the language; no third-party packages",
    ),
    "rustpython": Runtime(
        name="rustpython",
        approx_mb=5,
        capabilities=frozenset({DOM, SOCKETS, CRYPTO, WASM}),
        cdn_patterns=(
            r"/rustpython.*\.(?:wasm|js)$",
            r"/rustpython/.*/rustpython\.wasm$",
        ),
        loader="rustpython/rustpython_wasm.js",
        implemented=True,
        notes="middle ground: wider stdlib than MicroPython, no CPython fidelity",
    ),
    "brython": Runtime(
        name="brython",
        approx_mb=2,
        capabilities=frozenset({DOM, CRYPTO}),
        cdn_patterns=(r"/brython(@[^/]+)?/brython(?:_stdlib)?(\.min)?\.js$",),
        loader="brython/brython.min.js",
        implemented=True,
        notes="Python implemented in JS; real DOM, no wasm, subset of stdlib",
    ),
    "pyodide": Runtime(
        name="pyodide",
        approx_mb=55,
        capabilities=frozenset({DOM, PACKAGES, STDLIB_FULL, SOCKETS, CRYPTO, WASM}),
        cdn_patterns=(
            r"/pyodide/v?\d+\.\d+\.\d+/full/[^/?#]+$",
            r"/pyodide/v?\d+\.\d+\.\d+/pyodide-lock\.json$",
        ),
        loader="pyodide/pyodide.mjs",
        implemented=True,
        notes="CPython compiled to wasm; the only tier with real package support",
    ),
}

DEFAULT_ORDER: tuple[str, ...] = ("micropython", "brython", "rustpython", "pyodide")


def order(names: list[str] | None) -> list[Runtime]:
    """Resolve a developer-supplied ordering (names, cheapest first)."""
    chosen = list(names or DEFAULT_ORDER)
    unknown = [n for n in chosen if n not in RUNTIMES]
    if unknown:
        raise ValueError(f"unknown runtime(s) {unknown}; known: {sorted(RUNTIMES)}")
    return [RUNTIMES[n] for n in chosen]


def requirements_for(*, dependencies: list[str], packages: list[str], sockets: bool,
                     crypto: bool, full_stdlib: bool) -> set[str]:
    """Turn what an app declared into the capabilities its tier must have."""
    needs = {DOM}
    if dependencies or packages:
        needs.add(PACKAGES)
    if full_stdlib:
        needs.add(STDLIB_FULL)
    if sockets:
        needs.add(SOCKETS)
    if crypto:
        needs.add(CRYPTO)
    return needs


def choose(needs: set[str], names: list[str] | None = None) -> Runtime:
    """First tier in `names` (cheapest first) that satisfies `needs`."""
    for rt in order(names):
        if needs <= rt.capabilities:
            return rt
    raise ValueError(
        f"no runtime in {[r.name for r in order(names)]} satisfies {sorted(needs)}; "
        "add pyodide or drop the requirement"
    )


def cache_patterns(names: list[str] | None = None) -> list[str]:
    """Every runtime's blob regexes — caching is for other pages too, so we keep
    patterns for tiers this app does not use."""
    out: list[str] = []
    for rt in (*order(names), *RUNTIMES.values()):
        for pat in rt.cdn_patterns:
            if pat not in out:
                out.append(pat)
    return out


@dataclass
class TierPlan:
    """What the build decided, and why."""

    runtime: Runtime
    needs: set[str] = field(default_factory=set)
    considered: list[str] = field(default_factory=list)

    def describe(self) -> str:
        skipped = [n for n in self.considered if n != self.runtime.name]
        reason = f"needs {sorted(self.needs)}"
        if skipped:
            reason += f"; skipped {skipped} (missing capabilities)"
        return f"runtime={self.runtime.name} ({self.runtime.approx_mb} MB, {reason})"
