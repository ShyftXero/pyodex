"""Demo 15 — Python that only exists where the URL says it should.

The claim being tested: an interpreter costs nothing until a page declares that it
should run there. `@urlmatch` decides *where*, `@watch_for` decides *when*, and the two
together mean every other page in the browser never boots Python at all.

On an article page this demo reads the DOM, computes stats from the text, strips the
sponsored block, and rewrites the article into a clean view — with the numbers shown in
an overlay card so you can check its arithmetic against the page.
"""

import asyncio
import contextlib

from pyodex import (
    chrome,
    events,
    ext_page,
    fetch_text,
    on_event,
    page,
    start,
    status,
    urlmatch,
    watch_for,
)


@start(interactive=False)  # informational card: the page must keep its clicks
def main():
    status(None)

WORDS_PER_MINUTE = 220
state = {"url": ""}


@urlmatch(["*://*/article/*"])
@watch_for("article")
def reading_mode():
    asyncio.ensure_future(build())


@on_event(events.CLICK, selector="a[href^='/article/']", prevent_default=True)
def link_clicked(payload):
    """Intercept a link: the browser stops, Python decides.

    `prevent_default=True` cancels the navigation *synchronously* in the page, then hands
    us what it would have gone to. Nothing is left to race.
    """
    asyncio.ensure_future(open_next(payload))


async def open_next(payload: dict) -> None:
    href = str(payload.get("href") or "")
    if not href:
        return
    ext_page.title.text = "Intercepted — Python took over"
    ext_page.body.text = f"link: {payload.get('text', '')[:60]}\n→ {href}\n\nfetching…"

    try:
        html = await fetch_text(href)
    except Exception as e:  # noqa: BLE001
        ext_page.body.text = f"fetch failed: {type(e).__name__}"
        return

    # Crude extraction on purpose: this demo is about the click, not about parsing.
    import re as _re

    title = (_re.search(r"<title[^>]*>(.*?)</title>", html, _re.DOTALL | _re.IGNORECASE) or [None, "(no title)"])[1]
    paragraphs = _re.findall(r"<p[^>]*>(.*?)</p>", html, _re.DOTALL | _re.IGNORECASE)
    text = " ".join(_re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs)
    words = len(text.split())

    ext_page.title.text = f"Intercepted — {words} words"
    ext_page.body.text = "\n".join([
        f"page: {title.strip()[:70]}",
        f"url stayed: {state['url'][-24:]}",
        "",
        text[:180] + "…",
        "",
        "No navigation happened: the browser's click was cancelled,",
        "and Python fetched the target instead.",
    ])
    status(None)


async def build() -> None:
    info = await page.info()
    state["url"] = info["url"]
    paragraphs = [p for p in (await page.text("article p")) if p]
    words = sum(len(p.split()) for p in paragraphs)
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    lead = paragraphs[0] if paragraphs else ""

    # The visible change: clutter out, a Python-computed header in. Optional: a page
    # without an ad slot is not an error, and a demo that crashes on a variant teaches
    # the wrong lesson about page-shaped assumptions.
    with contextlib.suppress(Exception):  # a page without an ad slot is not an error
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
    with contextlib.suppress(Exception):  # storage is a nicety, not the demo
        seen = (await chrome.local.get("pyodex.reading.seen")) or 0
        await chrome.local.set("pyodex.reading.seen", int(seen) + 1)

    ext_page.badge.text = f"{minutes} min"
    ext_page.title.text = f"Reading mode — {minutes} min"
    ext_page.body.text = "\n".join([
        f"{words} words · {len(paragraphs)} paragraphs",
        f"{WORDS_PER_MINUTE} wpm → {minutes} min",
        f"url: {info['url'].rsplit('/', 2)[-2:][0]}/…",
        "",
        "Python booted here because the URL matched.",
    ])
    status(None)
