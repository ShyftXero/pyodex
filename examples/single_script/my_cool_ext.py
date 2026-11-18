# /// script
# requires-python = ">=3.12"
# dependencies = ["requests", "numpy"]
#
# [tool.pyext]
# name = "My Cool Ext"
# version = "0.1.0"
# description = "Single-file Python Chrome extension built with shyft_ext_bundle.py."
# matches = ["<all_urls>"]
# url_filter = "shyft"
# overlay_css = "#cool-panel { top: 60px; left: 16px; max-width: 320px; }"
# ///
"""Single-script extension. Build + zip with:

    uv run shyft_ext_bundle.py examples/single_script/my_cool_ext.py
"""

import numpy as np
import requests

from pyext_runtime import Overlay, on, status

ui = Overlay()
btn = ui.button("Compute 🧮", top=16, left=16)
panel = ui.panel(id="cool-panel")
panel.innerText = "click button"


@on(btn, "click")
def compute(_event):
    arr = np.random.rand(1000)
    try:
        ip = requests.get("https://httpbin.org/ip", timeout=10).json()["origin"]
    except Exception as e:  # noqa: BLE001
        ip = f"http failed: {e}"
    panel.innerText = f"numpy mean={arr.mean():.4f}\nyour ip={ip}"


status(None)
