"""Demo 17 — weather for an IP, animated, no JavaScript.

What it shows: a whole app whose logic is Python and whose motion is CSS. Python
geolocates an IP, asks open-meteo for the current conditions, and sets ONE
attribute — `data-state` on the scene. The stylesheet does the rest: the sun
spins, clouds drift, rain and snow fall, lightning flashes. There is no `.js`
file in this project and none is written for you.

Leave the box empty to read your own IP; type any IP to see the sky wherever it
points.

pyodex features: main.html bindings (onclick / onkeydown -> @handler), ext_page
(strict element access + dataset writes), fetch_json (async HTTP, no wheels),
@start. Everything animated lives in overlay.css as [data-state] rules.
"""

import asyncio

from pyodex import ext_page, handler, start, status
from pyodex_runtime import fetch_json

GEO = "https://ipwho.is/{ip}"
WX = ("https://api.open-meteo.com/v1/forecast"
      "?latitude={lat}&longitude={lon}&current_weather=true")

# WMO weather codes -> (scene state, human label). The state is the only thing
# the CSS keys off; several codes collapse to the same animation on purpose.
WMO = {
    0: ("clear", "Clear sky"),
    1: ("clear", "Mainly clear"),
    2: ("cloudy", "Partly cloudy"),
    3: ("cloudy", "Overcast"),
    45: ("fog", "Fog"),
    48: ("fog", "Rime fog"),
    51: ("rain", "Light drizzle"),
    53: ("rain", "Drizzle"),
    55: ("rain", "Dense drizzle"),
    56: ("rain", "Freezing drizzle"),
    57: ("rain", "Freezing drizzle"),
    61: ("rain", "Light rain"),
    63: ("rain", "Rain"),
    65: ("rain", "Heavy rain"),
    66: ("rain", "Freezing rain"),
    67: ("rain", "Freezing rain"),
    71: ("snow", "Light snow"),
    73: ("snow", "Snow"),
    75: ("snow", "Heavy snow"),
    77: ("snow", "Snow grains"),
    80: ("rain", "Rain showers"),
    81: ("rain", "Rain showers"),
    82: ("rain", "Violent showers"),
    85: ("snow", "Snow showers"),
    86: ("snow", "Snow showers"),
    95: ("storm", "Thunderstorm"),
    96: ("storm", "Storm with hail"),
    99: ("storm", "Storm with hail"),
}


def classify(code: int, is_day: int) -> tuple[str, str]:
    state, label = WMO.get(int(code), ("cloudy", f"Code {code}"))
    # A clear night is its own scene: moon and stars, not a spinning sun.
    if state == "clear" and not is_day:
        return "night", label
    return state, label


@start
def main():
    # Boot straight into the user's own weather; no click required.
    asyncio.ensure_future(_lookup())


@handler  # main.html: onclick="lookup"
def lookup(_event=None):
    asyncio.ensure_future(_lookup())


@handler  # main.html: onkeydown="maybe_enter"
def maybe_enter(event):
    if getattr(event, "key", "") == "Enter":
        asyncio.ensure_future(_lookup())


def _fail(message: str) -> None:
    ext_page.scene.dataset.state = "error"
    ext_page.place.text = "No reading"
    ext_page.temp.text = "—"
    ext_page.desc.text = message
    ext_page.note.text = ""
    status(None)


async def _lookup() -> None:
    ip = str(ext_page.ip.value).strip()
    ext_page.scene.dataset.state = "loading"
    ext_page.place.text = "Locating…"
    ext_page.temp.text = "—"
    ext_page.desc.text = "Asking where that is"
    ext_page.note.text = ""

    try:
        geo = await fetch_json(GEO.format(ip=ip))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Lookup failed: {type(exc).__name__}")

    if not geo.get("success", False):
        return _fail(geo.get("message") or "That IP did not resolve")

    lat, lon = geo.get("latitude"), geo.get("longitude")
    if lat is None or lon is None:
        return _fail("No coordinates for that IP")

    place = ", ".join(p for p in (geo.get("city"), geo.get("country")) if p)
    ext_page.place.text = place or f"{lat:.2f}, {lon:.2f}"
    ext_page.desc.text = "Reading the sky"

    try:
        wx = await fetch_json(WX.format(lat=lat, lon=lon))
        current = wx["current_weather"]
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Weather failed: {type(exc).__name__}")

    state, label = classify(current.get("weathercode", 3), current.get("is_day", 1))
    ext_page.scene.dataset.state = state
    ext_page.temp.text = f"{round(current['temperature'])}°C"
    ext_page.desc.text = label
    ext_page.note.text = f"wind {current.get('windspeed', 0)} km/h · {geo.get('ip', ip or 'your IP')}"
    status(None)
