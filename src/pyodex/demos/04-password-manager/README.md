# DIY password manager (teaching demo)

**What it demonstrates.** Stores a credential per site and fills it back into the
login form, persisted in **two** places — a JSON file on the persistent disk
(`pyodex_natural.config.data_dir`, IndexedDB-backed) and an IndexedDB object store
written from Python.

**The "encryption" is `secret[::-1]`** — a string reversal. It is deliberately not
cryptography: the point of the demo is that everything here is plain Python you can
read, and that a demo hiding its storage format would misrepresent what it protects.
Real use needs a KDF (Argon2/scrypt), authenticated encryption (AES-GCM with a
per-entry nonce), and a lock policy.

**pyodex features.** `@urlmatch` + `@watch_for("input[type=password]")`,
`main.html` + `ext_page`, `@handler`, `Page.fill({selector: value})`, the persistent
data dir with `save()`, `chrome.storage` as a third fallback, and raw IndexedDB via
the `js` bridge with `create_proxy`.

**How it is verified.** Harness level L17 — saves through the UI, asserts the stored
value is the reversed string, that both sinks report success, that it survives a
reload, and that both fields get filled.

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
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```
