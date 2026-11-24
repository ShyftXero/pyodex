"""Demo 02 — cross-site price comparison.

What it shows: declarative boot (@urlmatch + @watch_for), reading the host page,
and persisting state between visits with chrome.storage.

pyodex features: main.html, ext_page, @urlmatch, @watch_for, page.attr / page.text,
storage_get / storage_set. No network: it compares what your own browsing found.
"""

import asyncio
import json

from pyodex import ext_page, keep_alive, page, status, storage_get, storage_set, urlmatch, watch_for

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

    lines = [f"{v['item']}: {v['price']:.2f}  ({k.split('/')[2]})" for k, v in sorted(data.items(), key=lambda kv: kv[1]["price"])]
    ext_page.body.text = "\n".join(lines) + f"\n\ncheapest: {cheapest['price']:.2f} at {cheapest_site.split('/')[2]}"
    ext_page.badge.text = f"cheapest {cheapest['price']:.2f}"
    status(None)
