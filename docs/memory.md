# Measured interpreter cost

Numbers, not estimates. Method: `tests/measure_memory.py` launches Chromium with
the built extension, opens tabs one at a time, and sums **renderer RSS** for
processes belonging to that profile (`/proc/*/VmRSS` filtered by `user-data-dir`).
Two seconds of settle time per sample.

```
uv run tests/measure_memory.py --engine local  --tabs 3
uv run tests/measure_memory.py --engine shared --tabs 3
```

| Sample (bundled runtime, `boot = "user"`) | `engine = "local"` | `engine = "shared"` |
|---|---|---|
| 1 tab, no overlay | 1145 MB | 1133 MB |
| 2 tabs, no overlay | 1273 MB | 1260 MB |
| 3 tabs, no overlay | 1397 MB | 1382 MB |
| 1 tab, overlay booted | 1529 MB | 1516 MB |
| 2 tabs, overlay booted | 1584 MB | 1570 MB |
| 3 tabs, overlay booted | 1639 MB | 1624 MB |
| **per-tab interpreter cost** | **~55 MB** | **~54 MB** |

## What this says

1. An active overlay costs **~55 MB per tab**, and the first one costs more
   (~130 MB including shared browser-side allocations that only appear once).
2. `engine = "shared"` as shipped **does not reduce that**, because the overlay
   still boots its own interpreter. The shared engine adds a single instance for
   `shared_run()` work; it does not remove per-tab ones.
3. Therefore the memory win requires moving **UI** into the engine
   (`ui = "engine"`), which is the unfinished part of
   [`shared-engine.md`](shared-engine.md).
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
