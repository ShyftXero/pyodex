"""RustPython in a NORMAL extension page (no sandbox), with the glue's eval stubbed."""

import pyodex_rp as ui

status("rp no-sandbox booted")
set_attr("body", "data-rp", "inputs=" + str(count("input")))
