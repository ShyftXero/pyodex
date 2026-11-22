"""L12 — the RustPython tier (~5 MB), run in an MV3 sandbox page.

The sandbox is required: RustPython's wasm glue calls eval, and extension pages
cannot allow unsafe-eval. The shim is concatenated ahead of this file, so its names
are already in scope.
"""

status("rp booted")
set_attr("body", "data-rp", "inputs=" + str(count("input")))
print("rp stdout works")
