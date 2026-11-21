"""L6 — the extension is installed for the *page's* benefit.

The test page fetches a Pyodide-style CDN URL itself (like the Pyodide loader
does). With this extension installed that fetch is answered from local cache
after the first population, and the page never touches the CDN again. This
extension's own Python is irrelevant here, so main.py stays empty.
"""

from pyodex_runtime import status

status("L6 extension loaded")
