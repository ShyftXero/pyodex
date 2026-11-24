# Authoring ergonomics

The design rules for pyodex's app-facing API, the surface they produce, and the
alternatives that were rejected. Written because "make it easier" is not a
specification — without a stated measure, every convenience looks equally good and
the API grows a private dialect.

## The ethos

> We want the feeling of using jQuery when JavaScript sucked for the Chrome extension
> ecosystem. We want Python because JavaScript still sucks. That does not mean it will
> always be easy — but we should try to make the things that are often hard a little
> easier for ourselves and others.

jQuery won on four things, none of them clever: `$()` made CSS selectors a query
language before `querySelectorAll` existed; chaining made five steps read as one;
`.on()` with delegation bound behavior to a container instead of to elements that had
to exist yet; and `$.ajax` replaced the `readyState` state machine with one call.

It lost because it had no state model. It mutated a DOM it did not own, kept its state
in closures, and every app hand-wired its own `setBusy()`. React exists to fix that.

pyodex takes the fluency and not the mush. The line is: **a helper may remove a state
machine or a lookup; it may not introduce a language.** `$.ajax` removed `readyState`
and is good. `busy=[...], status=..., focus=...` introduces a second vocabulary inside
a kwarg list and is not.

## The measure

Every addition is judged by one test:

> Does it delete lines from `examples/experimental/17-ssh/main.py` while making the
> app's intent more obvious?

"More powerful" is not a passing grade. Neither is "more consistent with a framework."
The SSH example is the fixture because it is the most ordinary thing a pyodex app does:
a form, a submit, a long-running call, a busy state, an error, a cleanup.

## The rules

Each rule names the jQuery-era failure it prevents and the guardrail that enforces it.

**1. Low ceremony for the daily task, not cleverness for its own sake.**
`$("#user").text()` over `getElementById(...).textContent`; `requests.get(url).json()`
over a `urllib` opener dance. *Guardrail:* the line-count test above.

**2. Dispatch on type only with an unambiguous discriminator.**
jQuery sniffed for `<` to decide create-vs-select and its own docs warn about
`$("<input>", {size: "4"})`. *Guardrail:* `get("#host")` means one thing. Correctness
of `@start`/`@handler` callables is decided by **arity**, never by inspecting strings.

**3. One named extension seam with a return contract.**
`$.fn.foo = fn; return this` produced an ecosystem because the seam was one line and
the rule was one sentence. *Guardrail:* one way to register a handler (`@handler`), one
low-level form (`@bind`), no third. The `pyodex` / `pyodex_runtime` split is a wound to
close, not a pattern to extend.

**4. Reuse a syntax the author already knows from the adjacent domain.**
CSS selectors were the unlock because the people writing the markup already knew CSS.
*Guardrail:* ids and selectors from the HTML the author is already writing; Python
types for values. No pyodex-specific form DSL.

**5. Strict by default; leniency is a named opt-in.**
jQuery's empty set never raised, so "selector matched nothing" was unfindable.
SQLAlchemy's own post-mortem says hiding behavior *"make[s] for a more complex API which
works inconsistently."* *Guardrail:* `get()` raises, `maybe()` returns `None`,
`get_all()` returns a (possibly empty) list. The method name carries the cardinality.

**6. Every chain stays debuggable.**
The pandas chaining writeup admits long chains are hard to debug because there are no
intermediate values; its fix is a logging decorator. *Guardrail:* every stateful surface
has a `__repr__` with live state; `pyodex explain` prints what the build decided and why,
with `file:line`.

**7. Lifecycle boundaries are explicit.**
SQLAlchemy 2.0 made `with engine.begin():` visible because *"attempts to hide the
presence of a transaction"* were the bug. *Guardrail:* `@start` for entry, `ctx` for
ownership and teardown, `attach_to()` returning a handle. Nothing cleaned up by a
`finally:` block the author forgot.

**8. Declarative state beats hand-rolled state.**
The gap that killed jQuery. *Guardrail:* the framework owns *mechanism* (when a handler
runs, what it receives, what the build validates); the app owns *policy* (which widgets
are busy, what the status says).

## The surface

Three nouns for three surfaces, and one for ownership.

| Name | What it addresses | Sync | Ownership |
|---|---|---|---|
| `ext_page` | the page pyodex ships — `main.html` | sync | yours |
| `page` | the page you are injected into | async | theirs |
| `chrome` | browser and extension APIs | mixed | the platform |
| `ctx` | handles owned by this app instance | sync | torn down with the app |

`page` keeps the meaning it already has in the existing `Page` class; `ext_page` is the
new name. The qualifier is the rule: two pages, and the name says whose.

In an Isolated Web App there is no host page. `page` must fail with that sentence rather
than silently aliasing `ext_page`:

```
RuntimeError: no host page — this app runs as an Isolated Web App.
  `ext_page` is the app itself; `page` only exists in an extension tab.
```

### Element access

```python
ext_page.get("#host")        # required; raises, naming the DOM it searched
ext_page.maybe("#hint")      # optional; None
ext_page.get_all(".row")     # collection; possibly empty, iterable
ext_page.host                # attribute form, resolved on access
ext_page["class"]            # item form for anything normalization mangles
```

Attribute normalization: hyphenated ids become snake_case (`pyodex-ssh-prompt` →
`ext_page.pyodex_ssh_prompt`), Python keywords take a trailing underscore
(`class` → `class_`), and a leading underscore is reserved. Colliding ids are a build
error, not a silent first-wins.

A failed lookup names the surface and the alternatives:

```
LookupError: no element #hsot in the extension page.
  Declared ids: connection, host, user, port, connect, disconnect, terminal, …
  Did you mean "#host"?
  (looking for a host-page element? use `page.get()`)
```

### Binding doors

Three targets, three names, no guessing:

```python
@handler                      # markup in ext_page names a Python function
def connect():
    ...

@on_page("click", role="button", name="Buy now")   # an element on the host page
def add_to_cart(ev):
    ...

@on(ext_page.get("#hint"), "mouseenter")           # imperative, element already held
def show_hint(ev):
    ...
```

`@handler` takes its name from the function unless one is given:
`@handler("save_target")` exists only for when markup says something else. The event is
passed **only if the signature accepts one**, decided by arity — `def connect()` and
`def connect(event)` both work, and nothing sniffs strings to decide.

`@bind(selector, event, fn, **options)` is the low-level form `@handler` compiles down
to. It exists for dynamic wiring and for tests. It is not the documented path.

### Markup bindings

Canonical form, exactly like Alpine and Livewire so the vocabulary transfers:

```html
<form id="connection" pyodex-submit.prevent="connect">
<button id="disconnect" pyodex-click="disconnect">
<input id="search" pyodex-input="search" pyodex-keydown.esc="clear">
<div pyodex-keydown.escape.window="close">          <!-- bind on window -->
```

- The event lives in the attribute **name**: greppable, one attribute per event, and no
  controller indirection (Stimulus needs `click->ctrl#method` only because it multiplexes
  controllers).
- Modifiers are dotted suffixes: `.prevent`, `.stop`, `.once`, `.self`, `.window`.
- The value is a **handler name**, never an expression. Exceptions in the value are
  exactly how htmx's `hx-on` needs `new Function`, which MV3 forbids.
- Delegation on the overlay root for bubbling events; direct binding for `focus`,
  `blur`, `mouseenter`, `mouseleave`, which do not bubble.
- One `create_proxy` per handler name, cached. Not per element: Pyodide proxies are not
  collected automatically.

### Legacy `on*` sugar

Chrome locks extension pages to `script-src 'self'`, and MDN confirms inline handlers are
blocked by it:

> "Note that inline event handlers are blocked as well: `<button onclick="doSomething()">`
> … You should replace them with `addEventListener` calls."

So `onclick` in markup can never execute. It can still be *read*, which makes it useful
sugar for the canonical form — at **build** time, not boot time:

```html
<button onclick="disconnect">                      <!-- source -->
<button pyodex-click="disconnect">                 <!-- what ships -->
```

Build-time because the handler string is compiled lazily, at dispatch. Shipping real
`on*` and rewriting it five seconds into boot means every event in that window logs
`Refused to execute inline event handler…`; and `load`/`error` on images fire during
parse, before any binder exists. Rewriting before shipping has neither problem, and
removing `on*` / writing `pyodex-*` is not a Trusted Types sink (writing `on*` would be).

Accepted grammar — a reference, optionally called:

```
handler := ws ref ws (";" ws "return" ws "false" ws)?
         | ws "return" ws "false" ws
ref     := name ws ("(" ws ("event" | "this") ("," ws ("event" | "this"))* ws ")")?
```

`name` is `[A-Za-z_][A-Za-z0-9_]*`. `return false` maps to `.prevent` + `.stop`, the
jQuery meaning. Everything else is a build error, pointed at the attribute:

```
main.html:14 — onclick="alert('x')"
  pyodex handlers must be a function name, not JavaScript.
  Did you mean onclick="connect"?
```

Three attributes are not a blind `on<event>` → `pyodex-<event>` mapping to be safe:

| Attribute | Handling |
|---|---|
| `onload` on `<body>` | warn: document-ready, use `@start` |
| `onload` / `onerror` on `<img>`, `<script>` | bind, then retro-fire if `el.complete` |
| `onkeypress` | warn: deprecated, use `keydown` |
| `onreadystatechange` | reject: legacy XHR idiom, no element mapping |

### Attachment

`attach_to` takes an **element**, so which DOM is decided before the call:

```python
@start
def main():
    ctx.terminal = SshTerminal.attach_to(ext_page.get("#terminal"))
```

A plugin's `attach_to(element)` returns the plugin object; `ctx` owns it and tears it
down. `page.attach_to(...)` is the async sibling for the host page — the `await` is the
signal that the call crossed a context boundary, which is why it is not hidden.

Late and repeated appearance is a separate, explicit door:

```python
@attach_when("#terminal")            # SPA re-render, lazy panels
def mount_terminal(el):
    return SshTerminal().attach_to(el)   # returned handle is removed with the element
```

Strict default, opt-in waiting: the shape difference between "immediately" and
"eventually" is visible at the call site instead of being inferred.

### Host-page bindings

pyodex does not rewrite a third-party DOM. A React or Vue page re-renders and drops
injected attributes, so you would be racing the site's own MutationObserver forever.
Bindings delegate from `document` and match at dispatch:

```python
@on_page("click", selector="#buy")                      # allowed, fragile
@on_page("click", role="button", name="Buy now")        # survives redesigns
@on_page("click", any_of=["#buy", {"role": "button", "name": "Buy now"}])
```

The locator ladder is Playwright's, and for the same reason: long CSS chains "can break
when the DOM structure changes." Match at dispatch also survives virtualized lists and
re-renders, because nothing is cached.

Silence is the userscript ecosystem's chronic failure. pyodex surfaces it:

- `pyodex explain` lists every binding, its locator, and its `file:line`.
- `status()` reports per-binding match state: `{"#buy": matched, "#checkout": UNMATCHED}`.
- A dev-only badge warns on the page when a binding matches nothing.

## The build's job

Static analysis is what makes the sugar trustworthy. At `pyodex build`:

| Check | Source | Failure |
|---|---|---|
| handler names exist | `pyodex-*` / `on*` values × `@handler` names (AST) | error + `difflib` suggestion |
| ids exist | `ext_page.get("#…")` literals × `main.html` ids | error + suggestion |
| ids are unique | `main.html` | error naming both lines |
| legacy attributes rewrite | `main.html` | error for non-reference values |
| permissions | `chrome.*` mentions (existing) | already implemented |
| runtime tier | declared needs (existing) | already implemented |

`pyodex explain` prints the same facts as a receipt, because inferred behavior that
cannot be inspected is indistinguishable from magic:

```
bindings  main.html:9   pyodex-submit.prevent → connect
          main.html:15  pyodex-click           → disconnect
handlers  connect, disconnect                    (main.py)
elements  8 ids declared · 8 referenced · 0 duplicate
runtime   pyodide (55 MB, needs ['dom'])
manifest  storage                                (secure_shell_plugin.py:118)
```


## Converting an existing app

`examples/demos/01-minimal-ui` is the reference conversion: same behaviour, same
harness level, markup instead of widget calls.

1. **Move the markup out of Python.** Rename `overlay.body.html` to `main.html`
   (both are read). Every `id` in it becomes the API; nothing else has to be
   declared.
2. **Name elements by attribute, not by lookup.** `document.element("host")` →
   `ext_page.host`. Hyphens become underscores (`pyodex-ssh-prompt` →
   `ext_page.pyodex_ssh_prompt`), Python keywords take a trailing underscore
   (`class` → `class_`), and `ext_page["…"]` is the escape for anything else.
3. **Bind in the markup.** `@on(btn, "click")` becomes `@handler` plus
   `onclick="clicked"` (rewritten at build) or the canonical
   `pyodex-click="clicked"`. Modifiers that were keyword arguments move into the
   attribute: `prevent_default=True` → `pyodex-submit.prevent`.
4. **Move layout to CSS.** `Overlay.button(top=16, right=16)` and friends were
   inline styles; the new format expects the position to live in `overlay.css`.
   This is the README's own rule — layout is not Python's job.
5. **Keep app state in Python; keep handles in `ctx`.** A counter is still a
   module global. A mounted plugin handle is `ctx.<name>` so it is released with
   the page instead of leaked.
6. **Build.** Typos are build errors now, not silent no-ops:

   ```
   main.html:9 — pyodex-click="conect": no handler named "conect".
   Declared: connect, disconnect. Did you mean "connect"?
   ```

Two conversion gotchas worth knowing:

- **Only literal selectors are checked.** `ext_page.get("#host")` is validated;
  `ext_page.get(NAME)` is not, because the value is not known until runtime. That
  is deliberate: a computed selector is the escape hatch, and `maybe()` is the
  sanctioned way to ask whether something exists.
- **`focus`/`blur`/`mouseenter`/`mouseleave` do not bubble.** The binder attaches
  directly to the element for every event, so they work; only future delegation
  would need `focusin`/`focusout`.

## Rejected, and why

| Rejected | Reason |
|---|---|
| `@action(busy=[...], status=..., focus=...)` | a private DSL in a kwarg list; busy state is app policy, not framework mechanism |
| A `Form` class with `is_valid()`/`cleaned_data`/`errors` | correct at scale, but it is the component framework jQuery's failure produced; revisit only when two apps need cross-field validation |
| A selection engine, implicit iteration, `$()`-style chaining | jQuery's mush; and pyodex's DOM is small and owned |
| Build-time codegen of a UI module (Qt `uic` style) | a stale artifact to regenerate; fights "not a framework" |
| A TOML `[bindings]` table | the selector then lives in two places, and the handler name becomes a string with no jump-to-definition |
| Rewriting `on*` at boot instead of build | console CSP violations during boot; misses `load`/`error` |
| Evaluating the attribute value (htmx `hx-on`) | `new Function` cannot run under MV3 |
| Injecting bindings into a third-party DOM | VDOM re-render drops them; races the site's observer |
| `ui` as a name | reads as the page's UI when it means ours |
| Monkeypatch-style `Selection.extend` (`$.fn`) | namespace sprawl; register on one seam or not at all |

## Worked example

`examples/experimental/17-ssh/main.py`, before and after. Same behavior, same CSS, same
plugin API; the plugin's `mount` becomes `attach_to` and takes an element.

Before (59 lines): a six-line id preamble that runs at import, `global terminal`,
`int(port.value)`, a `try/except` per operation writing `status.textContent`, a
hand-rolled `set_busy()` over four widgets, and a `finally:` that restores focus.

After (37 lines):

```python
"""Connection form for the example's local SSH plugin."""

from pyodex import ctx, ext_page, handler, start
from secure_shell_plugin import SshTarget, SshTerminal


@start
def main():
    ctx.terminal = SshTerminal.attach_to(ext_page.get("#terminal"))
    ext_page.connection_status.text = "Ready. Enter the address of an SSH server you administer."
    ext_page.connect.disabled = False


@handler                              # pyodex-submit.prevent="connect"
def connect():
    target = SshTarget(ext_page.host.value, ext_page.user.value, ext_page.port.as_int())
    set_busy(True)
    ext_page.connection_status.text = f"Session active · {target}"
    try:
        code = ctx.terminal.connect(target)
        ext_page.connection_status.text = f"Disconnected · OpenSSH exited with status {code}"
    except Exception as exc:
        ext_page.connection_status.text = f"Disconnected · {exc}"
    finally:
        set_busy(False)
        ext_page.connect.focus()


@handler                              # pyodex-click="disconnect"
def disconnect():
    ctx.terminal.disconnect()


def set_busy(busy):
    for widget in (ext_page.host, ext_page.user, ext_page.port, ext_page.connect):
        widget.disabled = busy
    ext_page.disconnect.disabled = not busy
```

`ext_page.host` replaces six lookups; `ctx.terminal` replaces the global;
`ext_page.port.as_int()` replaces `int(port.value)`; one `try/except` remains for the
app's own messages; and `set_busy()` survives unchanged — it is app policy and should
stay app code.

Net: 22 lines of mechanism deleted, none of intent. That is the test passing.

## Evidence

The rules and the rejected list come from primary docs read directly (web search was
unavailable in the research environment, so nothing here rests on a search snippet):

- jQuery `$()` overloads, empty-set semantics, `.on()` delegation — api.jquery.com;
  displacement and the component-model critique — Wikipedia's jQuery entry, htmx's
  response essay.
- SQLAlchemy 2.0 migration (implicit-is-a-mistake, one-choice, forced `.unique()`) —
  docs.sqlalchemy.org/en/20/changelog/migration_20.html.
- pandas method chaining and its debugging cost — tomaugspurger.net.
- `@patch`, `store_attr`, `null`, `basic_repr` — fastcore.fast.ai.
- Textual reactive/`@work`/`Worker.state` — textual.textualize.io.
- `bind_value`, `BindableProperty`, polled active links — NiceGUI source.
- `param.depends`, `on_init`, batching — param.holoviz.org.
- Django bound forms (`is_valid`, `cleaned_data`, `errors`, `changed_data`) —
  docs.djangoproject.com.
- WXT entrypoints, `defineItem` storage, `ctx` lifecycle, `createShadowRootUi` —
  wxt.dev. Plasmo messaging conventions; `webext-core` protocol maps.
- GTK `GtkBuilder` signals, Qt `connectSlotsByName`, FXML `fx:id`/`onAction`, Android
  ViewBinding, WPF `x:Name` — the code-behind lineage.
- MV3 CSP defaults and the "cannot be relaxed" rule — developer.chrome.com; inline
  handler blocking — MDN `script-src`; `setAttribute` as a Trusted Types sink — MDN.
- Pyodide FFI: conversion rules, `create_proxy` lifetime, `run_sync`/JSPI restrictions
  and churn, `JsException.name/.message/.stack` — pyodide.org; pyodide-http's sync-XHR
  fallback.
- Playwright locators: strictness, re-resolution, the locator ladder, `or_`, shadow-DOM
  piercing — playwright.dev/python.
- Cargo layout, rustc `--explain`/`--json`, terraform `plan` diff/`-out`/`-detailed-exitcode`,
  copier `update`, `web-ext run` — the tooling precedents.

## Open questions

- **Closed shadow roots** and cross-origin iframes are unreachable from an isolated
  content script. Role/text matching pierces open roots; closed roots need a spike
  against a real web-component app.
- **Store review tolerance** for `prevent_default` on host-page navigation is unverified.
- **JSPI churn**: `run_sync` is still experimental and has moved repeatedly. It stays
  behind one choke point (`@blocking`), never spread through app code.
- **`attach_to` for a plugin that must be async** is still `@blocking` on the plugin
  method. Acceptable because it is plugin-author surface, not app surface, but a
  synchronous-by-construction variant may be better.

## Status

Implemented and verified:

| Phase | Surface | Proof |
|---|---|---|
| 1 | `ext_page` / `page` / `ctx`; `get` / `get_all` / `maybe`; `main.html` | L30 |
| 1 | `@handler` / `@bind`; `pyodex-<event>[.modifier]` binder, cached proxies, MutationObserver path | L30 |
| 1 | `on*` build rewriter (`onclick="connect"`, `…; return false` → `.prevent.stop`) | L30 (no `on*` ships) |
| 1 | Build validator: unknown handlers, unknown literal ids, duplicate ids, `difflib` suggestions | L30 build failure + `tests/test_pagecheck.py` |
| 1 | `@start(interactive=…)`; markup blocks take the pointer, empty space does not | L30, demo 01 |
| 2 | `attach_when` + `Mount`: attach on appearance, release on removal | L31 |
| 2 | `JsError`: a JS exception crossing into Python keeps its name and stack | L30 (`SyntaxError`) |
| 3 | `on_page` with `role`/`name`/`text`/`testid`/`any_of`, delegated at dispatch | L32 |
| 4 | `pyodex explain [--json]`: the receipt, cross-checked against the running app | L33 + `tests/test_tooling.py` |
| 5 | `pyodex dev`: rebuild in place (same directory), report errors, keep watching | L34 + `tests/test_tooling.py` |

Converted to the new surface: demos 01, 02, 03, 04, 06, 07, 08, 09, 10, 11, 12, 14,
15, 16, `examples/fireworks`, and `examples/experimental/17-ssh`. Each conversion keeps
its harness level unchanged — the level is the contract, so a conversion that needs the
test changed is wrong.

Not done yet:

1. **Engine mode** (`ui = "engine"`): `ext_page` and `attach_when` need a document;
   a SharedWorker has none. Demo 05 keeps `Overlay` until the agent-backed variant
   exists — it is the one demo that cannot be expressed in this surface today.
2. **Demo 13** drives a wasm canvas that belongs to the host page; it stays on the
   low-level API by design (it states its own cheat in its README).
3. **popup.py / panel.py** still write `innerHTML`: those pages have dedicated
   templates, so per-page markup would be a new feature, not a migration.
4. `Selection.extend`-style plugins, `Form`, typed storage — deliberately deferred
   (see "Rejected, and why").

L34 states the `pyodex dev` claim the browser can actually check: after an in-place
rebuild the extension keeps its origin (its id, and therefore its storage and
permissions) and serves the new files on a fresh navigation. It does not assert that
Chrome hot-reloads a running extension — that timing is not ours to control, and
pretending otherwise would be a test that lies. Phase 2's error translation is proven
by an invalid selector in L30 rather than by every JS error class; the mapping is
deliberately small and keeps the JS name it cannot classify.
