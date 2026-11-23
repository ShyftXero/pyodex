"""The permission table in the README must match what the builder actually does."""

from pathlib import Path

import pytest

from pyodex.builder import _config_from_data, _derived_permissions

CASES = {
    "alarms": "chrome.alarms.create('x', 1)",
    "contextMenus": "chrome.contextMenus.add('i', 't')",
    "notifications": "chrome.notifications.notify('i', 't', 'm')",
    "cookies": "chrome.cookies.get(u, 'n')",
    "downloads": "chrome.downloads.download(u)",
    "bookmarks": "chrome.bookmarks.search('')",
    "history": "chrome.history.search('')",
    "identity": "chrome.identity.get_auth_token()",
    "declarativeNetRequest": "chrome.dnr.add_rule(1, '/x')",
    "webRequest": 'chrome.on("webRequest.onBeforeRequest", f)',
    "activeTab": "chrome.tabs.capture_visible_tab()",
}


@pytest.mark.parametrize("permission,source", sorted(CASES.items()))
def test_permission_is_derived(tmp_path: Path, permission: str, source: str):
    (tmp_path / "main.py").write_text(source)
    cfg = _config_from_data({}, default_name="t", default_entry="main.py")
    assert permission in _derived_permissions(tmp_path, cfg), (
        f"{source!r} must add the {permission!r} permission"
    )


def test_dnr_test_also_requests_the_feedback_permission(tmp_path: Path):
    (tmp_path / "main.py").write_text("chrome.dnr.test(u)")
    cfg = _config_from_data({}, default_name="t", default_entry="main.py")
    got = _derived_permissions(tmp_path, cfg)
    assert "declarativeNetRequest" in got
    assert "declarativeNetRequestFeedback" in got


def test_unmentioned_permissions_are_not_added(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('no chrome here')")
    cfg = _config_from_data({}, default_name="t", default_entry="main.py")
    assert _derived_permissions(tmp_path, cfg) == []


def test_escape_hatch_can_be_disabled(tmp_path: Path):
    """A trigger-only app can refuse to offer a launcher anywhere else.

    The default keeps a way to run the app anyway; `escape_hatch = false` is for apps
    that mean it ("never on other pages"), and the rendered flag must follow the config.
    """
    from pyodex.builder import build_extension

    (tmp_path / "pyodex.toml").write_text("""[extension]
name = "Hatch Off"
matches = ["<all_urls>"]
boot = "trigger"
escape_hatch = false
http = false
""")
    (tmp_path / "main.py").write_text(
        "from pyodex_runtime import urlmatch, watch_for\n\n"
        '@urlmatch(["*://*/article/*"])\n@watch_for("article")\ndef go():\n    pass\n'
    )
    out = build_extension(tmp_path)
    injector = (out / "injector.js").read_text()
    assert "ESCAPE_HATCH = false" in injector


def test_escape_hatch_defaults_on(tmp_path: Path):
    from pyodex.builder import build_extension

    (tmp_path / "pyodex.toml").write_text("""[extension]
name = "Hatch On"
matches = ["<all_urls>"]
boot = "trigger"
http = false
""")
    (tmp_path / "main.py").write_text(
        "from pyodex_runtime import urlmatch, watch_for\n\n"
        '@urlmatch(["*://*/article/*"])\n@watch_for("article")\ndef go():\n    pass\n'
    )
    out = build_extension(tmp_path)
    assert "ESCAPE_HATCH = true" in (out / "injector.js").read_text()


def test_every_built_tier_emits_the_icon_its_manifest_names(tmp_path: Path):
    """A manifest that names a file the build never wrote is silently fatal.

    Chrome rejects the whole extension, so the symptom is "no content script, no overlay"
    with no error surface at all. This caught the MicroPython and RustPython tiers
    declaring icon-128.png while only the Pyodide path emitted it.
    """
    import json

    from pyodex.builder import build_extension

    for name, runtimes in (("Pyodide", ""), ("MicroPython", 'runtime = "micropython"')):
        project = tmp_path / name
        project.mkdir()
        (project / "pyodex.toml").write_text(f"""[extension]
name = "Icon {name}"
matches = ["<all_urls>"]
runtime_base = "bundled"
{chr(10) + runtimes if runtimes else ""}
""")
        (project / "main.py").write_text(
            "from pyodex_runtime import Overlay\n\ndocument.body.appendChild(" 
            "document.createElement('div')).textContent = 'hi'\n"
        )
        out = build_extension(project)
        manifest = json.loads((out / "manifest.json").read_text())
        icon = manifest.get("icons", {}).get("128")
        assert icon, f"{name}: manifest declares no icon"
        assert (out / icon).is_file(), f"{name}: manifest names {icon} but the build did not emit it"


def test_finish_refuses_a_manifest_that_names_a_file_it_did_not_emit(tmp_path: Path):
    """The single exit verifies; that is what makes 'every path emits the icon' true.

    Rather than trusting four build paths to remember, _finish checks the manifest against
    the directory and fails loudly — Chrome's own failure mode here is silence.
    """
    import pytest

    from pyodex.builder import ExtConfig, _finish

    out = tmp_path / "dist"
    out.mkdir()
    (out / "manifest.json").unlink(missing_ok=True)
    cfg = ExtConfig(name="T", version="1", description="d", entry="main.py", cache=False)

    # injector.js is named by every manifest's content_scripts and was not emitted here
    with pytest.raises(RuntimeError, match="did not emit"):
        _finish(out, tmp_path, cfg, ["overlay.html"])


def test_finish_emits_the_icon_whatever_the_call_site(tmp_path: Path):
    from pyodex.builder import ExtConfig, _finish

    for name, icon in (("default", ""), ("declared", "my.png")):
        out = tmp_path / name
        out.mkdir()
        (out / "overlay.html").write_text("x")
        (out / "injector.js").write_text("x")
        (out / "bg.js").write_text("x")  # named by the manifest when the runtime is remote
        if icon:
            (tmp_path / icon).write_bytes(b"png")
        cfg = ExtConfig(name="T", version="1", description="d", entry="main.py", icon=icon, cache=False)
        _finish(out, tmp_path, cfg, ["overlay.html", "injector.js"])
        assert (out / (icon or "icon-128.png")).is_file()
