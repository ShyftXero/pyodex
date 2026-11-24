# full instrumentation of a wasm blob

Two directions, both Python:

* **imports** — the module's host functions *are* Python functions, so every call it makes
  outward is yours to see (and to answer differently)
* **exports** — `with guest.trace()` records every call inward: name, args, result, µs

Instrumentation requires owning the instantiation. The module in demo 13 is instantiated
by someone else's page, so this visibility is not available there — that is the honest
boundary, and it is why the two demos are separate.

Timing caveat: browser timers are clamped to ~100 µs buckets for Spectre mitigation, so
call **counts** are exact while single-call microseconds are not. The report says so on
screen rather than printing precision it does not have.

**How it is verified.** Harness L27 — every export call is counted, the host import is observed from Python, timings are listed, and 19,200 pixel calls are measured.

```toml
[extension]
name = "Demo 14 Instrumented"
version = ""
description = "Everything a wasm blob does, observed from Python: imports hosted in Python, every export call timed."
matches = ["<all_urls>"]
boot = "user"
http = false
assets = ["guest.wasm", "color.wasm"]
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
