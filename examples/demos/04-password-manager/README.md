# DIY password manager (teaching demo)

**What it demonstrates.** Stores a credential per site and fills it back into the login form. **Not a real password manager**: plaintext in `chrome.storage.local`, no master password, no KDF, no authenticated encryption. It exists to show the mechanics.

**pyodex features.** `@urlmatch` + `@watch_for("input[type=password]")`, `Overlay.card` / `Overlay.input`, `Page.fill({selector: value})`, `storage_get` / `storage_set`

**How it is verified.** Harness level L17 — saves through the UI, survives a reload, fills both fields.

```toml
[extension]
name = "Demo 04 Password Manager"
version = ""
description = "A teaching password manager: stores credentials per site locally and fills them."
matches = ["<all_urls>"]
boot = "trigger"
reap_hidden_after = 0
http = false
```

```python
"""Demo 04 — DIY password manager (teaching demo).

What it shows: per-site credential storage, an interactive form rendered from
Python, and autofill through the page bridge.

pyodex features: @urlmatch + @watch_for, Overlay.card + Overlay.input, Page.fill
(mapping of selector → value), storage_get / storage_set, keep_alive.

READ THIS BEFORE REUSING IT: this demo stores credentials in
chrome.storage.local **in plaintext** and has no master password. It exists to show
the mechanics — real use needs a KDF (Argon2/scrypt), authenticated encryption
(AES-GCM with a per-entry nonce), and a lock policy. The fingerprint shown in the
UI is only a checksum so you can see that a secret was stored, not protection.
"""

import asyncio
import json

from pyodex_runtime import (Overlay, Page, keep_alive, on, status, storage_get,
                            storage_set, urlmatch, watch_for)

VAULT = "demo04_vault"
ui = Overlay()
page = Page()


def fingerprint(secret: str) -> str:
    """Cheap checksum so the UI can show 'something is stored'. NOT security."""
    return "".join(f"{(sum(map(ord, secret)) * (i + 7)) % 16:x}" for i in range(4))


async def vault() -> dict:
    raw = await storage_get(VAULT)
    return json.loads(raw) if isinstance(raw, str) and raw else (raw or {})


async def save(site: str, user: str, secret: str) -> None:
    data = await vault()
    data[site] = {"user": user, "secret": secret, "fp": fingerprint(secret)}
    await storage_set(VAULT, json.dumps(data))


@urlmatch(["http://*", "https://*"])
@watch_for("input[type=password]")
def login_form():
    keep_alive(180)
    asyncio.ensure_future(ui_for_this_site())


async def ui_for_this_site() -> None:
    info = await page.info()
    site = info["url"].split("?")[0].rsplit("/", 1)[0]
    data = await vault()
    saved = data.get(site)

    card = ui.card(f"Demo 04 — {site.split('//')[-1][:40]}")
    if saved:
        card.body.innerText = f"saved for {saved['user']} (fp {saved['fp']})"
        btn = ui.button("Fill", top=16, right=16, id="fill-btn")

        @on(btn, "click")
        def _fill(_e):
            asyncio.ensure_future(do_fill(site))

    else:
        card.body.innerText = "No credentials saved for this site yet."
        user_in = ui.input("username", id="vault-user", parent=card)
        pass_in = ui.input("password", id="vault-pass", parent=card, type="password")
        btn = ui.button("Save", top=16, right=16, id="save-btn")

        @on(btn, "click")
        def _save(_e):
            asyncio.ensure_future(do_save(site, user_in, pass_in))

    status(None)


async def do_save(site: str, user_in, pass_in) -> None:
    await save(site, user_in.value or "user", pass_in.value or "")
    await ui_for_this_site()
    status("credential saved locally (teaching demo: plaintext)")


async def do_fill(site: str) -> None:
    data = await vault()
    entry = data.get(site)
    if not entry:
        return
    filled = await page.fill({"input[type=password]": entry["secret"]})
    # fill the username field too, whatever it is called on this page
    for sel in ("input[name=user]", "input[type=email]", "input[name=username]", "input[id=user]"):
        if await page.count(sel):
            await page.fill({sel: entry["user"]})
            break
    status(f"filled {sum(filled.values())} field(s)")
```

Build and load it:

```
uv run pyodex build examples/demos/04-password-manager
# chrome://extensions -> Developer mode -> Load unpacked -> dist/<slug>/
```
