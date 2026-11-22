# Demo 06 in Python vs the same extension in JavaScript

Measured with `uv run tests/compare_js_python.py`: one tab, same page, fresh profile
per run, Chrome/Chromium 159 headless. The Python extension is
`examples/demos/06-api-tour`; the JavaScript one is
`examples/js-equivalent/06-api-tour` and does the same tour (chrome.runtime, tabs,
windows, storage local+session, action, DOM value/count/text/attr).

| | Python | JavaScript |
|---|---|---|
| launcher ready | 137 ms | 134 ms |
| **interpreter boot** | **5036 ms** | — (none) |
| tour execution | 310 ms | 53 ms |
| browser RSS | 1306 MB | 1192 MB |
| work done | 15 calls | 14 calls |

## Reading it honestly

- **Nothing is lost before the interpreter exists.** Both extensions get a usable
  launcher in ~135 ms, because in pyodex the launcher is a content script, not Python.
- **The cost is the boot, once.** ~5 s and ~114 MB in this configuration. That is the
  price of CPython-in-wasm, and it is paid on first use per tab (or never, if the tab
  stays dormant).
- **The tour itself is ~6× slower in Python** (310 ms vs 53 ms) for the same 15 API
  calls. In absolute terms it is a third of a second — acceptable for a tool that
  runs on demand, not for anything in a render loop.
- **Parity of capability is real**: the same chrome.* calls and the same DOM
  operations, with element handles (`page["input[name=user]"]`) instead of
  `document.querySelector`.

## What this says about the pitch

pyodex is not a faster way to write an extension; it is a way to reuse Python where
the alternative is rewriting logic in JS. The honest framing:

- **Use it** for analysis, automation, local state, and anything you already have in
  Python — the 0.3 s and 114 MB are irrelevant next to that.
- **Don't use it** for something that must react in milliseconds, or in forty tabs at
  once unless you use `ui = "engine"` (which drops the per-tab cost to ~1 MB, at the
  price of async DOM).
