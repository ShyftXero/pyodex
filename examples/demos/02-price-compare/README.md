# Cross-site price comparison

**What it demonstrates.** Reads the price on each shop page you visit and remembers it, then shows the cheapest across everything seen. No network: it compares your own browsing.

**pyodex features.** `@urlmatch` + `@watch_for` (boot only where a price exists), `Page.text` / `Page.attr`, `storage_get` / `storage_set`, `keep_alive`

**How it is verified.** Harness level L15 — two shop pages, one persistent comparison, cheapest wins.

```toml
[extension]
name = "Demo 02 Price Compare"
version = ""
description = "Collects the price on each shop page you visit and shows the cheapest."
matches = ["<all_urls>"]
boot = "trigger"
reap_hidden_after = 0
http = false
```

```python
"""Demo 02 — cross-site price comparison.

What it shows: declarative boot (@urlmatch + @watch_for), reading the host page,
and persisting state between visits with chrome.storage.

pyodex features: @urlmatch, @watch_for, Page.text_of / Page.attr, storage_get /
storage_set, Overlay.card. No network: it compares what your own browsing found.
"""

import asyncio
import json

from pyodex_runtime import Overlay, Page, keep_alive, status, storage_get, storage_set, urlmatch, watch_for

ui = Overlay()
page = Page()
STORE = "demo02_prices"


@urlmatch(["http://*", "https://*"])
@watch_for("[data-price]")
def price_seen():
    keep_alive(120)
    asyncio.ensure_future(collect())


def parse_price(text: str) -> float | None:
    digits = "".join(c for c in text if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


async def collect() -> None:
    raw = await page.attr("[data-price]", "data-price")
    price = parse_price(raw[0]) if raw and raw[0] else None
    if price is None:
        return

    info = await page.info()
    site = info["url"].split("?")[0]
    item = await page.text("[data-item]")
    name = item[0].strip() if item else "unknown item"

    saved = await storage_get(STORE)
    data = json.loads(saved) if isinstance(saved, str) and saved else (saved or {})
    data[site] = {"item": name, "price": price}
    await storage_set(STORE, json.dumps(data))

    cheapest_site = min(data, key=lambda k: data[k]["price"])
    cheapest = data[cheapest_site]

    card = ui.card("Demo 02 — price compare")
    lines = [f"{v['item']}: {v['price']:.2f}  ({k.split('/')[2]})" for k, v in sorted(data.items(), key=lambda kv: kv[1]["price"])]
    card.body.innerText = "\n".join(lines) + f"\n\ncheapest: {cheapest['price']:.2f} at {cheapest_site.split('/')[2]}"
    ui.badge(f"cheapest {cheapest['price']:.2f}", top=16, right=16)
    status(None)
```

Build and load it:

```
uv run pyodex build examples/demos/02-price-compare
# chrome://extensions -> Developer mode -> Load unpacked -> dist/<slug>/
```
