"""Demo 12 — a wasm module, driven from Python.

The point is the shape of the call, not the size of the module: exported functions are
attributes, memory is addressed like bytes, and the module's *imports* are Python
functions you write. That last part is what makes instrumentation a Python problem
instead of a JS one — you can stand in for any host function the module calls.

`guest.wasm` here is 81 bytes, hand-encoded by `tools/make_demo_wasm.py` so this demo
needs no compiler. The same API takes a real blob (a game, a codec, a model runtime).
"""

import asyncio

from pyodex_runtime import Overlay, Wasm, on, status

ui = Overlay()
card = ui.card("Demo 12 — wasm from Python", width=360)
card.body.innerText = "loading guest.wasm…"

host_calls: list[int] = []
export_calls: list[str] = []


def host_log(value):
    """This Python function *is* the module's `env.log` import."""
    host_calls.append(int(value))
    return 0


async def run() -> None:
    guest = await Wasm.load("guest.wasm", host={"env": {"log": host_log}})
    guest.instrument(lambda name, args, result: export_calls.append(name))

    total = guest.add(2, 3)
    guest.memory.write(0, b"pyodex wasm")
    text = guest.memory.cstring(0)
    guest.run()  # calls env.log(42) -> the Python function above

    card.head.innerText = f"Demo 12 — add(2, 3) = {total}"
    card.body.innerText = "\n".join([
        f"exports: {', '.join(guest.exports)}",
        f"memory[0:]: {text}",
        f"host saw: {host_calls}",
        f"instrumented calls: {export_calls}",
    ])
    status(None)


@on(ui.button("Run", top=16, right=16, id="run-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(run())
