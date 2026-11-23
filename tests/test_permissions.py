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
