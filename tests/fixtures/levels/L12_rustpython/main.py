"""L12 — the RustPython tier (~5 MB): wider stdlib than MicroPython, no DOM bridge."""

import pyodex_rp as ui

ui.status("rp booted")
ui.set_attr("body", "data-rp", f"inputs={ui.count('input')}")
print("rp stdout works")
