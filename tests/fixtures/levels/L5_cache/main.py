"""L5 — blob cache: digest verification, no stale serving, per-site opt-out.

The harness serves /blob/<key> and counts requests. Loading this page twice must
produce exactly one network fetch for a cached blob.
"""

import asyncio

import js

import pyodex_natural as nat
from pyodex_runtime import status

BASE = "%BASE%"          # test server origin, substituted at build time
DIGEST_A = "%DIGEST_A%"  # sha256 of /blob/a payload
DIGEST_OTHER = "%DIGEST_BTAIL%"  # deliberately wrong digest for the tamper case


async def mode() -> str:
    from pyodex_runtime import Page

    info = await Page().info()
    return info["url"].split("mode=")[-1].split("&")[0] if "mode=" in info["url"] else ""


async def run() -> None:
    m = await mode()
    try:
        if m == "fetch":
            data, src = await nat.cache_fetch(f"{BASE}/blob/a", DIGEST_A)
            status(f"fetch src={src} len={len(data)}")
        elif m == "tamper":
            # The blob is cached; asking for a different digest must never serve
            # the cached copy — it is evicted, refetched, and the mismatch raises.
            try:
                await nat.cache_fetch(f"{BASE}/blob/a", DIGEST_OTHER)
                status("tamper no-error")
            except Exception as e:  # noqa: BLE001
                status(f"tamper err={type(e).__name__}")
        elif m == "optout":
            await nat.cache_set_no_sites(["127.0.0.1"])
            data, src = await nat.cache_fetch(f"{BASE}/blob/b", None)
            status(f"optout src={src} len={len(data)}")
            hosts = await nat.cache_no_sites_async()
            status(f"optout src={src} len={len(data)} hosts={hosts}")
        else:
            status(f"mode unknown {m!r}")
    except Exception as e:  # noqa: BLE001
        status(f"error {type(e).__name__}: {str(e)[:120]}")
        raise


asyncio.ensure_future(run())
