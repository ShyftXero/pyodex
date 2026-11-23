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
"""Demo 14 — full instrumentation of a wasm blob.

Two directions, both in Python:

* **imports** — the module's host functions *are* Python functions, so you see every
  call it makes outward, with arguments, before deciding what to return.
* **exports** — `with guest.trace()` records every call inward: name, arguments, result
  and microseconds.

Instrumentation needs to own the instantiation. That is the honest boundary: the module
in demo 13 is instantiated by someone else's page, so this level of visibility is not
available there — you would have to load the blob yourself (which is what this demo does).

`guest.wasm` and `color.wasm` are 89 and 85 bytes, hand-encoded by
`tools/make_demo_wasm.py`: the plumbing is identical for a real payload.
"""

import asyncio

from pyodex_runtime import Overlay, Wasm, on, status

ui = Overlay()
card = ui.card("Demo 14 — instrumented wasm", width=400)
card.body.innerText = "click Run"

host_calls: list[int] = []


def host_log(value):
    """The module's env.log import — Python decides what it sees and returns."""
    host_calls.append(int(value))
    return 0


async def run() -> None:
    guest = await Wasm.load("guest.wasm", host={"env": {"log": host_log}})
    pixels = await Wasm.load("color.wasm")

    with guest.trace() as trace:
        for i in range(20):
            guest.add(i, i + 1)          # 20 timed export calls
        guest.memory.write(0, b"instrumented")
        text = guest.memory.cstring(0)
        guest.run()                       # calls env.log(42) -> Python

    # A real compute loop, measured the same way: 160x120 pixels through wasm.
    with pixels.trace() as pixel_trace:
        for y in range(120):
            for x in range(160):
                pixels.color(x, y)

    summary = trace.summary()
    pix = pixel_trace.summary()
    per_pixel = pix["total_us"] / max(pix["export_calls"], 1)
    card.head.innerText = f"Demo 14 — {summary['export_calls']} traced calls"
    card.body.innerText = "\n".join([
        trace.report(),
        f"host saw: {host_calls}",
        f"memory: {text}",
        "",
        f"pixels: {pix['export_calls']} calls, {pix['total_us']:.0f}us "
        f"({per_pixel:.2f}us/pixel, {1e6 / max(per_pixel, 1e-9):.0f}/s)",
        "",
        "timings are clamped to ~100us buckets by the browser (Spectre mitigations):",
        "counts are exact, single-call microseconds are not.",
    ])
    status(None)


@on(ui.button("Run", top=16, right=16, id="run-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(run())
```
