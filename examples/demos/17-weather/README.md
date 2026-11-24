# Demo 17 — Weather, animated, no JavaScript

Type an IP (or leave it blank for your own) and the overlay shows the weather
wherever that IP points, with a little animated sky: the sun spins, clouds
drift, rain and snow fall, lightning flashes.

The point of this demo is what it is *not*: there is no `.js` file in the
project and none is written for you. All the logic is Python, all the motion is
CSS.

## How it works

- **`main.py`** — geolocates the IP with `ipwho.is`, asks `open-meteo` for the
  current conditions (both over HTTPS, no API key), maps the WMO weather code to
  a scene name, and sets **one** attribute: `data-state` on `#scene`. It also
  writes the three text lines. That is the whole job.
- **`main.html`** — static markup. The sun, clouds, raindrops, snowflakes, moon,
  stars, and lightning bolt are all there from the first parse. `onclick` /
  `onkeydown` hold handler *names* (`lookup`, `maybe_enter`), which the build
  rewrites into canonical bindings — MV3 forbids inline JavaScript.
- **`overlay.css`** — every animation, keyed off `#scene[data-state]`. Python
  never touches a style; flipping `data-state` from `rain` to `snow` swaps one
  downpour for another.

## Run it

```sh
pyodex build examples/demos/17-weather
# then load dist/demo-17-weather as an unpacked extension, open any page,
# click the launcher.
```

`fetch_json` uses `pyodide.http.pyfetch`, so no `requests` wheels are bundled
(`http = false`). The only permissions derived are `storage` (framework cache)
and cross-origin GETs to the two API hosts, declared in `pyodex.toml`.

## WMO codes → scenes

`clear`/`night`, `cloudy`, `fog`, `rain`, `snow`, `storm` — several codes
collapse to the same animation on purpose (drizzle and heavy rain are both
`rain`). A clear sky at night becomes `night`: moon and stars, not a spinning
sun.
