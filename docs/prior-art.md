# What people actually say about Python-in-the-browser

Surveyed because the complaints are a checklist of what "first class" means. Sources
are the public discussion threads (HN items for PyScript 402 pts/234 comments,
Brython 403/214, Transcrypt 121/51) plus the projects' own docs. Quoted phrases are
from those threads.

## The landscape

| Project | Model | Where Python runs | Package story |
|---|---|---|---|
| **Pyodide** | CPython compiled to wasm | in-page, real interpreter | PyPI wheels, wasm builds; the strongest of the group |
| **PyScript** | Pyodide/MicroPython + HTML-first wrapper | in-page | inherits Pyodide |
| **Brython** | Python 3 implemented in JS | in-page | own stdlib JS bundle, no PyPI |
| **Transcrypt** | Python → JS transpiler | compiled away | only what transpiles |
| **RapydScript / Skulpt** | transpile / JS interpreter | compiled away / in-page | limited |
| **Anvil / Reflex / Gradio** | Python server, JS UI | on a server | full PyPI, but not the browser |

Two of these keep coming up as "the real options": Pyodide (powerful, heavy) and
Brython (light, limited). Everything else is a niche of one of them.

## What people like

- **Not switching languages** — one language for logic you already have in Python.
- **Pyodide specifically**: "bringing the scientific Python stack to the browser" is
  a genuine capability, not a language preference. numpy/pandas in a page is new.
- **Brython**: works with a plain `<script type="text/python">`, no build step, DOM
  access feels immediate.
- **Transcrypt**: "small, fast" output; you keep JS semantics and tooling, so the
  result behaves like the platform expects.
- **PyScript**: HTML-first — you can add Python to a page without a bundler.

## What people don't like (the recurring list)

1. **Cold start and payload.** "I counted 7 seconds to having it render the graph",
   "pulling in 7 MB of WASM just to render a chart". This is the single most common
   objection to PyScript, and it is a *product* problem, not a runtime one.
2. **Unclear what works.** "I read the site and FAQ and still don't know whether it
   compiles or interprets, what subset of Python it supports, what libraries work."
   Nobody wants to discover limits by hitting them.
3. **Interop leaks.** On Transcrypt: "it calls `document.getElementById` directly,
   so it isn't valid Python you could run independently" — the DOM surface shows
   through and the abstraction is a half-measure.
4. **"A crutch for people avoiding JS."** The corollary: it gets used for apps where
   JS is the better tool, the results are slower, and the whole category looks bad.
5. **Performance is a VM inside a VM.** Interpreting Python to drive DOM updates
   loses to JIT'd JS for exactly the work these frameworks encourage.
6. **Packages.** Both Brython and MicroPython lack a real import story; CPython's
   stdlib is only partially there in wasm.
7. **Debuggability.** Transpiled stacks don't map to Python lines; a slow first load
   has no visible progress; errors appear in a console full of other noise.
8. **Incremental adoption is hard.** These are all-or-nothing: either the page is a
   Python app or it isn't.

## What that means for pyodex — and what we already do

| Complaint | Our answer | Where |
|---|---|---|
| Cold start / payload | dormancy by default (nothing boots until needed), digest-cached runtime, a 2 MB MicroPython tier, optional loader-only package with streamed binaries | `boot`, `docs/memory.md`, `runtime_base` |
| "What works?" | a compatibility registry from *functional probes* in Node **and** Chrome, a tier table with explicit "does not boot yet" rows, and a visible traceback panel | `compat.json`, `compat_mp.json`, README tiers |
| Interop leaks | `page["#id"]`, `page.id("x")`, `Element` handles, a `chrome` namespace, `js_callback` — Python-shaped names over the platform, with `chrome.raw` as the named escape hatch | this repo, demo 04 |
| "Crutch" critique | we do not sell "write your SPA in Python". The pitch is narrower and defensible: *run Python where JS is the only option today*, and put it where the work is (automation, analysis, local state) rather than in the render path | README intro |
| Performance | no interpreter unless a declaration matches; heavy work can move to a shared worker or a socket-capable tier | `@watch_for`, `ui = "engine"` |
| Packages | resolved for `wasm32-pyodide` with uv, pinned, and **probed**; the MicroPython tier publishes which pure-Python packages actually import | `pyodex.lock`, `compat` |
| Debuggability | real CPython, so tracebacks are Python tracebacks (no source maps needed); dev-only panel; `status()`/badges for load progress | `dev`, `status` |
| Incremental adoption | an extension is additive by nature: it decorates pages the user already has, and can be dormant where it is not needed | `boot = "user"` |

## Where we are still weak

- **First-run cost is inherited, not solved.** We make the *second* run cheap and the
  idle case free; the first boot still pays for a runtime. Only a smaller tier fixes
  that, which is why the MicroPython tier exists.
- **DOM work stays async** outside the per-tab mode, and there is no visual debugger
  for the engine mode yet.
- **No incremental migration story to normal web app development** — that is out of
  scope, and pretending otherwise would be the "crutch" critique again.
