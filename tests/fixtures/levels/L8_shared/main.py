"""L8 — one interpreter shared by every tab.

Each tab runs this same main.py, but the Python work happens in the offscreen
engine. Two tabs must report the *same* boot id, which is only possible if a
single Pyodide instance is serving both.
"""

import asyncio

from pyodex_runtime import shared_run, shared_status, status


async def report() -> None:
    try:
        info = await shared_run("import sys; f'py={sys.version_info[0]}.{sys.version_info[1]}'")
        st = await shared_status()
        status(f"shared boot={st.get('bootId')} value={info.get('value')} runs={st.get('runs')}")
    except Exception as e:  # noqa: BLE001
        status(f"shared error {type(e).__name__}: {str(e)[:120]}")


asyncio.ensure_future(report())
