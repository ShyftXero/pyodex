"""Standard app authoring needs no app-owned JavaScript."""

import ast
import json
import shutil
import tomllib
from pathlib import Path

import pytest

from pyodex.builder import build_extension
from pyodex.iwa import build_iwa
from pyodex.scaffold import init_project

ROOT = Path(__file__).resolve().parents[1]
SSH_DEMO = ROOT / "examples/experimental/17-ssh"
# The canvas demo deliberately supplies its own low-level Wasm runner.
LEGACY_HYBRID = {"13-doomlike"}


def assert_python_project(project: Path, *, vendored: bool = False) -> None:
    roots_to_skip = {"dist", "vendor"} if vendored else {"dist"}
    files = [p for p in project.rglob("*") if p.relative_to(project).parts[0] not in roots_to_skip]
    assert not [p for p in files if p.is_file() and p.suffix in {".js", ".mjs", ".cjs", ".ts"}]
    config = tomllib.loads((project / "pyodex.toml").read_text())
    assert not config["extension"].get("scripts"), project
    assert not config.get("vendor"), project
    for path in files:
        if path.suffix == ".html":
            assert "<script" not in path.read_text().lower(), path


def test_standard_scaffold_has_no_app_javascript(tmp_path):
    init_project(tmp_path, "Python-only", None)
    assert_python_project(tmp_path)
    out = build_extension(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text())
    assert (out / "pyodex.py").is_file()
    assert "pyodex.py" in manifest["web_accessible_resources"][0]["resources"]
    assert not (out / "app/secure_shell_plugin.py").exists()
    assert not (out / "vendor/plugin/wasm/ssh.wasm").exists()


def test_python_demos_keep_javascript_in_the_framework():
    projects = (ROOT / "examples/demos").iterdir()
    for project in projects:
        if project.is_dir() and project.name not in LEGACY_HYBRID:
            assert_python_project(project)


@pytest.mark.skipif(not (ROOT / ".git").exists() and not SSH_DEMO.exists(), reason="checkout-only example")
def test_ssh_is_an_example_plugin_not_a_framework_integration(tmp_path):
    assert_python_project(SSH_DEMO, vendored=True)
    entry = ast.parse((SSH_DEMO / "main.py").read_text())
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(entry))
    assert "asyncio" not in ast.unparse(entry)
    project = tmp_path / "project"
    shutil.copytree(SSH_DEMO, project, ignore=shutil.ignore_patterns("dist", "__pycache__"))
    out = build_iwa(project, tmp_path / "iwa", direct_sockets=True)
    manifest = json.loads((out / ".well-known/manifest.webmanifest").read_text())
    assert manifest["permissions_policy"]["direct-sockets"] == ["self"]
    assert (out / "app/secure_shell_plugin.py").read_bytes() == (project / "secure_shell_plugin.py").read_bytes()
    for name in ("vendor/plugin/wasm/ssh.wasm", "vendor/LICENSE-libapps", "vendor/wassh/js/worker.js"):
        assert (out / name).read_bytes() == (project / name).read_bytes()
    assert "ssh" not in (out / "pyodex_runtime.py").read_text().lower()
    assert not list((ROOT / "src/pyodex").rglob("ssh.wasm"))
