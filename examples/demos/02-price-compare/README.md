# Cross-site price comparison

**What it demonstrates.** Reads the price on each shop page you visit and remembers it, then shows the cheapest across everything seen. No network: it compares your own browsing.

**pyodex features.** `main.html`, `ext_page`, `@urlmatch` + `@watch_for` (boot only where a price exists), `page.text` / `page.attr`, `storage_get` / `storage_set`, `keep_alive`

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

```html
<!-- main.html: the overlay is markup; Python fills in the comparison -->
<article class="card">
  <h1 id="title">Demo 02 — price compare</h1>
  <pre id="body">watching for prices…</pre>
</article>

<span id="badge" class="badge">no prices yet</span>
```

```python
"""main.py — boot where a price exists, read it, compare across visits."""

import asyncio
import json

from pyodex import ext_page, keep_alive, page, status, storage_get, storage_set, urlmatch, watch_for

STORE = "demo02_prices"


@urlmatch(["http://*", "https://*"])
@watch_for("[data-price]")
def price_seen():
    keep_alive(120)
    asyncio.ensure_future(collect())


async def collect() -> None:
    raw = await page.attr("[data-price]", "data-price")
    price = parse_price(raw[0]) if raw and raw[0] else None
    if price is None:
        return
    # … read item + url, persist in chrome.storage, pick the cheapest …
    ext_page.body.text = "\n".join(lines) + f"\n\ncheapest: {cheapest['price']:.2f} at {cheapest_site.split('/')[2]}"
    ext_page.badge.text = f"cheapest {cheapest['price']:.2f}"
    status(None)
```

The card and badge live in `main.html`; `ext_page.body` and `ext_page.badge` are the
elements with those ids. Layout and appearance live in `overlay.css`, not the Python.

Build and load it:

```
uv run pyodex build examples/demos/02-price-compare
# chrome://extensions -> Developer mode -> Load unpacked -> dist/<slug>/
```
