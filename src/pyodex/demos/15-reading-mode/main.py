"""Demo 15 — Python that only exists where the URL says it should.

The claim being tested: an interpreter costs nothing until a page declares that it
should run there. `@urlmatch` decides *where*, `@watch_for` decides *when*, and the two
together mean every other page in the browser never boots Python at all.

On an article page this demo reads the DOM, computes stats from the text, strips the
sponsored block, and rewrites the article into a clean view — with the numbers shown in
an overlay card so you can check its arithmetic against the page.
"""

import asyncio

from pyodex_runtime import Overlay, Page, chrome, on, status, urlmatch, watch_for

page = Page()
ui = Overlay()
card = ui.card("Reading mode", width=340)
card.body.innerText = "reading…"

WORDS_PER_MINUTE = 220


@urlmatch(["*://*/article/*"])
@watch_for("article")
def reading_mode():
    asyncio.ensure_future(build())


async def build() -> None:
    info = await page.info()
    paragraphs = [p for p in (await page.text("article p")) if p]
    words = sum(len(p.split()) for p in paragraphs)
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    lead = paragraphs[0] if paragraphs else ""

    # The visible change: clutter out, a Python-computed header in.
    await page.set_attr("#ad-slot", "hidden", "1")
    await page.append_html("article", f"""
      <header id="pyodex-reading" data-pyodex-reading="1"
              style="border-left:3px solid #f43f5e;padding:10px 14px;margin:18px 0;
                     background:#0b0b0d;color:#f4f4f5;border-radius:8px;
                     font:14px/1.5 system-ui,-apple-system,sans-serif">
        <strong>{minutes} min read</strong> · {words} words · {len(paragraphs)} paragraphs
        <div style="color:#a1a1aa;margin-top:6px">{lead[:110]}…</div>
      </header>""")

    # Persist that this page was read, so the count survives a reload.
    try:
        seen = (await chrome.local.get("pyodex.reading.seen")) or 0
        await chrome.local.set("pyodex.reading.seen", int(seen) + 1)
    except Exception:  # noqa: BLE001
        pass

    ui.badge(f"{minutes} min", top=16, left=16)
    card.head.innerText = f"Reading mode — {minutes} min"
    card.body.innerText = "\n".join([
        f"{words} words · {len(paragraphs)} paragraphs",
        f"{WORDS_PER_MINUTE} wpm → {minutes} min",
        f"url: {info['url'].rsplit('/', 2)[-2:][0]}/…",
        "",
        "Python booted here because the URL matched.",
    ])
    status(None)
