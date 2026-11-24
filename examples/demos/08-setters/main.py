"""Demo 08 — the setter side of the event bridge.

The bridge (demo 07) let Python *receive* events. This is the other half: creating the
things that generate them — a periodic alarm, a context-menu entry, a notification —
and handling their events in Python.

Notice there is no manifest editing: `chrome.alarms` in the code adds the `alarms`
permission at build time, and the same for `contextMenus` and `notifications`.
"""

from pyodex import chrome, ext_page, handler, status

ticks = {"n": 0}


@chrome.on("alarms.onAlarm")
def alarm_fired(args):
    ticks["n"] += 1
    ext_page.body.text = f"alarm fired {ticks['n']}×\n(alarms.onAlarm)"
    ext_page.badge.text = f"{ticks['n']} alarm(s)"


@chrome.on("contextMenus.onClicked")
def menu_clicked(args):
    ext_page.body.text = f"menu clicked\ncontextMenus.onClicked"


@chrome.on("notifications.onClicked")
def notification_clicked(args):
    ext_page.body.text = "notification clicked\nnotifications.onClicked"


@handler  # main.html: onclick="setup"
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

    ext_page.body.text = "\n".join(lines) + "\n\nwaiting for an alarm…"
    status(None)
