"""L4.3 — runtime capabilities.

Covers: micropip install at runtime (async path), the persistent data dir across
reloads, input() via window.prompt, and the capability probe's answers.
"""

import asyncio

from pyodex_runtime import Overlay, on, save, status
import pyodex_natural as nat

ui = Overlay()
btn = ui.button("L4.3 run", top=16, left=16, id="l43-btn")
out = ui.panel(id="l43-out")
out.innerText = "idle"


async def install_pkg() -> str:
    try:
        await nat.pip_install_async("six")
        import six

        return f"ok:{six.__version__}"
    except Exception as e:  # noqa: BLE001
        return f"err:{type(e).__name__}:{str(e)[:60]}"


def bump_persistent() -> str:
    try:
        path = f"{nat.config.data_dir}/l43.txt"
        n = 0
        try:
            with open(path) as f:
                n = int(f.read().strip() or 0)
        except (OSError, ValueError):
            n = 0
        n += 1
        with open(path, "w") as f:
            f.write(str(n))
        save()
        return f"x{n}"
    except Exception as e:  # noqa: BLE001
        return f"err:{type(e).__name__}"


def ask() -> str:
    try:
        return input("L4.3 name?") or "empty"
    except EOFError:
        return "eof"
    except Exception as e:  # noqa: BLE001
        return f"err:{type(e).__name__}"


async def run_all() -> None:
    res = {"pip": await install_pkg(), "fs": bump_persistent(), "input": ask()}
    caps = nat.detect()
    res["caps"] = (f"jspi={int(bool(caps.get('jspi')))} "
                   f"wt={int(bool(caps.get('webtransport')))} "
                   f"ds={int(bool(caps.get('direct_sockets')))} "
                   f"py={caps.get('pyodide')}")
    out.innerText = "\n".join(f"{k}={v}" for k, v in res.items())
    status("L4.3 done")


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(run_all())
