"""Password-manager shape: watches every page, one interpreter, no habit changes.

Intent is declared in Python, not in config:

    @watch_for("input[type=password]")   -> this page matters, boot on match
    @on_event(events.SUBMIT, ...)        -> tell me when the form is submitted

The page agent (plain JS, no wasm) evaluates those declarations. Unrelated tabs
never involve the interpreter; a login page gets it with no click.

Note: page-DOM work still needs an interpreter attached to that page. The shared
engine (docs/shared-engine.md) is what removes the per-tab heap.
"""

import asyncio

from pyodex_runtime import Page, events, on_event, status, watch_for

page = Page()


@watch_for("input[type=password]")
def login_form_seen():
    """Called the first time this page is found to contain a password field."""
    asyncio.ensure_future(prepare())


@on_event(events.SUBMIT, selector="form")
def form_submitted(payload):
    status("Vault: submission observed", error=False)


async def prepare():
    fields = await page.query("input[type=password]")
    if not fields:
        return
    status(f"Vault ready: {len(fields)} login field(s)")
    await fill("demo@example.com", "not-a-real-password")


async def fill(username: str, password: str):
    users = await page.query("form input[name*=user], form input[type=email]")
    if users:
        await page.set_value("form input[name*=user], form input[type=email]", username)
    await page.set_value("input[type=password]", password)
