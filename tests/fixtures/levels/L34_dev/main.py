"""L34 — `pyodex dev` rebuilds in place: same extension, new files."""

from pyodex import ext_page, start, status


@start
def main():
    ext_page.from_python.text = "python-ran"
    status(None)
