"""Pyodide package compatibility registry + tester.

Mirrors haru-pack's approach for "thick" apps: a package is *known good* only
if a real import test passed inside Pyodide (under Node), *known bad* if the
resolve/fetch/import failed. Anything else is *untested*.

Registry: `src/pyext/compat.json` (shipped) merged with `~/.cache/pyext/compat.json`
(local results). Builder refuses known-bad packages unless `--allow-bad`.

    pyext compat test requests numpy pandas
    pyext compat test --top 50
    pyext compat list [good|bad|untested]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import PYODIDE_VERSION
from .deps import cache_dir, ensure_pyodide
from .packages import Pin, ensure_in_store, pyodide_constraints, resolve

SHIPPED = Path(__file__).parent / "compat.json"
TOP_URL = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json"

# Packages that can never work: need a real OS, sockets, threads, subprocess, or native drivers.
# Seeded by reasoning; test results override.
SEED_BAD = {
    "aiobotocore": "needs aiohttp (raw sockets)",
    "aiohttp": "needs raw sockets (asyncio transports)",
    "uvicorn": "server; needs sockets",
    "starlette": "server framework (import may work, cannot serve)",
    "psycopg2": "native libpq, sockets",
    "psycopg": "sockets",
    "pymongo": "sockets",
    "redis": "sockets (use TcpSocket proxy)",
    "paramiko": "sockets, cryptography native",
    "pytest": "test runner; subprocess/threads",
    "setuptools": "build tool, not runtime",
    "grpcio": "native, sockets",
    "torch": "no wasm wheel",
    "tensorflow": "no wasm wheel",
}

# Import name differs from dist name.
IMPORT_NAMES = {
    "pyyaml": "yaml", "pillow": "PIL", "python-dateutil": "dateutil", "python-dotenv": "dotenv",
    "pyjwt": "jwt", "typing-extensions": "typing_extensions", "charset-normalizer": "charset_normalizer",
    "beautifulsoup4": "bs4", "scikit-learn": "sklearn", "opentelemetry-api": "opentelemetry",
    "markdown-it-py": "markdown_it", "rpds-py": "rpds", "jsonschema-specifications": "jsonschema_specifications",
    "typing-inspection": "typing_inspection", "annotated-types": "annotated_types", "pydantic-core": "pydantic_core",
    "importlib-metadata": "importlib_metadata", "attrs": "attr", "msgpack": "msgpack", "protobuf": "google.protobuf",
    "pyodide-http": "pyodide_http",
}


# Functional probes. Import alone is a weak signal (boto3 imports fine, then
# dies on sockets). A probe runs after import; pass = "good", fail = "bad".
# Packages without a probe get "import-only" (usable, unverified).
# Network probes hit a loopback HTTP server the runner starts, exposed as URL.
PROBES = {
    "requests": "import requests; assert requests.get(URL, timeout=10).json()['ok']",
    "urllib3": "import urllib3, json; assert json.loads(urllib3.request('GET', URL).data)['ok']",
    "httpx": "import httpx; assert httpx.get(URL).json()['ok']",
    "boto3": "import boto3, botocore.exceptions as be\ntry: boto3.client('s3', region_name='us-east-1', aws_access_key_id='x', aws_secret_access_key='y', endpoint_url=URL).list_buckets()\nexcept be.ClientError: pass  # server answered -> HTTP path works",
    "botocore": "import botocore.session, botocore.exceptions as be\ntry: botocore.session.get_session().create_client('s3', region_name='us-east-1', aws_access_key_id='x', aws_secret_access_key='y', endpoint_url=URL).list_buckets()\nexcept be.ClientError: pass",
    "s3transfer": "import s3transfer",
    "opentelemetry-api": "from opentelemetry import trace; trace.get_tracer('x').start_span('s').end()",
    "aiohttp": "import aiohttp, asyncio\nasync def f():\n    async with aiohttp.ClientSession() as s:\n        async with s.get(URL) as r: assert (await r.json())['ok']\nawait f()",
    "numpy": "import numpy as np; assert np.linalg.norm(np.ones(3)) > 1.7",
    "pandas": "import pandas as pd; assert pd.DataFrame({'a':[1,2]}).a.sum() == 3",
    "pydantic": "from pydantic import BaseModel\nclass M(BaseModel): x: int\nassert M(x='3').x == 3",
    "pyyaml": "import yaml; assert yaml.safe_load('a: 1')['a'] == 1",
    "jinja2": "import jinja2; assert jinja2.Template('{{x}}').render(x=1) == '1'",
    "cryptography": "from cryptography.fernet import Fernet; k=Fernet.generate_key(); assert Fernet(k).decrypt(Fernet(k).encrypt(b'x')) == b'x'",
    "pillow": "from PIL import Image; Image.new('RGB', (2, 2)).tobytes()",
    "pyjwt": "import jwt; assert jwt.decode(jwt.encode({'a':1}, 'k', algorithm='HS256'), 'k', algorithms=['HS256'])['a'] == 1",
    "jsonschema": "import jsonschema; jsonschema.validate(1, {'type': 'integer'})",
    "rich": "from rich.console import Console; Console(file=__import__('io').StringIO()).print('x')",
    "click": "import click\n@click.command()\ndef c(): pass\nc.main([], standalone_mode=False)",
    "protobuf": "from google.protobuf import struct_pb2; struct_pb2.Value(number_value=1).SerializeToString()",
    "redis": "import redis; redis.Redis(host='127.0.0.1', port=1, socket_timeout=1).ping()",
    "psycopg2": "import psycopg2; psycopg2.connect('host=127.0.0.1 port=1 connect_timeout=1')",
    "pymongo": "import pymongo; pymongo.MongoClient('127.0.0.1', 1, serverSelectionTimeoutMS=500).server_info()",
    "paramiko": "import paramiko; c=paramiko.SSHClient(); c.connect('127.0.0.1', port=1, timeout=1)",
    "uvicorn": "import uvicorn, asyncio\nasync def app(s, r, w): pass\nawait asyncio.wait_for(uvicorn.Server(uvicorn.Config(app, port=0)).serve(), 2)",
    "pytest": "import pytest; assert pytest.main(['--version']) == 0",
    "multidict": "from multidict import MultiDict; assert MultiDict(a=1)['a'] == 1",
    "yarl": "from yarl import URL as U; assert U('http://x/y').path == '/y'",
    "markupsafe": "from markupsafe import escape; assert str(escape('<')) == '&lt;'",
    "python-dateutil": "from dateutil import parser; parser.parse('2020-01-01')",
    "anyio": "import anyio\nasync def f(): await anyio.sleep(0)\nawait f()",
    "rpds-py": "from rpds import HashTrieMap; assert HashTrieMap({'a':1})['a'] == 1",
    "charset-normalizer": "from charset_normalizer import from_bytes; str(from_bytes(b'abc').best())",
    "idna": "import idna; idna.encode('example.com')",
    "certifi": "import certifi, os; assert os.path.exists(certifi.where())",
    "h11": "import h11; h11.Connection(h11.CLIENT).send(h11.Request(method='GET', target='/', headers=[('host','x')]))",
    "httpcore": "import httpcore; httpcore.ConnectionPool()",
    "starlette": "from starlette.applications import Starlette; Starlette()",
    "packaging": "from packaging.version import Version; assert Version('1.0') < Version('2')",
    "pygments": "from pygments import highlight, lexers, formatters; highlight('x=1', lexers.PythonLexer(), formatters.NullFormatter())",
    "tomlkit": "import tomlkit; assert tomlkit.parse('a=1')['a'] == 1",
    "fsspec": "import fsspec; fsspec.filesystem('memory').pipe('/a', b'x')",
    "filelock": "from filelock import FileLock\nwith FileLock('/tmp/x.lock'): pass",
    "platformdirs": "import platformdirs; platformdirs.user_cache_dir('x')",
    "python-dotenv": "from dotenv import dotenv_values; dotenv_values(stream=__import__('io').StringIO('A=1'))['A']",
    "six": "import six; six.text_type('x')",
    "attrs": "import attr\n@attr.s\nclass A: x = attr.ib()\nassert A(1).x == 1",
    "typing-extensions": "from typing_extensions import TypedDict",
    "referencing": "from referencing import Registry; Registry()",
    "tzdata": "import zoneinfo; zoneinfo.ZoneInfo('America/Chicago')",
    "markdown-it-py": "from markdown_it import MarkdownIt; assert '<p>' in MarkdownIt().render('x')",
    "pathspec": "import pathspec; pathspec.PathSpec.from_lines('gitwildmatch', ['*.py']).match_file('a.py')",
    "jmespath": "import jmespath; assert jmespath.search('a', {'a': 1}) == 1",
    "pluggy": "import pluggy; pluggy.PluginManager('x')",
    "iniconfig": "import iniconfig; iniconfig.IniConfig('x', data='[a]\\nb=1')['a']['b']",
    "pycparser": "import pycparser; pycparser.c_parser.CParser()",
    "cffi": "import cffi; cffi.FFI().cdef('int f(int);')",
    "annotated-types": "from annotated_types import Gt; Gt(0)",
}


@dataclass
class Result:
    name: str
    status: str  # good | bad
    version: str = ""
    reason: str = ""
    pyodide: str = PYODIDE_VERSION
    tested: float = 0.0


def _local_path() -> Path:
    return cache_dir() / "compat.json"


def load_registry() -> dict[str, dict]:
    reg: dict[str, dict] = {}
    for p in (SHIPPED, _local_path()):
        if p.exists():
            try:
                reg.update(json.loads(p.read_text()))
            except json.JSONDecodeError:
                pass
    for name, why in SEED_BAD.items():
        reg.setdefault(name, {"status": "bad", "reason": why, "source": "seed"})
    return reg


def status_of(name: str) -> tuple[str, str]:
    e = load_registry().get(name.lower().replace("_", "-"))
    return (e["status"], e.get("reason", "")) if e else ("untested", "")


def record(results: list[Result], *, into_shipped: bool = False) -> None:
    target = SHIPPED if into_shipped else _local_path()
    data = json.loads(target.read_text()) if target.exists() else {}
    for r in results:
        data[r.name] = {"status": r.status, "version": r.version, "reason": r.reason,
                        "pyodide": r.pyodide, "tested": int(r.tested), "source": "test"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(sorted(data.items())), indent=1) + "\n")


def import_name(dist: str) -> str:
    return IMPORT_NAMES.get(dist.lower(), dist.lower().replace("-", "_"))


# --------------------------------------------------------------------------- #
# Runner (Node + Pyodide)
# --------------------------------------------------------------------------- #

RUNNER_JS = r"""
const { loadPyodide } = await import(process.argv[2] + "/pyodide.mjs");
const [, , indexURL, wheelsJson, modName, probeB64] = process.argv;
const wheels = JSON.parse(wheelsJson);
const probe = probeB64 ? Buffer.from(probeB64, "base64").toString("utf8") : "";
const http = await import("node:http");
const srv = http.createServer((req, res) => { res.setHeader("content-type", "application/json"); res.end('{"ok": true}'); });
await new Promise((r) => srv.listen(0, "127.0.0.1", r));
const URL_ = `http://127.0.0.1:${srv.address().port}/`;
const trim = (e) => String(e && e.message || e).split("\n").filter((l) => l.trim()).slice(-3).join(" | ").slice(0, 400);
const out = { ok: false, probe: null };
try {
  const py = await loadPyodide({ indexURL, stdout: () => {}, stderr: () => {} });
  if (wheels.length) await py.loadPackage(wheels, { messageCallback: () => {} });
  await py.runPythonAsync(`import importlib; m = importlib.import_module(${JSON.stringify(modName)})`);
  out.ok = true;
  out.version = String(await py.runPythonAsync(`getattr(m, "__version__", "")`));
  if (probe) {
    try {
      await py.runPythonAsync(`URL = ${JSON.stringify(URL_)}\n` + probe);
      out.probe = "pass";
    } catch (e) { out.probe = "fail"; out.error = trim(e); }
  }
} catch (e) { out.error = trim(e); }
srv.close();
console.log(JSON.stringify(out));
process.exit(0);
"""


def _node() -> str:
    n = shutil.which("node")
    if not n:
        raise RuntimeError("node not found; needed to run Pyodide import tests")
    return n


def test_package(name: str, pyodide_version: str = PYODIDE_VERSION, timeout: int = 180) -> Result:
    name = name.lower().replace("_", "-")
    pyo = ensure_pyodide(pyodide_version)
    pyodide_lock = json.loads((pyo / "pyodide-lock.json").read_text())
    py_ver = pyodide_lock["info"]["python"].rsplit(".", 1)[0]
    now = time.time()

    # 1. resolve for wasm32
    try:
        pins = resolve([name], py_ver, pyodide_constraints(pyodide_lock))
    except RuntimeError as e:
        return Result(name, "bad", reason=f"resolve: {str(e).splitlines()[-1][:200]}", tested=now)

    # 2. fetch wheels into store
    wheels: list[str] = []
    version = ""
    try:
        for pin in pins:
            w = ensure_in_store(pin, pyodide_lock=pyodide_lock, pyodide_version=pyodide_version)
            wheels.append(str(w))
            if pin.key == name:
                version = pin.version
    except RuntimeError as e:
        return Result(name, "bad", version=version, reason=f"wheel: {e}"[:200], tested=now)

    # 3. import under Node
    with tempfile.TemporaryDirectory() as td:
        runner = Path(td) / "run.mjs"
        runner.write_text(RUNNER_JS)
        import base64

        probe = PROBES.get(name, "")
        pb = base64.b64encode(probe.encode()).decode() if probe else ""
        try:
            # JSPI flag: urllib3's Emscripten backend needs it for sync fetch on node < 24.
            proc = subprocess.run([_node(), "--experimental-wasm-stack-switching", str(runner), str(pyo),
                                   json.dumps(wheels), import_name(name), pb],
                                  capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return Result(name, "bad", version=version, reason="import timeout", tested=now)
    line = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        out = json.loads(line)
    except json.JSONDecodeError:
        return Result(name, "bad", version=version, reason=f"runner: {proc.stderr.strip()[-200:]}", tested=now)
    ver = out.get("version") or version
    if not out.get("ok"):
        return Result(name, "bad", version=ver, reason=f"import: {out.get('error', '')}", tested=now)
    if out.get("probe") == "pass":
        return Result(name, "good", version=ver, tested=now)
    if out.get("probe") == "fail":
        return Result(name, "bad", version=ver, reason=f"probe: {out.get('error', '')}", tested=now)
    return Result(name, "import-only", version=ver, reason="imports; no functional probe defined", tested=now)


def top_packages(n: int = 50) -> list[str]:
    req = urllib.request.Request(TOP_URL, headers={"User-Agent": "pyext"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        rows = json.load(r)["rows"]
    return [row["project"] for row in rows[:n]]


def check_pins(pins: list[Pin], *, allow_bad: bool = False) -> None:
    """Raise if any pin is known-bad. Called by the builder."""
    bad = []
    for p in pins:
        st, why = status_of(p.key)
        if st == "bad":
            bad.append(f"  {p.key}=={p.version}: {why}")
    if bad and not allow_bad:
        raise RuntimeError("known-incompatible packages (run `pyext compat test NAME` to re-check, or --allow-bad):\n"
                           + "\n".join(bad))
    if bad:
        print("warning: building with known-bad packages:\n" + "\n".join(bad))
