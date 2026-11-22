"""Demo 04 — DIY password manager (teaching demo).

What it shows: credentials persisted in TWO places — an IndexedDB object store and
a JSON file on the persistent (IndexedDB-backed) disk — and autofill through the
page bridge.

pyodex features: @urlmatch + @watch_for, Overlay.card + Overlay.input,
Page.fill(mapping), the persistent data dir (`pyodex_natural.config.data_dir`) with
save(), and raw IndexedDB access from Python via the `js` bridge.

READ THIS BEFORE REUSING IT: the "encryption" here is `secret[::-1]` and nothing
else. Reversing a string is not cryptography — it is there to make the point that
everything below is plain Python, and that a demo which hides the storage format
would be lying about what it protects. Real use needs a KDF (Argon2/scrypt),
authenticated encryption (AES-GCM with a per-entry nonce), and a lock policy.
"""

import asyncio
import json

import pyodex_natural as nat
from pyodex_runtime import (Overlay, Page, create_proxy, js, keep_alive, on, status,
                            storage_get, storage_set, to_js, urlmatch, watch_for)

VAULT = "demo04_vault"
VAULT_FILE = f"{nat.config.data_dir}/demo04-vault.json"
IDB_NAME = "demo04-vault"
IDB_STORE = "entries"

ui = Overlay()
page = Page()


def scramble(secret: str) -> str:
    """The 'encryption'. It is a string reversal, i.e. not encryption at all."""
    return secret[::-1]


def unscramble(stored: str) -> str:
    return stored[::-1]


# --------------------------------------------------------------------------- #
# persistence: a JSON file on the persistent disk
# --------------------------------------------------------------------------- #


def write_file(data: dict) -> bool:
    try:
        with open(VAULT_FILE, "w") as f:
            json.dump(data, f)
        nat.save()  # flush the persistent dir to IndexedDB
        return True
    except OSError:
        return False


def read_file() -> dict:
    try:
        with open(VAULT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- #
# persistence: an IndexedDB object store, driven from Python
# --------------------------------------------------------------------------- #


async def idb_put(site: str, entry: dict) -> bool:
    """Write one entry into IndexedDB.

    The callback fires after this call returns, so the result is awaited rather
    than read synchronously — the first version returned False every time.
    """
    fut = asyncio.get_event_loop().create_future()

    def on_upgrade(event):
        db = event.target.result
        if IDB_STORE not in db.objectStoreNames:
            db.createObjectStore(IDB_STORE)

    def on_success(event):
        db = event.target.result
        try:
            tx = db.transaction(IDB_STORE, "readwrite")
            tx.objectStore(IDB_STORE).put(to_js(entry), site)
            if not fut.done():
                fut.set_result(True)
        except Exception:  # noqa: BLE001
            if not fut.done():
                fut.set_result(False)
        finally:
            db.close()

    def on_error(_event):
        if not fut.done():
            fut.set_result(False)

    req = js.indexedDB.open(IDB_NAME, 1)
    req.onupgradeneeded = create_proxy(on_upgrade)
    req.onsuccess = create_proxy(on_success)
    req.onerror = create_proxy(on_error)
    return await asyncio.wait_for(fut, 5)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #


@urlmatch(["http://*", "https://*"])
@watch_for("input[type=password]")
def login_form():
    keep_alive(180)
    asyncio.ensure_future(ui_for_this_site())


async def entries() -> dict:
    """File is the source of truth; storage is the legacy fallback."""
    data = read_file()
    if data:
        return data
    raw = await storage_get(VAULT)
    return json.loads(raw) if isinstance(raw, str) and raw else (raw or {})


async def save(site: str, user: str, secret: str) -> dict:
    stored = scramble(secret)
    data = await entries()
    data[site] = {"user": user, "stored": stored}
    wrote_file = write_file(data)
    wrote_idb = await idb_put(site, {"user": user, "stored": stored})
    await storage_set(VAULT, json.dumps(data))
    return {"file": wrote_file, "indexeddb": wrote_idb, "stored": stored}


async def ui_for_this_site() -> None:
    info = await page.info()
    site = info["url"].split("?")[0].rsplit("/", 1)[0]
    data = await entries()
    saved = data.get(site)

    card = ui.card(f"Demo 04 — {site.split('//')[-1][:40]}")
    if saved:
        card.body.innerText = (
            f"saved for {saved['user']}\n"
            f"stored (reversed, not encrypted): {saved['stored']}\n"
            "persisted to: file + IndexedDB"
        )
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
    res = await save(site, user_in.value or "user", pass_in.value or "")
    await ui_for_this_site()
    status(f"saved: file={res['file']} indexeddb={res['indexeddb']} (reversed, not encrypted)")


async def do_fill(site: str) -> None:
    data = await entries()
    entry = data.get(site)
    if not entry:
        return
    secret = unscramble(entry["stored"])
    await page.fill({"input[type=password]": secret})
    for sel in ("input[name=user]", "input[type=email]", "input[name=username]", "input[id=user]"):
        if await page.count(sel):
            await page.fill({sel: entry["user"]})
            break
    status("filled from the reversed store")
