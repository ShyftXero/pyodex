"""L3 — the in-depth tutorial: declarative intent, host DOM, persistence.

Proves the ergonomic path only: no boot config, no triggers array. Intent is
declared here, the build derives page behaviour, and Python edits the real page
through the content-script bridge.
"""

import asyncio

from pyodex_runtime import Page, events, on_event, save, status, urlmatch, watch_for

page = Page()


@urlmatch(["http://*", "https://*"])
@watch_for("input[type=password]")
def login_form_seen():
    status("L3: login form detected")
    asyncio.ensure_future(fill_and_count())


@on_event(events.SUBMIT, selector="form")
def submitted(_payload):
    status("L3: submit observed")


async def fill_and_count():
    await page.set_value("input[name=user]", "l3@pytest")
    await page.set_value("input[type=password]", "l3-secret")
    n = bump_counter()
    await page.set_text("#l3-report", f"filled x{n}")


def bump_counter() -> int:
    """Counter in the persistent data dir: increments across page reloads."""
    import pyodex_natural as nat

    path = f"{nat.config.data_dir}/l3_count.txt"
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
    return n
