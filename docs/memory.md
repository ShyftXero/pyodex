# Measured interpreter cost

Numbers, not estimates. Method: `tests/measure_memory.py` launches Chromium with
the built extension, opens tabs one at a time, and sums **renderer RSS** for
processes belonging to that profile (`/proc/*/VmRSS` filtered by `user-data-dir`).
Two seconds of settle time per sample.

```
uv run tests/measure_memory.py --engine local  --tabs 3
uv run tests/measure_memory.py --engine shared --tabs 3
```

| Sample (bundled runtime) | `ui = "local"` | `ui = "engine"` |
|---|---|---|
| 1 tab, no overlay | 1145 MB | 1133 MB |
| 2 tabs, no overlay | 1273 MB | 1260 MB |
| 3 tabs, no overlay | 1397 MB | 1382 MB |
| 1 tab, engine/overlay running | 1529 MB | 1492 MB |
| 2 tabs, engine/overlay running | 1584 MB | 1493 MB |
| 3 tabs, engine/overlay running | 1639 MB | 1494 MB |
| **per-tab interpreter cost** | **~55 MB** | **~1 MB** |

The `ui = "engine"` column is the point of the whole exercise: with one Pyodide in a
SharedWorker, adding tabs costs ~1 MB instead of ~55 MB. Forty tabs moves from
≈2.2 GB to ≈40 MB.

```
uv run tests/measure_memory.py --engine shared --ui engine --tabs 3
```

## What this says

1. An active overlay costs **~55 MB per tab**, and the first one costs more
   (~130 MB including shared browser-side allocations that only appear once).
2. `engine = "shared"` **with** `ui = "engine"` removes it: ~1 MB per tab, because
   the tab holds only a small agent iframe and the one interpreter lives in a
   SharedWorker. That is the configuration to ship for a tool present on every page.
3. `engine = "shared"` alone (UI still per tab) does **not** help — the overlay keeps
   its own interpreter, which is what the earlier measurement caught.
4. Dormancy is what makes 40 tabs survivable today: with the default
   `boot = "user"`, an untouched tab pays nothing — the 55 MB applies only to
   tabs whose overlay is actually running.

## Caveats

- RSS includes Chromium's own per-tab allocations (~120 MB/tab for the page
  itself in this harness), so read the *delta* column, not absolutes.
- Headless Chromium, bundled runtime, no wheels beyond the HTTP shims. Loading
  numpy/pandas raises the per-tab figure.
- Process attribution is by `--user-data-dir`, so the browser must be the only one
  using that profile (true here).
