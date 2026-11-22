"""Demo 06 — API tour.

What it shows: the breadth a normal extension has, driven from Python — tabs,
windows, storage (local + session), the toolbar action, runtime metadata — plus the
DOM surface (element handles, values, text, clicks) — with every call's result
printed so you can see the real values rather than a claim.

pyodex features: `chrome.*` namespaces, `page[...]` element handles, `wait_for`,
`set_attr`, `Overlay.card` / `badge`.
"""

import asyncio

from pyodex_runtime import Overlay, Page, chrome, on, status

ui = Overlay()
page = Page()
card = ui.card("Demo 06 — API tour", width=340)
card.body.innerText = "Press Run to exercise chrome.* and the DOM."
btn = ui.button("Run tour", top=16, right=16, id="tour-btn")


async def tour(_event=None) -> None:
    lines: list[str] = []
    ok = 0

    def record(label: str, value: object) -> None:
        nonlocal ok
        ok += 1
        lines.append(f"{label}: {value}")

    # --- chrome.runtime ---------------------------------------------------- #
    record("extension id", chrome.runtime.id[:12] + "…")
    manifest = chrome.runtime.manifest()
    perms = manifest.get("permissions") or []
    record("permissions", ",".join(perms) or "none")
    record("runtime url", "chrome-extension://" in chrome.runtime.url("manifest.json"))

    # --- chrome.tabs / windows --------------------------------------------- #
    record("tabs open", await chrome.tabs.count())
    current = await chrome.tabs.current()
    record("this tab", (current.get("url") or "?")[:38])
    record("windows", len(await chrome.windows.all()))
    record("tabs query", len(await chrome.tabs.query(active=True)))

    # --- chrome.storage ---------------------------------------------------- #
    await chrome.local.set("tour", "local-ok")
    record("storage.local", await chrome.local.get("tour"))
    try:
        await chrome.session.set("tour", "session-ok")
        record("storage.session", await chrome.session.get("tour"))
    except Exception as e:  # noqa: BLE001
        record("storage.session", f"unavailable ({type(e).__name__})")

    # --- chrome.action ----------------------------------------------------- #
    chrome.action.title("Demo 06 — API tour")
    chrome.action.badge("06")
    record("action badge", "set")

    # --- DOM --------------------------------------------------------------- #
    found = await page.wait_for("input", timeout=5)
    record("dom.wait_for(input)", found)
    if found:
        el = page["input[name=user]"]
        await el.set_value("tour@example.com")
        if not await el.value():
            el = page["input"]
            await el.set_value("tour@example.com")
        record("dom element value", await el.value())
        record("dom count(input)", await page.count("input"))
        record("dom text(h1)", (await page.text("h1"))[:30])
        await page.set_attr("body", "data-tour", "ran")
        record("dom set_attr", "body[data-tour]=ran")

    card.head.innerText = f"Demo 06 — {ok} call(s) exercised"
    card.body.innerText = "\n".join(lines)
    ui.badge(f"{ok} ok", color="#15803d", top=16, left=16)
    status(None)


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(tour())
