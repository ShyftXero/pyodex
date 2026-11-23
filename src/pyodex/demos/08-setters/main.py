"""Demo 08 — the setter side of the event bridge.

The bridge (demo 07) let Python *receive* events. This is the other half: creating the
things that generate them — a periodic alarm, a context-menu entry, a notification —
and handling their events in Python.

Notice there is no manifest editing: `chrome.alarms` in the code adds the `alarms`
permission at build time, and the same for `contextMenus` and `notifications`.
"""

import asyncio

from pyodex_runtime import Overlay, chrome, on, status

ui = Overlay()
card = ui.card("Demo 08 — setters", width=340)
card.body.innerText = "setting up…"
ticks = {"n": 0}


@chrome.on("alarms.onAlarm")
def alarm_fired(args):
    ticks["n"] += 1
    card.body.innerText = f"alarm fired {ticks['n']}×\n(alarms.onAlarm)"
    ui.badge(f"{ticks['n']} alarm(s)", top=16, left=16)


@chrome.on("contextMenus.onClicked")
def menu_clicked(args):
    card.body.innerText = f"menu clicked\ncontextMenus.onClicked"


@chrome.on("notifications.onClicked")
def notification_clicked(args):
    card.body.innerText = "notification clicked\nnotifications.onClicked"


@on(ui.button("Set up", top=16, right=16, id="setup-btn"), "click")
def clicked(_event):
    asyncio.ensure_future(setup())


async def setup() -> None:
    lines = []

    try:
        await chrome.alarms.create("demo08-tick", 1, delay_minutes=0.5)
        names = [a.get("name") for a in await chrome.alarms.get_all()]
        lines.append(f"alarms: {names}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"alarms: {type(e).__name__}")

    try:
        menu_id = await chrome.contextMenus.add("demo08-menu", "Demo 08: note this page")
        await chrome.contextMenus.remove(menu_id)     # prove the round trip
        lines.append("contextMenus: added + removed")
    except Exception as e:  # noqa: BLE001
        lines.append(f"contextMenus: {type(e).__name__}: {str(e)[:90]}")

    try:
        await chrome.notifications.notify("demo08", "Demo 08", "Setters are working.")
        lines.append("notifications: created")
    except Exception as e:  # noqa: BLE001
        lines.append(f"notifications: {type(e).__name__}")

    card.body.innerText = "\n".join(lines) + "\n\nwaiting for an alarm…"
    status(None)
