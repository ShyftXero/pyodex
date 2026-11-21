# ADR: one shared interpreter for all tabs

Status: **interpreter half implemented** (`engine = "shared"`, harness L8); UI/DOM migration still open, see *Migration plan* step 4. Supersedes the per-tab
interpreter as the long-term architecture. `boot`/`triggers` remain as the
interim mechanism.

## Context

Users keep 40+ tabs across several windows. Demanding they change that to use a
tool is not a requirement we can impose — the extension adapts to the user.

Today each matching tab costs one Pyodide instance: its own renderer process,
V8 isolate, wasm code, stdlib, and wheels. That is tens of MB per tab and a full
boot per tab, including after a discarded tab is revisited.

Two consequences:

1. A tool that must watch every page (password manager, annotation overlay,
   price tracker) pays N× cost for N tabs.
2. Declarative triggers reduce N to "pages that matter", but do not remove the
   per-page cost for a tool that matters everywhere.

## Decision

Run **one** Pyodide interpreter per browser, in a single offscreen document, and
keep page-side behaviour in per-tab JS that holds no interpreter.

```
┌──────────────────────────┐        ┌──────────────────────────────┐
│ offscreen document       │        │ tab (content script + iframe)│
│  Pyodide, one instance   │◀─────▶ │  DOM agent, no interpreter   │
│  app/main.py, decorators │ ports  │  executes DOM ops, emits     │
│  sockets, caches, state  │        │  events                      │
└──────────────────────────┘        └──────────────────────────────┘
```

- **Interpreter:** started once, survives tab churn, keeps sockets and caches warm.
- **Per tab:** a few hundred KB of JS. It answers "does this page match a
  declaration?" and executes DOM operations. It never loads wasm.
- **Routing:** decorators (`@watch_for`, `@on_event`) are compiled into the page
  agent at build time, so routing needs no Python call. The agent only contacts
  the engine on a match.
- **Lifetime:** the offscreen document is kept alive while any tab is attached,
  and released when the last one detaches.

## Consequences

Costs, stated plainly:

- **DOM access becomes asynchronous.** Python lives in another context, so
  `Page`-style calls stay `await`-based and UI construction becomes RPC. A
  synchronous API would need a SharedArrayBuffer + `Atomics.wait` bridge; that is
  only legal in a worker context and depends on extension pages being
  cross-origin isolated. **Unverified — see open questions.**
- **Isolation is lost.** One runaway loop or OOM affects every tab's overlay.
- **Offscreen lifetime is not fully under our control.** Extension updates and
  Chrome's own reclamation can end it; the engine must restart and rehydrate.
- **Debugging spans two targets** (engine + page), so tracebacks must be routed.

## Alternatives rejected

- *Per-tab interpreter (status quo):* simplest, best isolation, worst memory.
- *Trigger-only lazy boot:* helps unrelated tabs, does nothing for a tool that
  matters on every page.
- *Service worker as host:* Pyodide cannot run there (no DOM, `wasm-unsafe-eval`
  unavailable), so an offscreen document is the only viable host.

## Migration plan

1. **Declarations** (done): `@on_load` / `@watch_for` / `@on_event` plus static
   extraction, so routing rules exist without executing app code.
2. **Engine skeleton:** offscreen document boots Pyodide once; port protocol
   between engine, service worker, and page agents.
3. **Page agent:** execute the existing DOM op set (`query`, `set_value`, ...)
   against the engine's requests; emit declared events.
4. **Runtime split:** `Page`/`Overlay` grow an engine transport; `pyodex_natural`
   socket shims move to the engine unchanged (they never touch the DOM).
5. **Per-tab fallback:** keep the current per-tab path behind `engine = "local"`
   for tools that need synchronous DOM, until the sync bridge question is settled.

## Open questions

- Can extension pages construct a `SharedArrayBuffer` and use `Atomics.wait` from
  the offscreen document without cross-origin isolation? If yes, a synchronous
  DOM bridge is possible and step 4 preserves today's ergonomics.
- How much offscreen memory is reclaimed after the last tab detaches, and does
  Chrome keep it warm across navigations?
- Does one interpreter's single event loop become a head-of-line bottleneck once
  40 tabs share it?

## Acceptance criteria

- 40 open tabs with the extension installed show no more than one Pyodide heap.
- Opening a second matching tab does not increase total memory beyond the first.
- A `@watch_for` tool boots Python on match, with no click, in any tab.
- Engine restart rehydrates attached tabs without reloading pages.
