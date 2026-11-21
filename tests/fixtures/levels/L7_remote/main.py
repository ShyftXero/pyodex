"""L7 — remote runtime base, streamed through the blob cache.

The extension package contains no wasm: the runtime binaries come from the base
URL on first load and from the cache afterwards.
"""

import js

from pyodex_runtime import status

caps = dict(js.pyodexCaps.to_py()) if getattr(js, "pyodexCaps", None) else {}
stats = dict(js.pyodexRuntimeStats.to_py()) if getattr(js, "pyodexRuntimeStats", None) else {}
status(f"remote boot point={caps.get('pyodide', '?')} wasm={stats.get('pyodide.asm.wasm', '?')} stdlib={stats.get('python_stdlib.zip', '?')}")
