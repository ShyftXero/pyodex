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

from pyodex import Wasm, ext_page, handler, status

host_calls: list[int] = []


def host_log(value):
    """The module's env.log import — Python decides what it sees and returns."""
    host_calls.append(int(value))
    return 0


@handler  # main.html: onclick="run"
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
    ext_page.title.text = f"Demo 14 — {summary['export_calls']} traced calls"
    ext_page.body.text = "\n".join([
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
