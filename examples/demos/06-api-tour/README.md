
## How it compares to the JavaScript version

| | Python | JavaScript |
|---|---|---|
| launcher ready | 137 ms | 134 ms |
| interpreter boot | **5036 ms** | — (none) |
| tour execution (15 API calls) | 310 ms | 53 ms |
| browser RSS | 1306 MB | 1192 MB |

Measured by `uv run tests/compare_js_python.py` against
`examples/js-equivalent/06-api-tour` (same tour, same architecture).


`examples/js-equivalent/06-api-tour` performs the same tour in plain JavaScript, and
`uv run tests/compare_js_python.py` measures both: ~135 ms to a usable launcher in
either, a one-time ~5 s / ~114 MB interpreter cost on the Python side, and ~310 ms vs
~53 ms for the tour itself. Details and the honest reading:
[`docs/python-vs-js.md`](../../../docs/python-vs-js.md).
