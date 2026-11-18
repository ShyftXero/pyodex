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
- **pip_install("pkg")**: micropip at runtime (bundled).
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

## Raw TCP via proxy

Browsers block raw sockets. Run the bundled bridge:

```
uv run proxy/python_websocket_proxy.py --port 1234 --allow localhost:*
```

Then in the extension:

```python
from pyodex_runtime import TcpSocket
sock = await TcpSocket.connect("localhost", 6379)
sock.send(b"PING\r\n")
print(await sock.recv_until(b"\n"))
```

Routes: `ws://127.0.0.1:1234/` → `--target HOST:PORT`; `ws://.../tcp/HOST/PORT` and
`ws://.../tls/HOST/PORT` (proxy opens TLS upstream) → dynamic, gated by `--allow` rules (`name`, glob, or CIDR; port `n`, `a-b`, `*`).
`--token X` requires `?token=X`. Binds `127.0.0.1` by default.

Standalone binaries (haru-pack, thick tier, offline):

```
bash proxy/build_binaries.sh                 # host + windows-x86_64 -> dist/proxy/
bash proxy/build_binaries.sh linux-aarch64   # explicit targets
./dist/proxy/python_websocket_proxy --port 1234 --allow localhost:*
```

## Example

`examples/fireworks` — fireworks-js driven from Python on any URL containing
`shyft`, plus a `requests` demo and optional TCP remote control (`nc -l 9999`,
type `launch` / `stop`).

```
uv run pyodex build examples/fireworks
```

## CLI

```
pyodex init <dir> [--name N] [--match PATTERN ...]
pyodex build <dir> [--out DIR]
pyodex zip <dir> [--out FILE]
pyodex fetch [--pyodide-version V] [--force]
pyodex compat test|list [PKG ...] [--top N] [--shipped] [--filter STATUS]
pyodex pyodide probe|latest|diff [--version V] [OLD NEW]
```

Cache location: `$PYODEX_CACHE` or `~/.cache/pyodex`.
