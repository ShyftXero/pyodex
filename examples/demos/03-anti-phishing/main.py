"""Demo 03 — anti-phishing link inspector.

What it shows: reading structured data out of the host page, doing real analysis in
Python, and reporting findings with reasons rather than a red squiggle.

pyodex features: Page.attr (bulk attribute read), Overlay.card, table-free rendering.
The heuristic is deliberately small and explainable: a link is suspicious when its
registrable domain is a near-miss of a protected brand (edit distance, digit
substitutions) or uses a brand name in a subdomain.
"""

import asyncio

from pyodex_runtime import Overlay, Page, on, status

BRANDS = ("paypal", "google", "microsoft", "apple", "amazon", "github", "coinbase")
LOOKALIKE_DIGITS = {"0": "o", "1": "l", "3": "e", "5": "s", "7": "t", "4": "a"}

ui = Overlay()
page = Page()
card = ui.card("Demo 03 — link check")
card.body.innerText = "Press Scan to check this page's links."
btn = ui.button("Scan links", top=16, right=16, id="scan-btn")


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":")[0].lower()


def normalise(host: str) -> str:
    out = []
    for ch in host:
        out.append(LOOKALIKE_DIGITS.get(ch, ch))
    return "".join(out)


def assess(url: str) -> str | None:
    host = host_of(url)
    if not host or host.replace(".", "").isdigit():
        return None
    labels = host.split(".")
    domain = ".".join(labels[-2:]) if len(labels) >= 2 else host
    flat = normalise(domain.split(".")[0])
    for brand in BRANDS:
        if flat == brand and domain.split(".")[0] != brand:
            return f"lookalike of {brand} (digit substitution)"
        if 0 < edit_distance(flat, brand) <= 2 and flat != brand:
            return f"near-miss of {brand} (edit distance {edit_distance(flat, brand)})"
        if brand in ".".join(labels[:-2]) if len(labels) > 2 else False:
            return f"{brand} used in a subdomain, not the domain"
    return None


async def scan(_event=None) -> None:
    hrefs = await page.attr("a[href]", "href")
    findings = []
    for href in hrefs:
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        why = assess(str(href))
        if why:
            findings.append(f"{host_of(str(href))}\n  {why}")

    card.head.innerText = f"Demo 03 — {len(hrefs)} link(s), {len(findings)} suspicious"
    card.body.innerText = "\n\n".join(findings) if findings else "No lookalike domains found."
    ui.badge(f"{len(findings)} flagged", color="#b91c1c" if findings else "#15803d", top=16, left=16)
    status(None)


@on(btn, "click")
def clicked(_event):
    asyncio.ensure_future(scan())
