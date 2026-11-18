"""datever: package version == UTC build timestamp (haru-pack's scheme)."""

from __future__ import annotations

import datetime as dt
import os


def datever(now: dt.datetime | None = None) -> str:
    """YYYYMMDDHHMMSS in UTC. Monotonic, PEP 440 valid as a bare number."""
    if os.environ.get("PYODEX_VERSION"):
        return os.environ["PYODEX_VERSION"]
    return (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%d%H%M%S")


def chrome_version(v: str) -> str:
    """Chrome accepts 1-4 dot-separated ints <= 65535: 20261118140200 -> 2026.11.18.1402."""
    if len(v) != 14 or not v.isdigit():
        return v
    return f"{v[0:4]}.{int(v[4:6])}.{int(v[6:8])}.{int(v[8:12])}"
