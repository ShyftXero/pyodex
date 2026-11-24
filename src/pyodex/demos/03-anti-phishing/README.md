# Anti-phishing link inspector

**What it demonstrates.** Scans the links on a page and flags lookalike domains with an explainable reason: digit substitution (`paypa1`), edit distance (`gogle`), brand-in-subdomain tricks.

**pyodex features.** Bulk `page.attr("a[href]", "href")`, `main.html` + `ext_page` + `@handler`, ordinary Python analysis (edit distance) with no JS involved

**How it is verified.** Harness level L16 — flags all three lookalike classes and leaves a clean domain alone.

```toml
[extension]
name = "Demo 03 Anti-Phishing"
version = ""
description = "Scans the links on a page and flags lookalike domains."
matches = ["<all_urls>"]
boot = "user"
http = false
```

```python
# The app is `main.py` + `main.html` — read those files.
# This README used to duplicate the source and drifted; it no longer does.
```

Build and load it:

```
uv run pyodex build examples/demos/03-anti-phishing
# chrome://extensions -> Developer mode -> Load unpacked -> dist/<slug>/
```
