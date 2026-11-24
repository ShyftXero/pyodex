"""The page's static contract: attribute sugar, ids, handler names."""

from pathlib import Path

import pytest

from pyodex.pagecheck import check, handlers_in, parse_on_value, prepare, rewrite


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("connect", ("connect", ())),
        ("connect()", ("connect", ())),
        (" connect ", ("connect", ())),
        ("connect(event)", ("connect", ())),
        ("connect(this)", ("connect", ())),
        ("connect(); return false", ("connect", ("prevent", "stop"))),
    ],
)
def test_accepted_on_values(raw, expected):
    assert parse_on_value(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "return false",
        "alert('x')",
        "a(); b()",
        "connect(1+1)",
        "this.value = 'x'",
        "javascript:connect()",
        "connect(1)",
    ],
)
def test_rejected_on_values(raw):
    with pytest.raises(ValueError):
        parse_on_value(raw)


def test_rewrite_maps_sugar_and_keeps_quotes():
    body = '<form onsubmit="connect(); return false"><b onclick=\'go\'></b></form>'
    out, warnings, errors = rewrite(body)
    assert out == '<form pyodex-submit.prevent.stop="connect"><b pyodex-click=\'go\'></b></form>'
    assert not warnings and not errors


def test_rewrite_skips_raw_text_and_comments():
    body = (
        '<pre> onclick="x" </pre>'
        '<code>onmouseover="y"</code>'
        '<script>el.onclick="z"</script>'
        '<!-- onclick="c" -->'
        '<b onclick="go"></b>'
    )
    out, warnings, errors = rewrite(body)
    assert not warnings and not errors
    # Examples inside raw-text / comment regions survive byte-for-byte.
    assert '<pre> onclick="x" </pre>' in out
    assert '<code>onmouseover="y"</code>' in out
    assert '<script>el.onclick="z"</script>' in out
    assert '<!-- onclick="c" -->' in out
    # A real attribute outside those regions is still rewritten.
    assert '<b pyodex-click="go"></b>' in out


def test_rewrite_reports_code_values():
    out, _warnings, errors = rewrite('<b onclick="alert(1)"></b>')
    assert "pyodex-click" not in out
    assert errors and "not JavaScript" in errors[0]


def test_rewrite_rejects_readystatechange():
    _out, _warnings, errors = rewrite('<b onreadystatechange="go"></b>')
    assert errors and "legacy XHR" in errors[0]


def test_rewrite_warns_on_keypress():
    _out, warnings, errors = rewrite('<input onkeypress="go">')
    assert not errors
    assert warnings and "keydown" in warnings[0]


def _sources(tmp_path: Path, main: str, html: str | None = None) -> list[tuple[Path, str]]:
    path = tmp_path / "main.py"
    path.write_text(main)
    return [(path, main)]


MAIN = """\
from pyodex import ext_page, handler


@handler
def connect():
    ext_page.host.value


@handler("save_target")
def save():
    ext_page.get("#missing").text
"""


def test_handler_names_reads_bare_and_named(tmp_path):
    found = handlers_in(_sources(tmp_path, MAIN))
    assert set(found) == {"connect", "save_target"}


def test_check_reports_unknown_handler_with_suggestion(tmp_path):
    problems = check('<form pyodex-submit="conect"></form>', _sources(tmp_path, MAIN))
    handler_problems = [p for p in problems if "no handler named" in p]
    assert len(handler_problems) == 1
    assert 'Did you mean "connect"' in handler_problems[0]


def test_check_reports_unknown_id(tmp_path):
    problems = check("<div id='host'></div>", _sources(tmp_path, MAIN))
    assert any("#missing" in p and "no element id" in p for p in problems)


def test_check_reports_duplicate_id(tmp_path):
    problems = check('<div id="host"></div>\n<p id="host"></p>', _sources(tmp_path, MAIN))
    assert any("duplicate id" in p for p in problems)


def test_check_is_quiet_when_everything_matches(tmp_path):
    main = 'from pyodex import ext_page, handler\n\n\n@handler\ndef connect():\n    ext_page.host.value\n'
    assert check('<input id="host" pyodex-click="connect">', _sources(tmp_path, main)) == []


def test_prepare_rewrites_then_validates(tmp_path):
    main = 'from pyodex import handler\n\n\n@handler\ndef connect():\n    pass\n'
    (tmp_path / "main.py").write_text(main)
    body, problems = prepare('<form onsubmit="connect(); return false"></form>', tmp_path, "main.py", [])
    assert "pyodex-submit.prevent.stop" in body
    assert problems == []


def test_prepare_stops_on_a_typo(tmp_path):
    main = 'from pyodex import handler\n\n\n@handler\ndef connect():\n    pass\n'
    (tmp_path / "main.py").write_text(main)
    _body, problems = prepare('<form onsubmit="conect"></form>', tmp_path, "main.py", [])
    assert problems and "Did you mean" in problems[0]


def test_css_selectors_are_not_checked(tmp_path):
    main = 'from pyodex import ext_page\n\n\next_page_obj = ext_page.get("div > p")\n'
    assert check("<div><p>x</p></div>", _sources(tmp_path, main)) == []


def test_hyphenated_ids_are_checked(tmp_path):
    main = 'from pyodex import ext_page\n\n\next_page.get("#some-id")\n'
    assert check('<div id="other-id"></div>', _sources(tmp_path, main)) != []
    assert check('<div id="some-id"></div>', _sources(tmp_path, main)) == []

