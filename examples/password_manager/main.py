"""Password-manager shape: always watching, but Python boots only when needed.

Two things make this work without paying for a Pyodide instance in all 40 tabs:

  * `boot = "trigger"` in pyodex.toml: the content script watches the DOM and
    starts the interpreter only on pages that contain a password field.
  * `reap_hidden_after = 0`: once awake on a login page, this tab stays resident
    so it can react while the user is in another tab/window.

The hot path (observing forms, showing an affordance) is plain JS. Python is
reserved for what JS is bad at: parsing the vault, matching, generating fill.
"""

import json

from pyodex_runtime import Page, on, status

page = Page()  # host-page bridge (async DOM access)


async def find_login_fields():
    return await page.query("input[type=password]")


async def fill(username: str, password: str):
    """Fill the first credential form and dispatch real input events."""
    users = await page.query("form input[name*=user], form input[type=email]")
    if users:
        await page.set_value("form input[name*=user], form input[type=email]", username)
    await page.set_value("input[type=password]", password)


async def main():
    fields = await find_login_fields()
    if not fields:
        return
    status(f"Vault ready: {len(fields)} login field(s)")
    # Demo only: a real vault would decrypt locally and never hardcode anything.
    await fill("demo@example.com", "not-a-real-password")
    status(None)


import asyncio

asyncio.ensure_future(main())
