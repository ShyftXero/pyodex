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

## What does not work

The worker→engine hop for *app traffic*. Observed, precisely:

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
