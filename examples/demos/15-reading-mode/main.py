"""Demo 15 — Python that only exists where the URL says it should.

The claim being tested: an interpreter costs nothing until a page declares that it
should run there. `@urlmatch` decides *where*, `@watch_for` decides *when*, and the two
together mean every other page in the browser never boots Python at all.

On an article page this demo reads the DOM, computes stats from the text, strips the
sponsored block, and rewrites the article into a clean view — with the numbers shown in
an overlay card so you can check its arithmetic against the page.
"""

import asyncio

from pyodex_runtime import (Overlay, Page, chrome, events, fetch_text, on, on_event, status,
                            urlmatch, watch_for)

page = Page()
# Informational overlay: `interactive=False` keeps the iframe click-through, so the page
# stays usable — which matters here, because the point is clicking a link in the page.
ui = Overlay(interactive=False)
card = ui.card("Reading mode", width=340)
card.body.innerText = "reading…"

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
    card.head.innerText = "Intercepted — Python took over"
    card.body.innerText = f"link: {payload.get('text', '')[:60]}\n→ {href}\n\nfetching…"

    try:
        html = await fetch_text(href)
    except Exception as e:  # noqa: BLE001
        card.body.innerText = f"fetch failed: {type(e).__name__}"
        return

    # Crude extraction on purpose: this demo is about the click, not about parsing.
    import re as _re

    title = (_re.search(r"<title[^>]*>(.*?)</title>", html, _re.S | _re.I) or [None, "(no title)"])[1]
    paragraphs = _re.findall(r"<p[^>]*>(.*?)</p>", html, _re.S | _re.I)
    text = " ".join(_re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs)
    words = len(text.split())

    card.head.innerText = f"Intercepted — {words} words"
    card.body.innerText = "\n".join([
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
    try:
        await page.set_attr("#ad-slot", "hidden", "1")
    except Exception:  # noqa: BLE001
        pass
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
