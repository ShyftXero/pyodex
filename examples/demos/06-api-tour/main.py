"""Demo 06 — API tour.

What it shows: the breadth a normal extension has, driven from Python — tabs,
windows, storage (local + session), the toolbar action, runtime metadata — plus the
DOM surface (element handles, values, text, clicks) — with every call's result
printed so you can see the real values rather than a claim.

pyodex features: `chrome.*` namespaces, `page[...]` element handles, `wait_for`,
`set_attr`, `main.html` + `ext_page` + `@handler`.
"""

from pyodex import chrome, ext_page, handler, page, status


@handler  # main.html: onclick="tour"
async def tour():
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

    ext_page.title.text = f"Demo 06 — {ok} call(s) exercised"
    ext_page.body.text = "\n".join(lines)
    ext_page.badge.text = f"{ok} ok"
    ext_page.badge.classList.add("ok")
    status(None)
