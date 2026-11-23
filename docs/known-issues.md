# Known issues and deliberate deviations

## micropip installs nothing when the lockfile path is taken

**Symptom.** From an extension page (offline-first bundle), `await micropip.install("six")`
returns cleanly, `micropip.list()` is unchanged, nothing is importable. The failure
surfaces later as `ModuleNotFoundError` in unrelated code.

**Cause (micropip 0.11.1, as shipped with Pyodide 314.0.8).**
`Transaction.__post_init__` (transaction.py:54) chooses resolution order:

```python
self.search_pyodide_lock_first = (self.index_urls == package_index.DEFAULT_INDEX_URLS)
```

With no explicit `index_urls`, the lockfile is searched first. For a package that
is in `pyodide-lock.json`, `_add_requirement_from_pyodide_lock()` returns True and
the requirement is added to `transaction.pyodide_packages`. `install()` then calls
`loadPackage(...)`, which — when the lockfile base URL is not reachable, as in a
browser extension — fails without raising. `install()` therefore completes
"successfully" with nothing installed.

Passing an explicit `index_urls` changes the *order*, not just the index set, so
the wheel is fetched from PyPI and installed normally. This is not obvious from
the docstring; it is why our workaround works.

**Our fix (`pyodex_natural.pip_install`).**
1. Always pass `index_urls=["https://pypi.org/simple/"]`, which selects the
   index-first path and then falls back to the lockfile for wasm packages — the
   order we actually want.
2. Verify afterwards with `importlib.util.find_spec`, raising a clear error if a
   package is not importable. A silent no-op is worse than a slow path.

Both are locked by `tests/test_core.py::test_pip_install_pins_index_and_verifies`,
so the guard cannot be tidied away as redundant.

**Upstream.** Reported as
[pyodide#6569](https://github.com/pyodide/pyodide/issues/6569). Another contributor has
claimed the fix; if it stalls we will open a PR (proposed shape: verify after
`loadPackage`, fall back to the index, and fail loudly if neither works).

## Socket capability probe must not guess

Pyodide implements `socket` for Node only (`useNodeSockFS()`). Probing with
`connect_ex` false-positived in Chrome and disabled the shim, so detection now
decides from the environment first (`process` present, `window` absent) and only
then confirms.

## Reflink is best-effort

`link_cow` prefers reflink, then hardlink, then copy. Building into `/tmp` while
the store lives in `~/.cache` crosses filesystems, so builds there report `copy`.

## Gate flake (29/30 → 30/30 wobble) — not reproduced 2026-11-23 (T2)

**History.** Earlier sessions saw the gate drop one level on consecutive runs under
`--jobs 10` (suspect L23 dNR live-fetch or L29 computed-style read).

**T2 sweep.** 4 full runs: 3× `--jobs 10` + 1× `--jobs 1`. All **30/30**. Flake did not
reproduce.

**Why likely gone.** L23 already retries the blocked fetch 20× with `cache: 'no-store'`
(rule application lags the `updateDynamicRules` promise under parallel load). That guard
is in place and held across all 4 runs.

**Status.** Not reproduced, so not newly fixed — a timing flake that doesn't fire isn't
proof it's dead. If it returns, bisect L23 vs L29 by running each solo 10× at `--jobs 10`
and watching for the one assertion that occasionally reads stale state. Logs from this
sweep: `/tmp/t2/run{1..4}.log` (ephemeral).
