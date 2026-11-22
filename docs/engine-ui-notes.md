# engine-ui: where it stands

`ui = "engine"` is the mode that makes Python-drawn UI free per tab: the tab holds
no interpreter, the shared engine paints into it. It is **not finished**; harness
L9 is experimental and excluded from the gate.

## What works

- Build: `ui = "engine"` emits no overlay path (`injector.js` returns before the
  iframe), ships `engine.html`/`engine.js`, requests `offscreen`, and registers the
  worker. Verified: a tab has **no overlay iframe**.
- Offscreen host boots: `chrome.offscreen.createDocument` succeeds, and a direct
  `{pyodexEngineRequest: true}` from the service worker is answered
  (`{ok: true, bootId, ready}`).
- Runtime API: `Page` and `Overlay` detect engine mode (`js.pyodexEngine`) and
  route through it; `on_attach(fn)` gives each attaching tab a `Page` bound to it;
  `Overlay.button()` sends a `ui_button` op to the tab agent instead of drawing
  locally, so the API stays synchronous for app authors.

## Fixed while digging

- The engine-mode block called `runDomOp`, which lived inside the *other* IIFE and
  was therefore out of scope in engine mode. Every DOM op failed with
  `runDomOp is not defined`. The op implementation is now hoisted to file scope.
- `Overlay.button()` at import time crashed because no tab had attached yet
  (`currentTabId()` is null). Widget creation without an ambient tab is now a
  no-op; a widget is per-tab, so creating it in `on_attach` is the correct shape.
- `chrome.offscreen.createDocument` inside a message handler raced the response
  channel; the document is now created eagerly at worker startup.

## Proven hops (each tested in isolation)

| Hop | Result |
|---|---|
| content script → worker (attach) | message arrives (instrumented counter) |
| service worker → offscreen engine | answered: `{ok, bootId, ready}` |
| worker → content script (tab op) | `{"ok":true,"payload":2}` |
| engine boot | `ready: true`, `engine_mode() == True` |

## What does not work

The app never starts in the engine. Probe from inside the engine:
`repr((len(_ATTACH_HANDLERS), engine_mode()))` → `(0, True)`. Zero handlers means
`app/main.py` was never executed, which means `startOnce()` did not run, which
means `dispatchAttach()` was not called for the real content-script attach —
even though every hop above works when driven manually.

Earlier observed, precisely:

- The content script's `pyodex-attach` message **does** reach the worker (proved by
  a counter written from inside the listener).
- The engine's request handler **does** receive and answer messages sent from the
  service worker directly (status ping returns a boot id).
- But an attach routed through the worker's own `askEngine()` never reaches the
  engine: the engine reports `ready: false` and has received nothing, and the
  content script's reply is `undefined` rather than an error.

So the break is specific: SW-initiated `chrome.runtime.sendMessage` from inside a
message handler, versus the same call from an evaluated expression. Everything
else in the chain is proven.

## Latest finding (stage instrumentation)

Placing `chrome.storage.local` markers at each stage produced **no marker at all**:
the worker's `pyodex-attach` handler did not run. The same handler previously did
run (an earlier build proved it with the identical technique), so the regression is
in the current build's handler wiring or an exception thrown inside it — not in
messaging itself. Notably, worker → content-script tab ops still return
`{"ok":true,"payload":2}` in the same build, so the content script, the worker and
the engine are all alive.

Next move: wrap the attach handler body in try/catch that persists the thrown error
to storage, then read it. A handler that throws synchronously returns no response and
looks exactly like this from the outside.

## Latest finding: attach is not delivered, though tab ops are

In the same build and the same content script: `chrome.tabs.sendMessage` from the
worker *reaches* the page agent (`{"ok":true,"payload":2}`), while the agent's own
`chrome.runtime.sendMessage({kind:"pyodex-attach"})` never arrives at the worker
(a storage marker at the top of the handler is never written, after 20 s and 10
retries). Messaging works in every other direction we have tested, including
content-script → worker for the blob cache, so this is not a general routing
failure — it is specific to this handler.

Next step is the same trick that cracked the offscreen hop: a three-file
extension where a content script sends one message and the worker writes a storage
marker, with no pyodex in the way. If that works, bisect the difference (module vs
classic worker, listener count, eager `ensureEngine()` at module top).

## Next steps, cheapest first

1. Reproduce the hop in isolation (no pyodex): a three-file extension where the
   content script asks the worker, and the worker asks an offscreen document, and
   log each stage. This is where I lost time by debugging through three layers.
2. If SW→offscreen messaging from a handler turns out unreliable in this Chromium,
   move the routing to a `chrome.runtime.connect` port established *by the offscreen
   document* to the worker at startup (direction may matter), or have the offscreen
   document long-poll the worker.
3. Then: `tests/measure_memory.py --engine shared --tabs 3` with
   `ui = "engine"` should show a near-zero per-tab delta. That measurement is the
   acceptance test for the whole exercise — not L9.

## Why this matters

With `ui = "local"` an active overlay costs ~55 MB per tab (measured, see
`memory.md`). That is the correct *native* trade — DOM access in the same realm as
Python — but a tool present on every page pays it 40 times. `ui = "engine"` is the
answer for those tools; until it works, the honest guidance is dormancy
(`boot = "user"`) so untouched tabs cost nothing.
