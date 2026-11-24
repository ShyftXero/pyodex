# A wasm module, driven from Python

The shape of the call is the point: exported functions are attributes, memory is
addressed like bytes, and the module's *imports* are Python functions you write — so
instrumenting a module is a Python problem, not a JS one.

`guest.wasm` is 89 bytes, hand-encoded by `tools/make_demo_wasm.py` (wasm is a binary
format, not a compiler feature), so this demo needs no toolchain. The same API takes a
real blob.

**pyodex features.** `Wasm.load`, host imports in Python, `guest.instrument(fn)`.

**How it is verified.** Harness L25 asserts `add(2, 3) == 5`, a string round-trips
through linear memory, the guest's `env.log(42)` arrives in Python, and both export calls
were instrumented.

```toml
[extension]
name = "Demo 12 Wasm"
version = ""
description = "Load a wasm blob and drive it from Python: exports, memory, and Python-implemented imports."
matches = ["<all_urls>"]
boot = "user"
http = false
assets = ["guest.wasm"]
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
