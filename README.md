# pyodex — Python Chrome extensions on Pyodide

Write a Chrome (Manifest V3) extension in Python. `pyodex` scaffolds the project,
bundles a local Pyodide runtime, resolves your Python dependencies for the
wasm32 target, and emits a loadable unpacked extension.

## Fastest path: one script

Write a PEP 723 script with a `[tool.pyodex]` block, then:

```
uv run pyodex_bundle.py my_cool_ext.py
```

That resolves deps for wasm32, bundles Pyodide, and writes
`dist/<slug>/` (unpacked) plus `dist/<slug>.zip`. See
`examples/single_script/my_cool_ext.py`.

```python
# /// script
# dependencies = ["requests", "numpy"]
#
# [tool.pyodex]
# name = "My Cool Ext"
# matches = ["<all_urls>"]
# url_filter = "shyft"
# overlay_css = "#p { top: 60px }"
# [tool.pyodex.vendor]
# "lib.js" = "https://cdn.jsdelivr.net/npm/some-lib/dist/index.umd.js"
# ///
```

Flags: `--out DIR`, `--no-zip`, `--refresh-lock`.

## Project mode

```
uv sync
uv run pyodex init myext --match "*://*.example.com/*"
uv run pyodex build myext        # -> myext/dist/myext/
uv run pyodex zip myext          # -> myext/dist/myext.zip
```

Load `dist/<slug>/` via `chrome://extensions` → Developer mode → Load unpacked.

## Project layout

```
myext/
├── pyodex.toml          # name, version, matches, url_filter, scripts, vendor, ...
├── main.py             # entry point, runs inside Pyodide (PEP 723 deps supported)
├── overlay.css         # optional styles for the overlay page
├── overlay.body.html   # optional markup injected into the overlay <body>
└── pyodex.lock          # generated: pinned wasm32 wheel versions
```

## How it runs

1. `injector.js` (content script) adds a transparent, click-through iframe over
   the page pointing at `overlay.html`.
2. `bootstrap.js` loads Pyodide from the extension's own `pyodide/` folder,
   unpacks bundled wheels into site-packages, patches `urllib`, then runs `main.py`.
3. `pyodex_runtime` (importable from `main.py`) gives you `Overlay`, `on`, `el`,
   `Page` (talk to host page), `WebSocketClient`, `TcpSocket`, `fetch_json`, `vendored`.

## Declaring intent in Python

The app says what it needs; the build works out how pages behave. No config
required, and no executing app code at build time (extraction is static):

```python
from pyodex_runtime import on_load, watch_for, on_event, events, keep_alive

@on_load                          # wants to run at page load
def begin(): ...

@watch_for("input[type=password]")  # this page matters -> boot on match
def login_form_seen(): ...

@on_event(events.SUBMIT, selector="form")   # react to page events
def on_submit(payload): ...

@urlmatch(["127.0.0.1", "*.mysite.com", "https://some.site.com"])
def only_these_sites(): ...      # URL gate; combine with @watch_for to require both
```

`@urlmatch` takes the pattern you actually mean and classifies it, so there is no
matcher DSL to learn:

| Pattern | Interpreted as |
|---|---|
| `127.0.0.1` | exact host |
| `*.mysite.com` | `mysite.com` and every subdomain (Chrome match-pattern semantics) |
| `*` | any host |
| `https://some.site.com` | URL prefix |
| `https://*`, `https://*.site.com/*` | URL wildcard |
| `https?://.*\\.php`, `re:...` | regular expression |

Stacked on one function, decorators are ANDed — the app boots only when the URL
matches *and* the element exists:

```python
@urlmatch(["https://*", "http://*"])
@watch_for("input[type=password]")
def login_form_seen(): ...
```

`@watch_for` implies `boot = "trigger"`, `@on_load` implies `visible`, nothing
declared means `user` (click to run). Explicit config still wins. Declarations
that cannot be resolved statically are printed as warnings rather than skipped.

## Boot policy

One Pyodide instance runs per tab, with its own heap. Booting every matching tab
is expensive when a user has dozens open, so a tab starts dormant unless the
project asks otherwise.

| Setting | Default | Meaning |
|---|---|---|
| `boot` | `"user"` | `user`: launcher button, boot on click. `visible`: boot when the tab is visible. `auto`: boot on every page load. `trigger`: boot when the page matches a declared condition. |
| `triggers` | `[]` | `trigger` mode only: `[{ url = "*/login*", selector = "input[type=password]" }]`. Both keys optional; both must match if both given. |
| `idle_timeout` | `600` | `user` mode: seconds without activity before the interpreter is freed. |
| `reap_hidden_after` | `300` | `visible`/`auto`: seconds hidden before the interpreter is freed, and re-booted when the tab returns. |
| `max_live` | `0` | Cap on automatic boots (`visible`/`auto`) across tabs. Triggered and user-initiated boots are exempt — a page that needs the tool gets it. |

Declaring load-time work is the sanctioned way to change the default:

```python
from pyodex_runtime import on_load

@on_load
def start():
    scan_page()          # runs as soon as the page loads
```

`@on_load` implies `boot = "visible"` when no policy is set, so a hidden tab is
released and re-booted on return. Set `boot = "auto"` only if the app must keep
running while hidden — that is the mode that costs memory in every tab.

Python can also negotiate at runtime, always within the build's ceiling:

```python
want_boot("visible")   # ask for more than click-to-run (refused if the build says "user")
release()              # done: free this tab's interpreter now
```

`lazy = true|false` is still accepted as an alias for `boot = "user"|"auto"`.

### Tools that must watch every page

A password manager cannot wait for a click, but it also should not own a Python
interpreter in every tab. Split the two concerns: keep the *watching* in the
content script, and boot Python only when there is something to do.

```toml
[extension]
boot = "trigger"
triggers = [{ selector = "input[type=password]" }]
reap_hidden_after = 0     # once awake here, stay awake while hidden
```

```python
from pyodex_runtime import on_load, keep_alive, Page

@on_load
def start():
    keep_alive(600)       # don't get reaped mid-session
    ...                   # read vault, fill the form via Page()
```

The hot loop (DOM mutations, showing an affordance) stays in JS; Python handles
vault parsing, matching, and filling. Pages with no matching trigger cost nothing.

Note the cost model honestly: any tool that touches a page's DOM needs an
interpreter *in that page*, so per-tab memory is inherent for those pages — what
triggers buy you is not paying it on the other 35 tabs. See
`examples/password_manager` for this shape end to end.

That residual per-page cost is temporary. The target architecture is **one
interpreter shared by every tab**, living in an offscreen document, with a thin
JS DOM agent per page — see [`docs/shared-engine.md`](docs/shared-engine.md) for
the design, the migration plan, and the open questions (notably whether a
synchronous DOM bridge is possible).

## Dependencies (PEP 723)

Declare packages inline in `main.py`:

```python
# /// script
# dependencies = ["requests", "numpy>=2"]
# ///
```

On build, `pyodex`:

- resolves with `uv pip compile --python-platform wasm32-pyodide2025`, so only
  wheels that run in Pyodide are accepted (`py3-none-any` or `pyodide_2025_0_wasm32`);
- pins results in `pyodex.lock`, reused until the dependency list changes;
- downloads each wheel once into a global read-only store
  (`~/.cache/pyodex/store/<name>/<version>/`, files `0444`), preferring Pyodide's
  own CDN build when the version matches, else PyPI with sha256 check;
- links wheels into `dist/wheels/` copy-on-write: reflink where the filesystem
  supports it, else hardlink (store file is read-only, so writes fail), else copy.

`pyodex.toml` `pyodide_packages = [...]` adds extra requirements the same way.
Packages Pyodide ships itself (numpy, pandas, cryptography, ...) are pinned to
Pyodide's own build via uv constraints and fetched from the Pyodide CDN.

## The Python surface

Everything an app needs comes from one module — you never import `pyodide`,
`pyodide.ffi`, or `chrome.*` directly.

**DOM on the overlay** (`Overlay`): `card()`, `badge()`, `input()`, `button()`,
`panel()`, `layer()`.

**The host page, async** (`Page`): `query()`, `text()`, `html()`, `attr()`,
`count()`, `set_value()`, `set_text()`, `set_html()`, `click()`, `scroll_to()`,
`fill({selector: value})`, `info()`, `subscribe()`.

**Declaring intent**: `@on_load`, `@watch_for(sel)`, `@on_event(events.X)`,
`@urlmatch([...])`, `@on_attach` (engine mode), `events`.

**Extension APIs**: `chrome.local/sync/session`, `chrome.tabs`, `chrome.windows`,
`chrome.action`, `chrome.runtime`, `chrome.on(event, fn)` for browser events,
`chrome.alarms`, `chrome.contextMenus`, `chrome.notifications`, `chrome.cookies`,
`chrome.downloads`, `chrome.bookmarks`, `chrome.history`, `chrome.identity`,
`chrome.dnr`, and `chrome.raw` for anything not wrapped. **Manifest permissions are
derived from the code** — mention `chrome.alarms` and the build adds `"alarms"`.

**Lifecycle & dev**: `keep_alive()`, `release()`, `want_boot()`, `status()`,
`save()`, `engine_mode()`, `pip_install()`.

**Networking**: `fetch_json()`, `fetch_text()`, `WebSocketClient`, `TcpSocket`,
`WebTransport`.

**Interop**: `js` (the JS globals), `create_proxy`, `to_js`, `jsnull`, `jsobj()`,
`vendored(name)`.

So a demo reads like Python:

```python
from pyodex_runtime import Overlay, Page, on, storage_set, urlmatch, watch_for

ui, page = Overlay(), Page()
ui.card("Hi").body.innerText = await page.text("h1")
```

## Demos

Five extensions, all pure Python, escalating from "does the loop work" to the
shared-interpreter architecture. Every one is built by `pyodex build`, loaded in real
Chromium, and asserted by a harness level — they are checked artifacts, not prose.

```
uv run pyodex init my-ext --demo 03      # scaffold from any of them
uv run pyodex init --list-demos
```

| # | Demo | Shows | pyodex features |
|---|---|---|---|
| 01 | `01-minimal-ui` | render, react, count | `Overlay.card/badge/button`, `@on` |
| 02 | `02-price-compare` | reading the page and remembering between visits | `@urlmatch` + `@watch_for`, `Page.text/attr`, `storage_get/set` |
| 03 | `03-anti-phishing` | analysis in Python with explainable reasons | `Page.attr` (bulk), edit distance, `Overlay.card` |
| 04 | `04-password-manager` | credential store + autofill | `Page.fill({...})`, `Overlay.input`, `keep_alive` |
| 05 | `05-tab-watcher` | always-on, one interpreter for the browser | `engine = "shared"` + `ui = "engine"`, `@on_attach` |
| 06 | `06-api-tour` | the breadth a real extension has | `chrome.tabs/windows/storage/action/runtime`, element handles |
| 07 | `07-event-bridge` | Python receiving platform events | `chrome.on("storage.onChanged")` |
| 08 | `08-setters` | alarms, context menus, notifications | `chrome.alarms`, `chrome.contextMenus`, `chrome.notifications` |
| 09 | `09-toolbox` | cookies, screenshots, downloads, bookmarks | `chrome.cookies`, `chrome.tabs.capture_visible_tab`, `chrome.downloads` |
| 10 | `10-network` | rules the browser enforces; traffic observation | `chrome.dnr`, `chrome.on("webRequest.*")` |
| 11 | `11-popup` | the toolbar popup and side panel are Python | `popup.py` / `panel.py` entries, inferred at boot |
| — | `12-speech` | *planned:* offline speech-to-text in-browser | tracked in issue #3 (Cactus Whistle) |

Demo 01 — the smallest thing that works:

```python
from pyodex_runtime import Overlay, on

ui = Overlay()
card = ui.card("Demo 01 — minimal UI")
badge = ui.badge("0 clicks")
btn = ui.button("Click me", top=16, right=16, id="demo-btn")

@on(btn, "click")
def clicked(_event):
    badge.innerText = "1 click"
```

Demo 02 — boot only where it matters, then remember:

```python
@urlmatch(["http://*", "https://*"])
@watch_for("[data-price]")
def price_seen():
    asyncio.ensure_future(collect())      # no click; the page decided
```

Demo 05 — one interpreter, every tab (~1 MB/tab instead of ~55 MB):

```python
@on_attach
async def attached(page: Page, url: str) -> None:
    ui.badge(f"{len(seen)} tab(s) seen")   # shared state, per-tab paint
    card = ui.card("Demo 05 — tab watcher")
    card.body.innerText = f"This tab: {host_of(url)}\nTabs seen: {len(seen)}"
```

Two notes that cost time and are worth inheriting: in `ui = "engine"` mode the widgets
are painted by the per-tab **agent**, so a test (or a human) must look inside that
agent's shadow root, not at the page; and `status()` is a no-op there because the
interpreter has no local DOM — use a badge or a card for per-tab state.

## Runtime tiers

A pyodex app does not have to run on Pyodide. Tiers are ordered cheapest first and
each one is a **capability** claim, not just a size, so selection is mechanical:

| Tier | ~Size | Capabilities | Status |
|---|---|---|---|
| `micropython` | 2 MB | DOM, subset of stdlib, no packages | **implemented** | DOM, no wasm at all | vendored (runtime + stdlib), **does not execute app code yet** |
| `rustpython` | 5 MB | wider stdlib than MicroPython, no DOM bridge, no CPython fidelity | **implemented** — runs in an MV3 sandbox page (its glue needs `eval`) |
| `pyodide` | 55 MB | full CPython, packages, sockets, crypto | implemented |

```toml
[extension]
runtime = "micropython"                        # force a tier
runtimes = ["micropython", "pyodide"]          # or rank the ones you will accept
```

The build prints its reasoning and writes `runtime-plan.json`:

```
runtime: runtime=pyodide (55 MB, needs ['dom', 'packages']; skipped ['micropython', 'rustpython'] (missing capabilities))
```

Defaults are deliberately conservative: with **no** `runtimes` list the build stays
on Pyodide, because `pyodex_runtime` is written against it and silently switching a
project's interpreter would be a trap. Ask for a tier explicitly and you get it;
ask for one that cannot satisfy your declared needs and the build refuses, naming
what is missing.

Adding a tier is one entry in `runtimes.py` (size, capabilities, CDN patterns,
loader) plus a bootstrap; nothing else in the build knows tier names. The blob
cache is runtime-agnostic too: `cache_patterns()` covers MicroPython and RustPython
URLs even though we ship neither, so other pages running those runtimes are served
from our cache when a digest is pinned.

**RustPython works, in a sandbox page.** Two obstacles, both solved:

1. *No glue module.* The package ships `rustpython_wasm.js` and `_bg.wasm` but no
   `_bg.js` — it expects WebAssembly ESM integration. The wasm's 80 imports come
   from that same ESM (`./rustpython_wasm`), so the build patches the vendored
   module to accept an instance (`__pyodex_setWasm`), compiles the wasm itself and
   hands the instance back.
2. *`eval`.* The generated glue compiles strings with `eval` (Emscripten asm
   consts), and MV3 extension pages cannot enable `unsafe-eval` — that CSP is fixed
   by the platform. The manifest declares `"sandbox": {"pages": ["sandbox.html"]}`,
   which is Chrome's sanctioned relaxation: a sandboxed page may use `eval`, at the
   cost of an opaque origin and no `chrome.*` APIs. The tier's UI therefore lives
   *inside* that page, and the bootstrap resolves assets relatively instead of via
   `chrome.runtime.getURL`.

Harness L12 asserts it in the gate: `runtime=rustpython`, Python painted a marker
into the sandbox document, no page errors.

The MicroPython tier gives you in-page DOM access at ~2 MB — `status()`, `button()`,
`count()`, `text_of()`, `on()`, plus small-tier shims in `pyodex_mp`:

| Shim | Status |
|---|---|
| `sha256_hex()` / `hmac_sha256_hex()` | works (MicroPython's hashlib has no `hexdigest()`, so it hexlifies the raw digest) |
| `http_get()` / `http_post_json()` | works, via *synchronous* XHR — it blocks the tab, so it is for occasional small calls, not heavy traffic |
| `Socket(host, port, bridge=...)` | **Real TCP** through the proxy's HTTP bridge (`/tcp/HOST/PORT/open|send|recv|close`). Verified in-browser: `open → send → recv:echo:PING → close`. Each `recv()` is a long-poll, so this is a latency trade against the Pyodide tier's duplex socket, not a capability one |

### Compatibility sweeps, per tier

`Pyodide`: broad sweep in Node, plus twelve packages re-exercised in Chrome's V8
(L10). `MicroPython`: there is no installer, so compatibility means "vendor the pure
wheel's modules and see if it imports" — exactly what a developer would do:

```
uv run pyodex compat mp --top 30 --write     # sweep, write src/pyodex/compat_mp.json
uv run pyodex compat list --runtime micropython
```

The proxy now also speaks a small HTTP bridge for tiers without a WebSocket
client (open/send/recv/close on port+1, same `--allow` rules and token):

```
curl -X POST http://127.0.0.1:1235/tcp/127.0.0.1/6379/open
curl -X POST --data-binary 'PING' 'http://127.0.0.1:1235/tcp/127.0.0.1/6379/send?id=…'
curl 'http://127.0.0.1:1235/tcp/127.0.0.1/6379/recv?id=…&timeout=3'
```

The first 30 packages: **4 good** (`packaging`, `pygments`, `python-dateutil`,
`typing-inspection`), 20 bad, 6 untestable (native wheels). The common failures are
informative: `no module named '__future__'`, `atexit`, `collections.abc`, and
newer annotation syntax. That is the honest shape of this tier — it is for small,
in-page logic, not for pulling in the world.

## Research notes

- [`docs/chrome-api-coverage.md`](docs/chrome-api-coverage.md) — every chrome.* namespace: wrapped, exercised, or a stated gap.

- [`docs/prior-art.md`](docs/prior-art.md) — what people dislike about Python-in-the-browser, mapped to our answers.
- [`docs/lessons-from-exploit-research.md`](docs/lessons-from-exploit-research.md) — what a CSP/worker investigation actually bought (a runtime tier, an upstream ask, two policies) and what it did not.

## Prior art, and what people dislike about it

Surveyed the public discussions (PyScript 402 pts/234 comments, Brython 403/214,
Transcrypt 121/51) and mapped every recurring complaint onto what we do:
[`docs/prior-art.md`](docs/prior-art.md). The short version — the objections are
cold start, unclear limits, leaking interop, and being a "crutch" for people
avoiding JS. Those are design requirements, not opinions to argue with.

## Novelty ledger

What here is new, and what is the platform used well. This is a claim register, not
marketing: it is **updated whenever prior art surfaces**, and entries move out of
the first table when they do. Last reviewed 2026-11-21.

### Believed novel (no prior art found yet)

| Item | Why it seems new |
|---|---|
| PEP 723 script → loadable extension | One file with a `[tool.pyodex]` block becomes a dependency-resolved, digest-pinned Chrome extension. Nothing in the Pyodide ecosystem offers that contract. |
| Compatibility gate from functional probes | ~50 packages imported **and exercised** inside real Pyodide, with the build refusing known-bad ones. haru-pack does this shape for native apps; for Pyodide packages we found no equivalent. Now run in **two engines**: Node for the broad sweep, Chrome's V8 (harness L10) for the twelve that matter most, because that is where extension Python actually runs. |
| Serving other pages' Pyodide CDN fetches | A MAIN-world `fetch` shim plus a digest-verified worker cache answers third-party pages' Pyodide requests from local storage, keyed against that version's `pyodex-lock.json`, with per-site opt-out. Caching your own assets is ordinary; accelerating arbitrary pages from an extension is not something we can find. |
| Build-time compilation of Python decorators | `@urlmatch(["*.mysite.com"])` / `@watch_for(...)` are extracted by AST at build time into structured page-side routing rules — no config, no JS authoring, and app code is never executed at build time. |
| Unmodified stdlib sockets in an extension | `http.client`, `smtplib` and `redis-py` pass in real Chromium via a JSPI bridge with proxy-terminated TLS. websockify-shaped proxies exist; making stock Python work untouched inside an MV3 extension is the new part. |
| Monotonicity as a trust invariant | The harness refuses to report green when a higher level passes while a lower one fails. That rule caught an overlay that no real user could click. |

### Not novel (platform, used well)

Pyodide and micropip; the WebSocket↔TCP proxy (websockify lineage); MV3 offscreen
documents; Cache Storage; `chrome.storage`; Playwright-driven extension testing;
`wasm-unsafe-eval` CSP for extension pages.

### Deliberately avoided

- **Remote code** (MV3 forbids it): the loader stays local, only assets stream.
- **`chrome.debugger`/CDP to drive page DOM** from a shared interpreter. It works
  and would remove the content-script hop, but costs a "debugging this browser"
  infobar, a powerful permission, and store-review risk. The sanctioned agent is
  slower per op and honest about it.
- **Transpiling Python to JS** (pyvue/vue.py style). Your Python would not run at
  runtime, which defeats the premise.

### Open claims we have not proven

- `ui = "engine"` (tabs hold no interpreter) — scaffolded, transport unfinished;
  see `docs/engine-ui-notes.md`.
- Per-tab memory is measured (`docs/memory.md`) only for the bundled runtime with
  no wheels beyond the HTTP shims.

## Compatibility registry

Like haru-pack's thick-app checks: a package is **good** only if a functional
probe passed inside Pyodide under Node, **bad** if resolve/fetch/import/probe
failed, **import-only** if it imports but has no probe. Shipped results live in
`src/pyodex/compat.json`; local runs append to `~/.cache/pyodex/compat.json`.
Builds refuse known-bad packages unless `--allow-bad`.

```
uv run pyodex compat test requests numpy redis
uv run pyodex compat test --top 50 --shipped
uv run pyodex compat list --filter bad
```

Top-50 PyPI as of 2026-11: 45 good, 4 import-only, 1 bad (uvicorn: no
`create_server`). Known bad by probe: redis, aiohttp, pymongo, paramiko,
psycopg2 (sockets / threads / no wasm wheel). Use `TcpSocket` + proxy for
those protocols. Add probes in `compat.py` `PROBES`.

## HTTP from Python

`http = true` (default) bundles `requests`, `urllib3`, `pyodide-http` and friends.
`requests` works unmodified (urllib3 ≥ 2.2 has a native Emscripten backend).
`urllib.request` is patched by `pyodide-http` at boot. For async use
`pyodex_runtime.fetch_json` / `pyodide.http.pyfetch`.

`host_permissions` (default `<all_urls>`) controls which origins the overlay may fetch.

## Natural Python (`pyodex_natural`)

Installed before `main.py` runs (`natural = true`, default). Each shim gates on
a detected capability and leaves stdlib alone if unsupported.

- **socket / ssl**: plain blocking `socket.create_connection`, `recv`, `sendall`
  work via JSPI `run_sync` over the proxy. `ssl.wrap_socket` routes through the
  proxy's `/tls/HOST/PORT` path (proxy verifies TLS upstream). Lets stdlib
  `http.client`, `smtplib`, `redis-py` style code run unchanged.
- **input() / getpass**: `window.prompt`. **webbrowser.open**: `window.open`.
- **time.sleep**: yields to event loop instead of spinning.
- **persistent cwd**: `/home/pyodide/data` on IndexedDB. `open('x','w')`
  survives reload. `pyodex_natural.save()` or `with persist():` flushes.
  Autosave every 2 s.
- **pip_install("pkg")**: micropip at runtime (bundled). We pass an explicit
  index and verify the import afterwards, because micropip can otherwise return
  successfully while installing nothing ([pyodide#6569](https://github.com/pyodide/pyodide/issues/6569)).
- **print**: mirrored to the browser console as `[py] ...`.

```python
import pyodex_natural
pyodex_natural.config.proxy = "ws://127.0.0.1:1234"
```

Capabilities detected at boot land on `window.pyodexCaps` (DevTools).

## Pyodide capability snapshots

```
uv run pyodex pyodide probe               # runs detect() in Node, saves src/pyodex/capabilities/<ver>.json
uv run pyodex pyodide latest              # newest GitHub release tag
uv run pyodex pyodide probe --version 315.0.0
uv run pyodex pyodide diff 314.0.8 315.0.0
```

Snapshot holds: JSPI, FS types, ffi surface, JS API keys, stdlib gaps, every
shipped package version. `diff` lists added/removed/changed entries so new
runtime features show up as a to-do for shims.

## Blob cache

Pyodide runtimes and wheels are large and immutable per version, which makes them
good cache citizens — as long as nothing stale is ever served. A cached blob is
keyed by URL and only served when its SHA-256 matches the digest the caller
expects; a mismatch is a miss plus eviction, never a silent serve.

```python
import pyodex_natural as nat

data, source = await nat.cache_fetch(url, expected_sha256)   # source: "cache" | "network"
await nat.cache_set_no_sites(["*.example.com"])              # purges that host
```

### Streaming the runtime instead of bundling it

By default the package ships the whole runtime (~22 MB). Point `runtime_base` at a
Pyodide CDN directory and only the JS loader ships (~1.6 MB); the wasm and stdlib
stream through the cache on first load and come from disk afterwards.

```toml
[extension]
runtime_base = "https://cdn.jsdelivr.net/pyodide/v314.0.8/full"
```

The build pins a SHA-256 for every streamed file in `runtime.json` (the lockfile
does not describe the core files, and pinning the bytes our build was tested with
is the stronger claim). Two constraints shape the implementation:

- **The loader stays local.** MV3 forbids remote code, so `pyodide.mjs` cannot be
  imported from the CDN — only *assets* stream.
- **Fetches go through the service worker, and the page reads Cache Storage
  directly.** An extension page is a secure context (mixed-content rules apply to
  plain-HTTP bases), and passing a 10 MB wasm through `runtime.sendMessage`
  overflows the 64 MiB message limit.

Proven by harness level L7: boots from a remote base, one fetch each for wasm and
stdlib, and a reload that re-fetches neither.

### Serving other pages' Pyodide fetches

Installing the extension also short-circuits the Pyodide CDN for *any* page. A
MAIN-world shim replaces `fetch` for the configured hosts; the request is relayed
(isolated bridge -> service worker) and answered from local cache when the bytes
match that version's `pyodide-lock.json` digest. Unverifiable requests fall
through to the network, so a page never breaks because of us.

```toml
[extension]
page_cache = true                          # default
page_cache_hosts = ["cdn.jsdelivr.net"]    # narrow on purpose
```

Proven by harness level L6: the first page load fetches from the "CDN", a second
page with the same profile is served from cache with **zero** CDN requests, and
opting the host out sends both pages back to the network.

### Opting out

Caching is on by default (`cache = false` / `page_cache = false` disable it). It is per-site opt-out:
the options page lists host patterns (`example.com`, `*.example.com`, `*`), and
both the browser's storage estimate and a 200 MB budget are surfaced there, with
clear/import/export. Opting a host out purges its entries immediately.

Proven by harness level L5: one network request for a blob across two page loads,
refetch on digest mismatch, and a re-fetch on every load once the host opts out.

## Shared interpreter

`ui = "engine"` runs **one Pyodide for the whole browser**: the interpreter lives in
a SharedWorker, and tabs hold none at all — no overlay iframe, no per-tab heap.

```toml
[extension]
engine = "shared"
ui = "engine"
```

```python
from pyodex_runtime import Overlay, Page, on_attach

ui = Overlay()

@on_attach
async def attached(page: Page, url: str) -> None:
    ui.button("Inspect", id="go")          # painted in that tab, by Python
    if "login" in url:
        await page.set_value("input[name=user]", "…")   # executed in that tab
```

Harness L9 (in the gate) asserts the two properties that matter: **two tabs are
filled by a single engine**, and **neither tab has an overlay iframe**.

Why a SharedWorker rather than an offscreen document — both were tried:

| | offscreen document | SharedWorker |
|---|---|---|
| observable in our Chromium | **no** (scripts never execute) | yes (`boots:1`, `boots:2`) |
| lifetime | survives zero tabs | ends with the last connection |
| `chrome.*` available | yes | **none** — resolves assets from its own URL |

So: SharedWorker for the interpreter, offscreen still the right host for work that
must outlive every tab. Details, including the five silent failures that had to be
fixed (no `chrome.*` in a worker, `runPythonAsync` never settling, `js.document` at
import time, string agent ids coerced with `int()`, and invisible tracebacks), are in
[`docs/engine-ui-notes.md`](docs/engine-ui-notes.md).

## Networking: what the browser actually allows

Python expects sockets; a browser page cannot open one. Chrome's options, and
what each can reach:

| Path | Bare TCP | Bare UDP | Usable from an MV3 extension? |
|---|---|---|---|
| `chrome.sockets.tcp/udp` | yes | yes | No — Chrome Apps only, removed in 2022 |
| Direct Sockets (`TCPSocket`, `UDPSocket`) | yes | yes | No — Isolated Web Apps only, plus enterprise policy |
| WebSocket | TCP-like, needs HTTP upgrade | no | yes |
| WebTransport | streams over QUIC | datagrams | yes, if the server speaks HTTP/3 |
| WebRTC data channel | no | UDP via ICE | yes, peer-to-peer only, needs signaling |
| Native messaging host | via helper process | via helper | yes, but requires installing a native host |

Pyodide does not change this: its socket support is Node-only
(`useNodeSockFS()` over Node's `net`), so in a browser you always need a shim.
`pyodex_natural` provides that shim on top of one of the transports below.

Reaching real TCP/UDP would need Chromium to expose Direct Sockets to extension
contexts. Today it does not: the API assumes a signed bundle and an enterprise
allowlist (see [IWA](#isolated-web-apps-experimental) below).

### Transport 1: local proxy (default)

```
uv run proxy/python_websocket_proxy.py --port 1234 --allow localhost:*
```

```python
from pyodex_runtime import TcpSocket
sock = await TcpSocket.connect("localhost", 6379)
sock.send(b"PING\r\n")
print(await sock.recv_until(b"\n"))
```

Routes: `ws://127.0.0.1:1234/` → `--target HOST:PORT`; `ws://.../tcp/HOST/PORT`
and `ws://.../tls/HOST/PORT` (proxy opens TLS upstream) → dynamic, gated by
`--allow` rules (hostname, glob, or CIDR; port `n`, `a-b`, `*`).
`--token X` requires `?token=X`. Binds `127.0.0.1` by default.

With the proxy running, plain stdlib code works unchanged (`socket.create_connection`,
`http.client`, `smtplib`, `redis-py`) — see `tests/e2e_natural.py`.

Standalone binaries (haru-pack, thick tier, offline):

```
bash proxy/build_binaries.sh                 # host + windows-x86_64 -> dist/proxy/
bash proxy/build_binaries.sh linux-aarch64   # explicit targets
./dist/proxy/python_websocket_proxy --port 1234 --allow localhost:*
```

### Transport 2: WebTransport (UDP, no local helper)

When you control the server, WebTransport gives you QUIC streams plus unreliable
datagrams with no local process and no open port. Requires an HTTP/3 endpoint
with a valid certificate.

```python
from pyodex_runtime import WebTransport
wt = await WebTransport.connect("https://example.com:4433/wt")
wt.send_datagram(b"ping")                    # unreliable
print(await wt.recv_datagram())
async with wt.stream() as s:                 # reliable, bidirectional
    s.write(b"hello")
    print(await s.read())
```

Check support at runtime: `window.pyodexCaps.webtransport`.

### Transport 3: native messaging host

For local services at the lowest latency, a native host is the sanctioned route:
no open port, no WebSocket hop. The tradeoff is installation — a host manifest
plus a registry key on Windows. Not bundled here.

### Isolated Web Apps (experimental)

An IWA is a signed web bundle (`isolated-app://`) that can use Direct Sockets,
i.e. real TCP/UDP and raw listeners. It cannot inspect traffic in other tabs; it
only sees what clients send to ports it binds.

`pyodex` can package an extension as an IWA-shaped app and serve it for local
sideloading:

```
uv run pyodex iwa build examples/fireworks     # dist/iwa/<slug>/ + instructions
uv run pyodex iwa dev examples/fireworks       # HTTPS dev server for chrome://web-app-internals
```

Sideload with developer flags (Method 1): enable `#enable-isolated-web-apps` and
`#enable-isolated-web-app-dev-mode`, open `chrome://web-app-internals`, and pick
*Install IWA with Dev Mode Proxy* pointing at the `pyodex iwa dev` URL.

Experimental by design: no signing, no update channel, no enterprise policy.
An extension cannot reach Direct Sockets today; this target exists so the code
path is ready if that changes, and so you can run it as a standalone IWA now.

## Tests

```
uv run pytest -q                 # unit: pep723, declarations, allowlist, CoW link, caps diff
uv run tests/e2e_natural.py      # sockets under Node: real servers + proxy
uv run tests/chrome_harness.py   # real Chromium: the release gate
```

The e2e suite proves the socket shim against actual protocol clients: raw sockets,
`select`, `http.client`, `smtplib`, `redis-py`, and proxy-terminated TLS.

### Chrome harness (the gate)

Six extensions, each built from a fixture and loaded into a real browser:

| Level | Proves |
|---|---|
| L1 trivial | content script, launcher, Pyodide boot, overlay DOM |
| L2 quickstart | dependencies (numpy), compute, cross-origin HTTP, async |
| L3 tutorial | `@urlmatch`/`@watch_for`/`@on_event`, host-page DOM, IDBFS persistence across reload, no boot on unmatched pages |
| L4.1 web | URL gate + trigger boot, two tabs filled independently, `keep_alive`, traceback surfacing |
| L4.2 net | `requests`, raw TCP over the proxy, proxy-terminated TLS, WebSocket |
| L4.3 runtime | micropip at runtime, persistent dir, `input()` via prompt, capability probe |
| L5 cache | digest-verified blob cache, no stale serving, per-site opt-out + options UI |
| L6 page cache | other pages' Pyodide CDN fetches served from the extension's cache |
| L7 remote runtime | loader-only package; wasm + stdlib streamed once, then cached |
| L8 shared engine | one interpreter serving two tabs (same boot id) |
| L10 compat | the same probes, run in Chrome's V8 |
| L11 micropython | the 2 MB tier boots, reads the overlay DOM, paints a widget |

```
uv run tests/chrome_harness.py                # all levels, parallel
uv run tests/chrome_harness.py L3 L4.2        # subset
uv run tests/chrome_harness.py --jobs 8       # override parallelism
uv run tests/chrome_harness.py --headed       # watch it happen (uses Xvfb if headless host)
```

Parallelism is picked from the machine: half the CPUs, capped by available memory
(~900 MB per browser), clamped to 2–20. Tests run headless by default, which needs
Playwright's **bundled Chromium** (`channel="chromium"`) — installed Chrome 137+
ignores `--load-extension`, and the headless *shell* cannot load extensions at all.

Monotonicity is enforced: if L4 passes while L1 fails, the run is reported as
untrustworthy rather than green.

## Example

`examples/fireworks` — fireworks-js driven from Python on any URL containing
`shyft`, plus a `requests` demo and optional TCP remote control (`nc -l 9999`,
type `launch` / `stop`).

```
uv run pyodex build examples/fireworks
```

## Known issues

Deliberate deviations and upstream bugs we work around are recorded in
[`docs/known-issues.md`](docs/known-issues.md) — including why runtime pip needs an
explicit index and why the socket probe no longer trusts `connect_ex`.

## Publishing

First upload creates the PyPI project; the name is claimed atomically.

`uv` does not read `~/.pypirc` (twine's format). It checks, in order: `--token`,
`UV_PUBLISH_TOKEN`, keyring, then `.netrc`. Configure `.netrc` once:

```
machine upload.pypi.org
  login __token__
  password pypi-AgEIcHlwaS5vcmc...
```

```
chmod 600 ~/.netrc
uv build && uv publish
```

CI uses trusted publishing (OIDC) instead — see `.github/workflows/publish.yml`.
For a brand-new name, add a *pending publisher* on PyPI (Owner `ShyftXero`,
repo `pyodex`, workflow `publish.yml`, environment `pypi`), then cut a release.

## CLI

```
pyodex init <dir> [--name N] [--match PATTERN ...]
pyodex build <dir> [--out DIR]
pyodex zip <dir> [--out FILE]
pyodex fetch [--pyodide-version V] [--force]
pyodex iwa build|dev <dir> [--out DIR] [--port N]
pyodex cache dir|prune [--dry]
pyodex compat test|list [PKG ...] [--top N] [--shipped] [--filter STATUS]
pyodex pyodide probe|latest|diff [--version V] [OLD NEW]
```

Cache location: `$PYODEX_CACHE` or `~/.cache/pyodex`.
