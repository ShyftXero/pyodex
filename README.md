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

Caching is on by default (`cache = false` disables it). It is per-site opt-out:
the options page lists host patterns (`example.com`, `*.example.com`, `*`), and
both the browser's storage estimate and a 200 MB budget are surfaced there, with
clear/import/export. Opting a host out purges its entries immediately.

Proven by harness level L5: one network request for a blob across two page loads,
refetch on digest mismatch, and a re-fetch on every load once the host opts out.

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
