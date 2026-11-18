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


def test_trigger_mode_renders(tmp_path):
    """A watcher tool boots on match, not on click, and is exempt from the cap."""
    _, js = _build(tmp_path, '''[extension]
name = "Vault"
boot = "trigger"
triggers = [{ selector = "input[type=password]" }]
''')
    assert 'const CEILING = "trigger";' in js
    assert "input[type=password]" in js
    assert "watchTriggers" in js and "MutationObserver" in js
    # trigger boots are demand-driven, so the max_live gate must not apply
    assert 'mode !== "trigger" && !(await budgetOk())' in js


def test_keepalive_and_release_api_exist():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "pyodex" / "runtime" / "pyodex_runtime.py").read_text()
    for name in ("def keep_alive(", "def release(", "def want_boot(", "def on_load("):
        assert name in src


def test_password_manager_example_resolves():
    from pathlib import Path

    from pyodex.builder import resolve_project

    root = Path(__file__).resolve().parents[1] / "examples" / "password_manager"
    cfg = resolve_project(root)[1]
    assert cfg.boot == "trigger"
    assert cfg.triggers and cfg.triggers[0]["selector"] == "input[type=password]"
    assert cfg.reap_hidden_after == 0


def test_declarations_extracted(tmp_path):
    from pyodex.declarations import extract

    f = tmp_path / "main.py"
    f.write_text('''
from pyodex_runtime import on_load, watch_for, on_event, events

@on_load
def begin(): ...

@watch_for("input[type=password]")
def a(): ...

@watch_for(id="login")
def b(): ...

@on_event(events.CLICK, selector="#go")
def c(): ...
''')
    d = extract([f])
    assert d.on_load is True
    assert {"selector": "input[type=password]"} in d.triggers
    assert {"selector": "#login"} in d.triggers
    assert {"event": "page.click", "selector": "#go"} in d.events
    assert not d.problems


def test_declarations_report_unparseable(tmp_path):
    from pyodex.declarations import extract

    f = tmp_path / "main.py"
    f.write_text("@watch_for(SELECTOR_CONST)\ndef a(): ...\n")
    d = extract([f])
    assert d.triggers == [] and d.problems  # reported, never silently dropped


def test_boot_inferred_from_watch_for(tmp_path):
    """Declaring @watch_for is what sets trigger mode; no config needed."""
    from pyodex.builder import resolve_project

    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "Vault"\n')
    (tmp_path / "main.py").write_text(
        'from pyodex_runtime import watch_for\n@watch_for("input[type=password]")\ndef f(): ...\n')
    cfg = resolve_project(tmp_path)[1]
    assert cfg.boot == "trigger"
    assert cfg.triggers == [{"selector": "input[type=password]"}]
    assert cfg.declarations["on_load"] is False


def test_explicit_boot_beats_declarations(tmp_path):
    from pyodex.builder import resolve_project

    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "V"\nboot = "auto"\n')
    (tmp_path / "main.py").write_text(
        'from pyodex_runtime import watch_for\n@watch_for("#x")\ndef f(): ...\n')
    assert resolve_project(tmp_path)[1].boot == "auto"


def test_password_manager_example_uses_decorators():
    from pathlib import Path

    from pyodex.builder import resolve_project

    root = Path(__file__).resolve().parents[1] / "examples" / "password_manager"
    cfg = resolve_project(root)[1]
    assert cfg.boot == "trigger"  # inferred, not configured
    assert cfg.triggers[0]["selector"] == "input[type=password]"
    assert cfg.declarations["events"] == [{"event": "page.submit", "selector": "form"}]


def test_urlmatch_classification():
    from pyodex.declarations import classify

    assert classify("127.0.0.1") == {"kind": "host", "pattern": "127.0.0.1"}
    assert classify("*.mysite.com") == {"kind": "host", "pattern": "*.mysite.com"}
    assert classify("https://some.site.com") == {"kind": "prefix", "pattern": "https://some.site.com"}
    assert classify("https?://.*:?/.*.php")["kind"] == "regex"
    assert classify("re:^https://x/.*")["kind"] == "regex"
    # a URL wildcard must not degrade into a literal prefix
    wild = classify("https://*")
    assert wild["kind"] == "regex" and wild["pattern"] == "^https://.*$"
    deep = classify("https://*.mysite.com/*")
    assert deep["kind"] == "regex" and deep["pattern"].startswith("^https://.*\\.mysite\\.com/.*$")


def test_urlmatch_combines_with_watch_for(tmp_path):
    from pyodex.declarations import extract

    f = tmp_path / "main.py"
    f.write_text('''
from pyodex_runtime import urlmatch, watch_for

@urlmatch(["https://*", "*.mysite.com"])
@watch_for("input[type=password]")
def both(): ...
''')
    d = extract([f])
    assert len(d.triggers) == 1
    t = d.triggers[0]
    assert t["selector"] == "input[type=password]"
    assert {r["kind"] for r in t["url_rules"]} == {"regex", "host"}


def test_urlmatch_alone_triggers_boot(tmp_path):
    from pyodex.builder import resolve_project

    (tmp_path / "pyodex.toml").write_text('[extension]\nname = "Site"\n')
    (tmp_path / "main.py").write_text(
        'from pyodex_runtime import urlmatch\n@urlmatch(["127.0.0.1", "*.mysite.com"])\ndef f(): ...\n')
    cfg = resolve_project(tmp_path)[1]
    assert cfg.boot == "trigger"
    assert cfg.triggers == [{"url_rules": [
        {"kind": "host", "pattern": "127.0.0.1"}, {"kind": "host", "pattern": "*.mysite.com"}]}]


def test_urlmatch_renders_into_injector(tmp_path):
    _, js = _build(tmp_path, '[extension]\nname = "Site"\n', (
        "from pyodex_runtime import urlmatch\n@urlmatch(['127.0.0.1'])\ndef f(): ...\n"))
    assert "urlRulesMatch" in js and "127.0.0.1" in js


def test_generated_js_is_syntactically_valid(tmp_path):
    """A broken template must fail the build, not boot in a browser and fail silently.

    This exists because a Python-style adjacent-string-literal slipped into
    bootstrap.js once and produced a module that never executed — no console
    error, just a permanently "Loading Python…" overlay.
    """
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node not available")

    built, _ = _build(tmp_path, '[extension]\nname = "Syntax"\n')
    for name in ("injector.js", "bootstrap.js"):
        src = (built / name).read_text()
        proc = subprocess.run(["node", "--input-type=module", "--check"], input=src,
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{name}: {proc.stderr.strip()[:400]}"
