import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "proxy"))

from pyodex.builder import ExtConfig, resolve_project
from pyodex.packages import link_cow, read_pep723, script_dependencies
from pyodex.pyodide_caps import diff, format_diff
from python_websocket_proxy import AllowList, Proxy


def test_pep723_parse(tmp_path):
    s = tmp_path / "x.py"
    s.write_text('# /// script\n# dependencies = ["requests", "numpy>=2"]\n#\n# [tool.pyodex]\n# name = "X"\n# ///\nprint(1)\n')
    meta = read_pep723(s)
    assert meta["dependencies"] == ["requests", "numpy>=2"]
    assert meta["tool"]["pyodex"]["name"] == "X"
    assert script_dependencies(s) == ["requests", "numpy>=2"]


def test_pep723_absent(tmp_path):
    s = tmp_path / "x.py"
    s.write_text("print(1)\n")
    assert read_pep723(s) == {}


def test_pep723_multiple_blocks(tmp_path):
    s = tmp_path / "x.py"
    s.write_text("# /// script\n# ///\n# /// script\n# ///\n")
    with pytest.raises(ValueError):
        read_pep723(s)


def test_resolve_project_script(tmp_path):
    s = tmp_path / "cool_ext.py"
    s.write_text('# /// script\n# [tool.pyodex]\n# name = "My Cool"\n# [tool.pyodex.vendor]\n# "a.js" = "https://x/a.js"\n# ///\n')
    root, cfg = resolve_project(s)
    assert root == tmp_path and cfg.entry == "cool_ext.py"
    assert cfg.name == "My Cool" and cfg.slug == "my-cool"
    assert cfg.vendor == {"a.js": "https://x/a.js"}


def test_resolve_project_dir(tmp_path):
    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "Dir Ext"\n')
    root, cfg = resolve_project(tmp_path)
    assert cfg.slug == "dir-ext" and cfg.entry == "main.py"


def test_resolve_project_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_project(tmp_path)


def test_slug():
    assert ExtConfig(name="  Héllo  World!! ").slug == "h-llo-world"


def test_link_cow_readonly(tmp_path):
    src = tmp_path / "store" / "a.whl"
    src.parent.mkdir()
    src.write_bytes(b"data")
    src.chmod(0o444)
    dst = tmp_path / "out" / "a.whl"
    method = link_cow(src, dst)
    assert method in ("reflink", "hardlink", "copy")
    assert dst.read_bytes() == b"data"
    with pytest.raises(PermissionError):
        open(dst, "ab").write(b"x")
    assert src.read_bytes() == b"data"


def test_allowlist():
    al = AllowList(["localhost:*", "10.0.0.0/8:5432", "*.corp.example:80-90"])
    assert al.allows("localhost", 1)
    assert not al.allows("evil.com", 80)
    assert al.allows("10.2.3.4", 5432)
    assert not al.allows("10.2.3.4", 5433)
    assert al.allows("db.corp.example", 85)
    assert not al.allows("db.corp.example", 91)


def test_allowlist_bad_rule():
    with pytest.raises(ValueError):
        AllowList(["noport"])


def test_proxy_resolve():
    p = Proxy(("localhost", 6379), AllowList(["localhost:*"]), token="s3", connect_timeout=1)
    with pytest.raises(PermissionError):
        p.resolve("/")
    assert p.resolve("/?token=s3") == ("localhost", 6379, False)
    assert p.resolve("/tls/localhost/443?token=s3") == ("localhost", 443, True)
    with pytest.raises(PermissionError):
        p.resolve("/tcp/evil/80?token=s3")
    with pytest.raises(LookupError):
        p.resolve("/what/ever?token=s3")


def test_caps_diff(monkeypatch, tmp_path):
    import pyodex.pyodide_caps as pc

    monkeypatch.setattr(pc, "CAPS_DIR", tmp_path)
    (tmp_path / "1.json").write_text(json.dumps({"jspi": False, "ffi": ["a"], "packages": {"x": "1", "y": "1"}}))
    (tmp_path / "2.json").write_text(json.dumps({"jspi": True, "ffi": ["a", "b"], "packages": {"x": "2", "z": "1"}}))
    d = diff("1", "2")
    assert d["changed"]["jspi"] == (False, True)
    assert d["added"]["ffi"] == ["b"]
    assert d["added"]["packages"] == {"z": "1"} and d["removed"]["packages"] == {"y": "1"}
    assert d["changed"]["packages"] == {"x": ("1", "2")}
    assert "jspi" in format_diff(d)


def _build(tmp_path, toml, entry="print('hi')\n"):
    from pyodex.builder import build_extension

    (tmp_path / "pyodex.toml").write_text(toml)
    (tmp_path / "main.py").write_text(entry)
    built = build_extension(tmp_path, tmp_path / "out")
    return built, (built / "injector.js").read_text()


def test_boot_defaults_to_user(tmp_path):
    """Default: no interpreter until the user clicks the launcher."""
    _, js = _build(tmp_path, '[extension]\nname = "Lazy Ext"\n')
    assert 'const CEILING = "user";' in js
    assert "const IDLE_MS = 600000;" in js
    assert "const MAX_LIVE = 0;" in js
    assert "attachShadow" in js
    assert "page.heartbeat" in js
    assert js.count("document.documentElement.appendChild(iframe)") == 1


def test_on_load_infers_visible_boot(tmp_path):
    """Declaring a load-time hook is the sanctioned way to ask for auto-boot."""
    import pyodex.builder as b

    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "Onload"\n')
    (tmp_path / "main.py").write_text("from pyodex_runtime import on_load\n@on_load\ndef go(): pass\n")
    cfg = b.resolve_project(tmp_path)[1]
    assert cfg.boot == "visible"


def test_explicit_boot_wins_over_on_load(tmp_path):
    import pyodex.builder as b

    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "Onload"\nboot = "user"\n')
    (tmp_path / "main.py").write_text("from pyodex_runtime import on_load\n@on_load\ndef go(): pass\n")
    assert b.resolve_project(tmp_path)[1].boot == "user"


def test_legacy_lazy_false_maps_to_auto(tmp_path):
    _, js = _build(tmp_path, '[extension]\nname = "Eager"\nlazy = false\nidle_timeout = 0\n')
    assert 'const CEILING = "auto";' in js and "const IDLE_MS = 0;" in js


def test_boot_mode_validated(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        _build(tmp_path, '[extension]\nname = "Bad"\nboot = "whenever"\n')


def test_max_live_and_reap_hidden_render(tmp_path):
    _, js = _build(tmp_path, '[extension]\nname = "Budget"\nboot = "visible"\nmax_live = 4\nreap_hidden_after = 60\n')
    assert "const MAX_LIVE = 4;" in js
    assert "const REAP_HIDDEN_MS = 60000;" in js
    assert 'const CEILING = "visible";' in js
