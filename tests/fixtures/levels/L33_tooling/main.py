"""L33 — `pyodex explain` must describe the app that actually runs."""

from pyodex import ext_page, handler, start, status


@start
def main():
    ext_page.out.text = "ready"
    status(None)


@handler  # main.html: onclick="run"
def run():
    ext_page.out.text = "ran"
