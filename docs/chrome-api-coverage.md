# chrome.* coverage: what a JS extension can do, and where pyodex stands

An extension's capability comes from `chrome.*`. This is the checklist we walk when
asking "could this be written in Python instead?" Three columns matter:

- **surface** — is there a Python name for it (`chrome.*` namespace in the runtime)?
- **exercised** — does a demo or harness level actually call it (a claim we can prove)?
- **gap** — what a JS extension can do that we cannot yet.

## Wrapped and exercised

| API | Python surface | Exercised by |
|---|---|---|
| `runtime.id`, `getURL`, `getManifest` | `chrome.runtime.id` / `.url()` / `.manifest()` | demo 06 (L19) |
| `tabs.query`, `count`, `current`, `create`, `reload` | `chrome.tabs.*` | demo 06 (L19), demo 05 (two tabs) |
| `windows.getAll` | `chrome.windows.all()` | demo 06 (L19) |
| `storage.local` | `chrome.local.get/set/remove/keys` | demo 04 (vault), demo 06 |
| `storage.session` | `chrome.session.*` | demo 06 (L19) |
| `storage.sync` | `chrome.sync.*` | not yet exercised |
| `action.setBadgeText`, `setTitle` | `chrome.action.badge()`, `.title()` | demo 06 (L19) |
| `alarms.create/getAll/clear` | `chrome.alarms.*` | demo 08 (L21) |
| `contextMenus.create/remove` | `chrome.contextMenus.add/remove` | demo 08 (L21) |
| `notifications.create` | `chrome.notifications.notify()` | demo 08 (L21) |
| `cookies.get/getAll/set/remove` | `chrome.cookies.*` | demo 09 (L22) |
| `tabs.captureVisibleTab` | `chrome.tabs.capture_visible_tab()` | demo 09 (L22) |
| `downloads.download/search` | `chrome.downloads.*` | demo 09 (L22) |
| `bookmarks.search/getTree` | `chrome.bookmarks.*` | demo 09 (L22) |
| `history.search` | `chrome.history.search()` | demo 09 (L22) |
| `declarativeNetRequest` (dynamic rules) | `chrome.dnr.add_rule/add_redirect_rule/rules/clear/test` | demo 10 (L23) |
| `webRequest.onBeforeRequest` etc. | `chrome.on("webRequest.*")` | demo 10 (L23) |
| `alarms.onAlarm`, `contextMenus.onClicked`, `notifications.onClicked` | `chrome.on(...)` | demo 08 (L21) |
| popup page | `popup.py` (entry inferred at boot) | demo 11 (L24) |
| anything else | `chrome.raw` | by design |

```python
from pyodex_runtime import chrome

print(chrome.runtime.id, chrome.runtime.manifest()["permissions"])
tab = await chrome.tabs.current()
await chrome.local.set("k", "v")
chrome.action.badge("3")
```

## Wrapped, not yet exercised

`chrome.sync.*` — same shape as `local`, but sync storage has quotas and conflict
semantics worth their own test before we claim it.

## Not wrapped — the real gaps

Everything that used to be here is closed except **three** rows, and each of those is
blocked or excluded for a stated reason rather than being unfinished.

| Capability | Status | Reason |
|---|---|---|
| **Background work without a tab** | **blocked by the platform** | Pyodide cannot run in an MV3 service worker, and offscreen documents do not execute in our test Chromium. The shared engine needs at least one live tab. Work with zero tabs open is not possible today — and it is not something a wrapper can fix |
| **Inject script into pages** | **deliberate exclusion** | `scripting.executeScript` would let a Python string become JavaScript in another origin: the remote-code boundary this framework exists to keep closed. DOM ops go through the typed content-script bridge instead |
| **Debugger attach** | **deliberate exclusion** | CDP from an extension shows a permanent infobar and hands full page control to the extension. Too much risk for a framework default |
| `identity` / OAuth | **wrapped; failure path exercised (L22)** | `get_auth_token()` raises an actionable message naming `[oauth2] client_id` when unconfigured, and L22 asserts that wording. The *success* path is untested: a headless harness cannot finish a consent flow, so it is not claimed |

### Closed in this pass

| Capability | Python surface | Level |
|---|---|---|
| Browser events | `chrome.on("ns.event", fn)` — the decorator is also the build-time declaration | L20 |
| Scheduled work | `chrome.alarms.create/get_all/clear` + `alarms.onAlarm` | L21 |
| Context menus | `chrome.contextMenus.add/remove` + `onClicked` | L21 |
| Notifications | `chrome.notifications.notify` + `onClicked` | L21 |
| Cookies | `chrome.cookies.get/get_all/set/remove` | L22 |
| Screenshots | `chrome.tabs.capture_visible_tab()` | L22 |
| Downloads / bookmarks / history | `chrome.downloads.*`, `chrome.bookmarks.*`, `chrome.history.search` | L22 |
| Network rules | `chrome.dnr.add_rule / add_redirect_rule / rules / clear / test` | L23 |
| Request observation | `chrome.on("webRequest.*")` | L23 |
| Popup in Python | `popup.py` as an entry, inferred at boot | L24 |
| Side panel in Python | `panel.py` as an entry; `side_panel` + `sidePanel` permission emitted by the build | L24 |

**Manifest permissions are derived from the code.** Mention `chrome.alarms` and the build
adds `"alarms"`; mention `chrome.dnr.test` and it adds `declarativeNetRequestFeedback` too.
`tests/test_permissions.py` pins every row of the README table, so documentation and
behaviour cannot drift apart.
Four separate permission facts had to be learned the hard way and are now encoded:

- `contextMenus.create` is callback-style and returns its id synchronously, so awaiting it
  raises `'str' object can't be used in 'await' expression`
- webRequest events **never wake a sleeping MV3 worker**, so they are subscribed in the
  live extension page — and `addListener` needs the filter argument or Chrome fails with
  `No matching signature`
- `declarativeNetRequest.testMatchOutcome` requires `declarativeNetRequestFeedback`, and
  in Chromium 137 here it never settles — so L23 proves blocking with a live page fetch
  (the blocked URL must fail, another URL must still work) instead of trusting the API
- the result field is `matchedRules`, not `rules`

## What is *not* a gap

- **DOM access** — `page[...]`, `Element` handles, and `Page.query/text/attr/...` cover
  the same ground as `document.querySelector` (async, because the DOM is in another
  realm).
- **Storage, tabs, windows, action** — wrapped and exercised (see the first table).
- **Anything exotic** — `chrome.raw` reaches the underlying API object directly, so a
  missing wrapper is an ergonomics problem, not a wall.

## The event bridge (closed)

```python
@chrome.on("tabs.onUpdated")
def updated(args): ...          # args = the event arguments, JSON-shaped
```

How it works, because the constraint is not obvious:

1. The build scans for `chrome.on("ns.event", ...)` and writes the list into the
   service worker (`bg.js` as `PYODEX_EVENTS`).
2. The worker registers those listeners **at top level**, because MV3 only wakes a
   worker for statically registered listeners.
3. On fire, the worker forwards `{kind:"pyodex-event", event, payload}`; the Python
   host dispatches to the registered functions.

Verified by harness L20 (demo 07): a real `chrome.storage` write reaches a Python
handler, twice, and updates the UI.

**What it unlocks next:** `alarms`, `contextMenus`, `notifications`, `webNavigation`,
`action.onClicked` and `storage.onChanged` all become wrappers over the same machinery
rather than new plumbing — the missing piece is only the *setter* side
(`contextMenus.create`, `alarms.create`, `notifications.create`).

## The order I would close the rest
2. **`declarativeNetRequest`** — it is JSON in and JSON out; a Python builder for the
   rules is a small, high-value wrapper.
3. **Background without tabs** — needs either a native-messaging host or an
   offscreen document that actually executes; both are outside the current design.
4. **Screenshot / cookies** — narrow, permission-gated, useful for tooling.

## How this list stays honest

Every row above that claims "exercised" points at a harness level; every row under
"not wrapped" is a capability we do not have. When one gets implemented, it moves up
and gets a level — the same rule the rest of the project follows: a claim without a
failing-when-broken test is not a claim.
