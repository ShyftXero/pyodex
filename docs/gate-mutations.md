# Gate mutation log (T1)

Each gate level is only evidence if it **fails when the feature is removed**. This table
records, per level: the mutation applied, where, and whether the level turned red. A level
that stays green under a plausible mutation is a lying gate.

Method: apply one surgical break, run `uv run tests/chrome_harness.py <L>`, confirm the
level fails on the intended assertion, `git checkout` the file. Demo levels (L14–L29) are
mutated in their own `examples/demos/*/main.py` (isolated per build); plumbing levels
(L1–L13) in the shared runtime/templates.

Status: `caught` = level went red on the intended assertion. `LYING` = stayed green.

| Level | Feature asserted | Mutation | Where | Result |
|---|---|---|---|---|
| L1 | Pyodide boot + caps (jspi) | force `caps["jspi"] = False` | `runtime/pyodex_natural.py:74` | caught — `jspi available` red |
| L2 | async cross-origin HTTP (fetch_json) | `raise` in `fetch_json` | `runtime/pyodex_runtime.py:1533` | caught — `cross-origin HTTP worked` red |
| L3 | IDBFS persistence across reload | `save()` no-op | `runtime/pyodex_natural.py:599` | caught — `state survived reload` red |
| L4.1 | uncaught traceback surfacing | `showError` early-return | `templates/bootstrap.js:20` | caught — `traceback surfaced` red |
| L4.2 | raw TCP/TLS via proxy socket shim | `_ProxySocket.connect` raise | `runtime/pyodex_natural.py:188` | caught — `tcp/tls works` red |
| L4.3 | runtime micropip install | `pip_install_async` raise | `runtime/pyodex_natural.py:695` | caught — `runtime pip install worked` red |
| L5 | blob cache serve-from-cache | force cache-miss in `cachedFetch` | `templates/cache.js:132` | caught — `reload served from cache` red |
| L6 | page-fetch cache intercept | `candidate = false` in page fetch shim | `templates/pagemain.js:56` | caught — `second page served from cache` red |
| L7 | remote runtime via cache | force cache-miss in `cachedFetch` | `templates/cache.js:132` | caught — `wasm was not re-fetched` red |
| L8 | shared interpreter (one bootId) | randomize `bootId` per status | `templates/engine.js:27` | caught — `both tabs share one interpreter` red |
| L9 | engine fills tab via on_attach | skip `_engine_attached` in `attach()` | `templates/engine_worker.js:78` | caught — `engine filled the tab` red. NOTE: first mutated `engine.js` (offscreen) — stayed green because offscreen never executes in test Chromium (confirms H2). Real seam is `engine_worker.js` (SharedWorker). |
| L10 | compat probes pass in Chrome V8 | corrupt numpy probe assertion | `compat.py:77` | caught — `every probed package passes in Chrome` red |
| L11 | MicroPython sha256 | prefix junk in `sha256_hex` | `runtime/pyodex_mp.py:90` | caught — `sha256 works on the small tier` red |
| L12 | RustPython DOM via js_vars | `set_attr` no-op | `runtime/pyodex_rp.py` | caught — `reached the page through js_vars` red |
| L13 | RustPython no-sandbox DOM via js_vars (experimental) | `set_attr` no-op | `runtime/pyodex_rp.py` | caught — `reached the DOM from a normal page` red (run `--experimental`) |
| L14 | demo01 click increments badge | `clicks += 0` | `demos/01-minimal-ui/main.py` | caught — `click handler updated badge` red |
| L15 | demo02 cheapest = min price | `min`→`max` | `demos/02-price-compare/main.py` | caught — `cheapest is the lower price` red |
| L16 | demo03 lookalike detection | `assess` returns None | `demos/03-anti-phishing/main.py` | caught — `digit-substitution domain caught` red |
| L17 | demo04 password reversed-at-rest | `scramble` identity | `demos/04-password-manager/main.py` | caught — `stores the password reversed` red |
| L18 | demo05 shared cross-tab counter | skip `seen.append` | `demos/05-tab-watcher/main.py` | caught — `counts 1 tab` + `count of 2` red |
| L19 | demo06 chrome.storage round-trip | `_StorageArea.set` no-op | `runtime/pyodex_runtime.py:937` | caught — `storage.local round-trip` red |
| L20 | demo07 browser event delivery | `_dispatch_browser_event` early-return | `runtime/pyodex_runtime.py:997` | caught — `event delivered to Python` red |
| L21 | demo08 chrome.alarms.create | `_ChromeAlarms.create` no-op | `runtime/pyodex_runtime.py:1191` | caught — `alarm registered` + `onAlarm reached Python` red |
| L22 | demo09 cookie round-trip | `cookies.set` stores wrong value | `runtime/pyodex_runtime.py:1141` | caught — `cookie round-trip` red |
| L23 | demo10 dNR rule install/block | `updateDynamicRules` sends empty `addRules` | `runtime/pyodex_runtime.py:1093` | caught — `dynamic rule installed` + `rule blocks a real request` red |
| L24 | demo11 popup.py renders popup | status string changed | `demos/11-popup/popup.py` | caught — `popup rendered by Python` red |
| L25 | demo12 wasm memory write | `WasmMemory.write` no-op | `runtime/pyodex_runtime.py:1307` | caught — `memory is addressed like bytes` red |
| L26 | demo13 page-canvas script reinjection | skip `<script>` re-create in `append_html` | `templates/injector.js:55` | caught — `canvas is not blank` red |
| L27 | demo14 wasm export trace recording | skip `_recorder.records.append` | `runtime/pyodex_runtime.py:1330` | caught — `every export call recorded` red |
| L28 | demo15 reading-mode word count | `words + 7` | `demos/15-reading-mode/main.py` | caught — `word count matches the page` red |
| L29 | demo16 cls drops inline bg (CSS wins) | `panel()` always keeps full inline defaults | `runtime/pyodex_runtime.py:514` | caught — `class-based styling wins` + `stylesheet radius applied` red |

## Result

**All 30 levels proven to fail under a plausible mutation** (29 default + L13
experimental). No lying gate found. Every level's headline assertion goes red when the
feature it guards is broken, then returns green on revert.

Each mutation was reverted immediately; the tree is clean. Mutations were tagged
`MUTATION-T1-<L>` so a stray one is greppable.

### One finding worth keeping (H2 confirmed)

L9's first mutation stayed **green** — but that was a wrong-seam error, not a gate lie.
`ui="engine"` with `engine="shared"` runs in a **SharedWorker** (`engine_worker.js`), while
`engine.js` is the **offscreen** document that never executes in the test Chromium
(stable Chrome ignores `--load-extension`; offscreen docs don't run there). Breaking
`engine.js` changed nothing because that code is dead in the harness. The real path is
`engine_worker.js::attach()`; breaking `_engine_attached` there turned L9 red.

Lesson: when mutating a shared-engine path, target `engine_worker.js`, not `engine.js`.
Confirms standing invariant #4 (assert the realm) and handoff hunt H2.

### Weak sub-assertions noticed (not gate-breaking, but soft)

These individual `c.expect` lines are literal strings printed regardless of whether the
underlying call worked — they'd stay green if only that one API broke. The levels still
catch regressions via *other* assertions, so none is a lying gate, but tighten if touched:

- L16 `scan produced flags`: matches substring `flagged`, which also appears in
  `0 flagged`. Downstream domain asserts caught the mutation; this one didn't.
- L19 `action badge` / `dom set_attr`: `record("dom set_attr", "body[data-tour]=ran")`
  writes the literal regardless of the `set_attr` result — it never reads the attr back.
- L22 `capture` line length is self-reported.

These are candidates for a follow-up (read the value back, like L19's storage.local and
L22's cookie round-trip already do).
