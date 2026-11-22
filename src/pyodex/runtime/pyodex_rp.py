"""pyodex_rp: the RustPython tier's API surface.

RustPython's wasm build has no DOM bridge, so everything here goes through the
`js_vars` callbacks the bootstrap passes in. Keep it small: a tier that needs more
than this should be the Pyodide tier.
"""

js_vars = js_vars  # injected by the bootstrap


def status(text):
    js_vars["set_status"](text)


def count(selector):
    return js_vars["query_count"](selector)


def text_of(selector):
    return js_vars["element_text"](selector)


def set_attr(selector, name, value):
    return js_vars["set_attr"](selector, name, value)
