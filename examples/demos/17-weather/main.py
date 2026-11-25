"""Demo 17 — MicroPython native weather."""
import uasyncio as asyncio
from pyodex import document, fetch_json

GEO = "https://ipwho.is/{ip}"
WX = ("https://api.open-meteo.com/v1/forecast"
      "?latitude={lat}&longitude={lon}&current_weather=true")

WMO = {
    0: ("☀️", "Clear sky"), 1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"), 3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Dense drizzle"),
    56: ("🌧️", "Freezing drizzle"), 57: ("🌧️", "Freezing drizzle"),
    61: ("🌦️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"), 67: ("🌧️", "Freezing rain"),
    71: ("❄️", "Light snow"), 73: ("❄️", "Snow"), 75: ("❄️", "Heavy snow"),
    77: ("❄️", "Snow grains"),
    80: ("🌦️", "Rain showers"), 81: ("🌦️", "Rain showers"), 82: ("🌧️", "Violent showers"),
    85: ("❄️", "Snow showers"), 86: ("❄️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Storm with hail"), 99: ("⛈️", "Storm with hail"),
}

state = {"icon": "⏳", "temp": "—", "place": "Locating…", "desc": "reading your weather", "note": "", "ip": ""}

def classify(code: int, is_day: int):
    icon, label = WMO.get(int(code), ("☁️", f"Code {code}"))
    if icon == "☀️" and not is_day: return "🌙", label
    return icon, label

def refresh():
    document.getElementById("icon").textContent = state["icon"]
    document.getElementById("temp").textContent = state["temp"]
    document.getElementById("place").textContent = state["place"]
    document.getElementById("desc").textContent = state["desc"]
    document.getElementById("note").textContent = state["note"]

async def lookup(event=None):
    ip = document.getElementById("ip").value.strip()
    state.update({"icon": "⏳", "temp": "—", "place": "Locating…", "desc": "Asking where that is", "note": ""})
    refresh()
    try:
        geo = await fetch_json(GEO.format(ip=ip))
    except Exception as exc:
        return fail(f"Lookup failed: {type(exc).__name__}")
    if not geo.get("success", False):
        return fail(geo.get("message") or "That IP did not resolve")
    lat, lon = geo.get("latitude"), geo.get("longitude")
    if lat is None or lon is None:
        return fail("No coordinates for that IP")
    place = ", ".join(p for p in (geo.get("city"), geo.get("country")) if p)
    state.update({"place": place or f"{lat:.2f}, {lon:.2f}", "desc": "Reading the sky"})
    refresh()
    try:
        wx = await fetch_json(WX.format(lat=lat, lon=lon))
        current = wx["current_weather"]
    except Exception as exc:
        return fail(f"Weather failed: {type(exc).__name__}")
    icon, label = classify(current.get("weathercode", 3), current.get("is_day", 1))
    state.update({
        "icon": icon,
        "temp": f"{round(current['temperature'])}°C",
        "desc": label,
        "note": f"wind {current.get('windspeed', 0)} km/h · {geo.get('ip', ip or 'your IP')}",
    })
    refresh()

def fail(message: str):
    state.update({"icon": "❌", "temp": "—", "place": "No reading", "desc": message, "note": ""})
    refresh()

def _on_key(event):
    if getattr(event, "key", "") == "Enter":
        asyncio.ensure_future(lookup())

async def main():
    app = document.getElementById("app")
    app.innerHTML = """
    <style>
      :root { color-scheme: dark; }
      body { margin:0; width:320px; padding:14px 16px;
             font:13px/1.5 system-ui,sans-serif; background:#0b0b0d; color:#f4f4f5; }
      .card { background:#18181b; border:1px solid #f43f5e; border-radius:12px; padding:14px 16px; }
      .title { font-size:14px; font-weight:600; margin-bottom:10px; }
      .row { display:flex; align-items:center; gap:8px; }
      .icon { font-size:48px; }
      .temp { font:700 30px/1.1 system-ui,sans-serif; font-variant-numeric:tabular-nums; }
      .place { font-weight:600; }
      .desc { color:#a1a1aa; }
      input { flex:1; min-width:0; padding:8px 10px; border-radius:8px; border:1px solid #2f2f35;
              background:#131316; color:#f4f4f5; font:inherit; }
      input:focus { outline:none; border-color:#f43f5e; box-shadow:0 0 0 3px rgba(244,63,94,.15); }
      button { padding:8px 14px; border-radius:8px; border:1px solid #f43f5e;
               background:#f43f5e; color:#17060a; font:inherit; font-weight:650; cursor:pointer; }
      button:hover { background:#fb7185; border-color:#fb7185; }
      .note { padding-top:8px; color:#71717a; font:11px/1.4 ui-monospace,monospace; }
    </style>
    <div class="card">
      <div class="title">Demo 17 — MicroPython Weather</div>
      <div class="row" style="margin-bottom:10px;">
        <span id="icon" class="icon">⏳</span>
        <div>
          <div id="temp" class="temp">—</div>
          <div id="place" class="place">Locating…</div>
          <div id="desc" class="desc">reading your weather</div>
        </div>
      </div>
      <hr style="border-color:#27272a; margin:10px 0;">
      <div class="row" style="gap:8px;">
        <input id="ip" placeholder="IP address (blank = you)">
        <button id="go">Check</button>
      </div>
      <hr style="border-color:#27272a; margin:10px 0;">
      <div id="note" class="note"></div>
    </div>
    """
    document.getElementById("go").addEventListener("click", lambda e: asyncio.ensure_future(lookup()))
    document.getElementById("ip").addEventListener("keydown", _on_key)
    refresh()
    await lookup()

asyncio.ensure_future(main())
