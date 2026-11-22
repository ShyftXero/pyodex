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

These are the things a normal JS extension does that pyodex cannot do today. Ordered
by how often an extension actually needs them.

| Capability | JS API | Why it matters |
|---|---|---|
| ~~**React to browser events**~~ | `chrome.on("ns.event", fn)` | **closed** — see below |
| **Background work without a tab** | MV3 service worker | Pyodide cannot run in an MV3 service worker (no DOM / eval restrictions). Our shared engine needs at least one tab open; work that must run with zero tabs is impossible today |
| **Inject script into pages** | `scripting.executeScript`, `registerContentScripts` | our DOM ops go through the content script; arbitrary JS execution in the page is not offered (deliberately — it is the remote-code boundary) |
| **Network control** | `declarativeNetRequest` (block, redirect, modify headers) | powerful and very commonly used; unwrapped. Rules are JSON, so this is mostly a Python-to-JSON API |
| **Observe requests** | `webRequest` (MV3: observe only) | logging what a page loads, for analysis tools |
| **Scheduled work** | `alarms` | a Python function every N minutes; needs the event bridge above |
| **Context menu items** | `contextMenus` | right-click actions; also needs the event bridge |
| **Notifications** | `notifications` | user-facing alerts |
| **Cookies** | `cookies` | needs the `cookies` permission; a real capability an automation tool would want |
| **Screenshot a tab** | `tabs.captureVisibleTab` | visual tools |
| **Side panel / popup UI in Python** | `sidePanel`, `action.default_popup` | our popup/options pages are static HTML+JS, not Python |
| **Identity / OAuth** | `identity` | sign-in flows |
| **Downloads, bookmarks, history, topSites** | matching APIs | data tools |
| **Debugger attach** | `debugger` | CDP from an extension — shows an infobar, high risk, deliberately out of scope |
| **Offscreen documents** | `offscreen` | we tried it for the shared engine and it was not usable in our test Chromium |

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
