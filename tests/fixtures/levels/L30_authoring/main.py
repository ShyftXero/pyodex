"""L30 — the app authoring surface.

Proves: markup attributes bind to @handler functions, the form submit is
prevented by `.prevent`, repeated clicks reuse the same binding, and a strict
`ext_page.get()` fails with a message that names the element it searched for.
"""

import pyodex
from pyodex import JsError, ext_page, handler, start, status

UNKNOWN = "#nope"  # a runtime value: only literal selectors are checked at build time


@start
def main():
    ext_page.out.text = "ready"
    ext_page.names.text = pyodex._handler_names()  # proves the registry
    status(None)


@handler  # main.html: pyodex-click="bump"
def bump():
    ext_page.count.text = str(int(ext_page.count.text) + 1)


@handler  # main.html: pyodex-submit.prevent="submit_form"
def submit_form():
    ext_page.out.text = "submitted:" + ext_page.name.value


@handler  # main.html: pyodex-click="lookup_miss"
def lookup_miss():
    try:
        ext_page.get(UNKNOWN)
    except LookupError as exc:
        ext_page.out.text = str(exc).splitlines()[0]


@handler  # main.html: onclick="bad_selector"
def bad_selector():
    try:
        ext_page.get("[[")  # invalid selector: JS raises SyntaxError
    except JsError as exc:
        ext_page.out.text = f"js:{exc.js_name}"
