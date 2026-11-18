"""End-to-end test of the natural-Python socket layer.

Starts real servers (echo, HTTP, SMTP, Redis RESP, HTTPS), runs the proxy, then
boots Pyodide under Node and exercises plain-blocking stdlib code:

    uv run tests/e2e_natural.py

Proves the shims that matter for "plain Python": socket.create_connection,
makefile/readline (http.client), the smtplib dialogue, redis-py, select() and
TLS terminated by the proxy.

Requires: node (22+), uv, openssl. Skips cleanly if node is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYODIDE_CACHE = Path(os.environ.get("PYODEX_CACHE", Path.home() / ".cache" / "pyodex"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- #
# servers
# --------------------------------------------------------------------------- #


class Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.sendall(b"hello\n")
        data = self.request.recv(4096)
        self.request.sendall(b"echo:" + data)


class HTTP(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


class SMTP:
    """Just enough SMTP for smtplib.SMTP().sendmail()."""

    def __init__(self, port: int) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(5)
        self.port = port
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        conn.sendall(b"220 localhost ESMTP\r\n")
        in_data = False
        with conn, f:
            for raw in f:
                line = raw.strip()
                if in_data:
                    if line == b".":
                        in_data = False
                        conn.sendall(b"250 OK queued\r\n")
                    continue
                cmd = line.upper()
                if cmd.startswith(b"EHLO") or cmd.startswith(b"HELO"):
                    conn.sendall(b"250-localhost\r\n250 8BITMIME\r\n")
                elif cmd.startswith(b"MAIL") or cmd.startswith(b"RCPT"):
                    conn.sendall(b"250 OK\r\n")
                elif cmd.startswith(b"DATA"):
                    in_data = True
                    conn.sendall(b"354 send it\r\n")
                elif cmd.startswith(b"QUIT"):
                    conn.sendall(b"221 bye\r\n")
                    return
                else:
                    conn.sendall(b"250 OK\r\n")


class Redis:
    """RESP server: PING, SET, GET, ECHO, CLIENT SETINFO (ignored)."""

    def __init__(self, port: int) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(5)
        self.port = port
        self.store: dict[bytes, bytes] = {}
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    @staticmethod
    def _read_command(f: object) -> list[bytes] | None:
        line = f.readline()  # type: ignore[attr-defined]
        if not line:
            return None
        if not line.startswith(b"*"):
            return line.strip().split()
        n = int(line[1:])
        parts = []
        for _ in range(n):
            hdr = f.readline()  # type: ignore[attr-defined]
            if not hdr.startswith(b"$"):
                return None
            ln = int(hdr[1:])
            parts.append(f.read(ln + 2)[:-2])  # type: ignore[attr-defined]
        return parts

    def _session(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        with conn, f:
            while True:
                cmd = self._read_command(f)
                if not cmd:
                    return
                op = cmd[0].upper()
                if op == b"HELLO":
                    # RESP3 handshake: redis-py reads this as a dict.
                    conn.sendall(b"%3\r\n"
                                 b"$6\r\nserver\r\n$5\r\nredis\r\n"
                                 b"$5\r\nproto\r\n:3\r\n"
                                 b"$4\r\nrole\r\n$6\r\nmaster\r\n")
                elif op == b"PING":
                    conn.sendall(b"+PONG\r\n")
                elif op == b"SET":
                    self.store[cmd[1]] = cmd[2]
                    conn.sendall(b"+OK\r\n")
                elif op == b"GET":
                    v = self.store.get(cmd[1])
                    conn.sendall(b"$-1\r\n" if v is None else b"$%d\r\n%s\r\n" % (len(v), v))
                elif op == b"ECHO":
                    conn.sendall(b"$%d\r\n%s\r\n" % (len(cmd[1]), cmd[1]))
                elif op == b"CLIENT":
                    conn.sendall(b"+OK\r\n")
                elif op == b"QUIT":
                    conn.sendall(b"+OK\r\n")
                    return
                else:
                    conn.sendall(b"+OK\r\n")


def make_cert(dirpath: Path) -> tuple[Path, Path]:
    cert, key = dirpath / "cert.pem", dirpath / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
         "-addext", "basicConstraints=critical,CA:TRUE"],
        check=True, capture_output=True,
    )
    return cert, key


# --------------------------------------------------------------------------- #
# pyodide runner
# --------------------------------------------------------------------------- #

RUNNER = r"""
const [, , indexURL, runtimeDir, scriptPath, wheelsJson] = process.argv;
const wheels = JSON.parse(wheelsJson || "[]");
const fs = await import("node:fs");
const { loadPyodide } = await import(indexURL + "/pyodide.mjs");
const py = await loadPyodide({ indexURL, stdout: (s) => process.stdout.write(s + "\n"), stderr: (s) => process.stderr.write(s + "\n") });
globalThis.pyodide = py;
for (const f of ["pyodex_natural.py", "pyodex_runtime.py"]) {
  py.FS.writeFile("/home/pyodide/" + f, fs.readFileSync(runtimeDir + "/" + f, "utf8"));
}
if (wheels.length) await py.loadPackage(wheels, { messageCallback: () => {} });
await py.runPythonAsync("import sys; sys.path.insert(0,'/home/pyodide')");
await py.runPythonAsync(fs.readFileSync(scriptPath, "utf8"));
"""

PY = r'''
import json, socket, select, sys
import pyodex_natural as nat
nat.CAPS.update(nat.detect())
nat.CAPS["socket_native"] = False   # force the browser code path
nat.CAPS["idbfs"] = False           # no IndexedDB under Node
nat._install_socket(); nat._install_ssl(); nat._install_io()
config = json.loads(CONFIG)
ONLY = set(config.get("only") or [])
nat.config.proxy = f"ws://127.0.0.1:{config['proxy']}"   # test proxy, not the default port
results = {}

def check(name, fn):
    print(f"-> {name}", flush=True)
    try:
        fn(); results[name] = "ok"
    except Exception as e:
        import traceback; traceback.print_exc()
        results[name] = f"FAIL {type(e).__name__}: {e}"
    print(f"<- {name}: {results[name]}", flush=True)

# 1. raw socket + makefile
def t_echo():
    s = socket.create_connection(("localhost", config["echo"]), timeout=5)
    assert s.recv(100) == b"hello\n"
    s.sendall(b"ping\n")
    assert s.recv(100) == b"echo:ping\n"
    s.close()
    s = socket.create_connection(("localhost", config["echo"]), timeout=5)
    f = s.makefile("rb")
    assert f.readline() == b"hello\n"
    f.close(); s.close()
def check_if(name, fn):
    if not ONLY or name in ONLY:
        check(name, fn)

check_if("socket+makefile", t_echo)

# 2. select
def t_select():
    s = socket.create_connection(("localhost", config["echo"]), timeout=5)
    r, w, x = select.select([s], [], [], 2)
    assert s in r, f"not readable: {r}"
    assert s.recv(100) == b"hello\n"
    r, _, _ = select.select([s], [], [], 0.05)
    assert r == [], f"should be idle, got {r}"
    s.close()
check_if("select", t_select)

# 3. http.client (makefile/readline + sendall)
def t_http():
    import http.client
    c = http.client.HTTPConnection("localhost", config["http"], timeout=5)
    c.request("GET", "/thing")
    r = c.getresponse()
    body = json.loads(r.read())
    assert r.status == 200 and body["ok"] and body["path"] == "/thing", body
    c.close()
check_if("http.client", t_http)

# 4. smtplib
def t_smtp():
    import smtplib
    s = smtplib.SMTP("localhost", config["smtp"], timeout=5)
    s.sendmail("a@example.com", ["b@example.com"], "Subject: hi\r\n\r\nbody\r\n")
    s.quit()
check_if("smtplib", t_smtp)

# 5. redis-py
def t_redis():
    import redis
    # protocol=2 (RESP2) is redis-py's default for most app code and exercises the
    # socket layer without the RESP3 push-notification handshake.
    r = redis.Redis(host="localhost", port=config["redis"], socket_timeout=5, protocol=2)
    assert r.ping() is True
    r.set("k", "v"); assert r.get("k") == b"v"
    r.close()
check_if("redis", t_redis)

# 6. TLS terminated by proxy
def t_tls():
    # TLS is terminated by the proxy; our ssl shim only routes the socket to /tls.
    import ssl
    raw = socket.create_connection(("localhost", config["tls"]), timeout=8)
    # module-level ssl.wrap_socket is gone in 3.12+; SSLContext is the real API and
    # the one http.client uses.
    s = ssl.create_default_context().wrap_socket(raw, server_hostname="localhost")
    assert s.recv(100) == b"hello\n", "no greeting over TLS"
    s.sendall(b"secret\n")
    assert s.recv(100) == b"echo:secret\n"
    s.close()
check_if("tls", t_tls)

print("RESULTS " + json.dumps(results))
'''


def main() -> int:
    if not shutil.which("node") or not shutil.which("openssl"):
        print("skip: node/openssl not available")
        return 0
    pyo = PYODIDE_CACHE / "pyodide-314.0.8"
    if not pyo.exists():
        print(f"skip: no pyodide cache at {pyo} (run `pyodex fetch`)")
        return 0

    ports = {k: free_port() for k in ("echo", "http", "smtp", "redis", "tls", "proxy")}
    tmp = Path(tempfile.mkdtemp())
    cert, key = make_cert(tmp)

    echo = socketserver.ThreadingTCPServer(("127.0.0.1", ports["echo"]), Echo)
    threading.Thread(target=echo.serve_forever, daemon=True).start()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", ports["http"]), HTTP)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    tls_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    tls_ctx.load_cert_chain(cert, key)
    tlsd = socketserver.ThreadingTCPServer(("127.0.0.1", ports["tls"]), Echo)
    tlsd.socket = tls_ctx.wrap_socket(tlsd.socket, server_side=True)
    threading.Thread(target=tlsd.serve_forever, daemon=True).start()

    SMTP(ports["smtp"])
    Redis(ports["redis"])

    env = {**os.environ, "SSL_CERT_FILE": str(cert), "SSL_CERT_DIR": str(tmp)}
    proxy = subprocess.Popen(
        [sys.executable, str(ROOT / "proxy" / "python_websocket_proxy.py"),
         "--port", str(ports["proxy"]), "--allow", "localhost:*"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    time.sleep(1.5)

    # redis-py: pull the Pyodide-compatible wheel into the store and hand the
    # absolute path to the runner so loadPackage() can install it.
    from pyodex.packages import ensure_in_store, pyodide_constraints, resolve
    lock = json.loads((pyo / "pyodide-lock.json").read_text())
    py_ver = lock["info"]["python"].rsplit(".", 1)[0]
    wheels = []
    try:
        for pin in resolve(["redis"], py_ver, pyodide_constraints(lock)):
            wheels.append(str(ensure_in_store(pin, pyodide_lock=lock, pyodide_version=pyo.name.split("-", 1)[1])))
    except Exception as e:  # noqa: BLE001
        print(f"warn: could not prepare redis wheel: {e}")

    script = tmp / "probe.py"
    only = [p for p in os.environ.get("E2E_ONLY", "").split(",") if p]
    script.write_text(f'CONFIG = {json.dumps({**ports, "only": only})!r}\n' + PY)
    runner = tmp / "run.mjs"
    runner.write_text(RUNNER)

    lines: list[str] = []
    proc = subprocess.Popen(
        ["node", "--experimental-wasm-stack-switching", str(runner), str(pyo),
         str(ROOT / "src" / "pyodex" / "runtime"), str(script), json.dumps(wheels)],
        env={**os.environ, "E2E_ONLY": os.environ.get("E2E_ONLY", "")},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    deadline = time.time() + float(os.environ.get("E2E_TIMEOUT", "120"))
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line.rstrip(), flush=True)
            lines.append(line)
            if time.time() > deadline:
                proc.kill()
                print("timeout: killed node", file=sys.stderr)
                break
        proc.wait(timeout=30)
    finally:
        proxy.terminate()
        for srv in (echo, httpd, tlsd):
            srv.shutdown()

    try:
        perr = proxy.stderr.read().decode(errors="replace") if proxy.stderr else ""
    except Exception:  # noqa: BLE001
        perr = ""
    out = "".join(lines)
    if proc.returncode not in (0, -9):
        print(perr[-2000:], file=sys.stderr)
        return 1
    line = next((l for l in out.splitlines() if l.startswith("RESULTS ")), None)
    if not line:
        print("no RESULTS line", file=sys.stderr)
        return 1
    results = json.loads(line[len("RESULTS "):])
    bad = {k: v for k, v in results.items() if v != "ok"}
    if bad:
        print("--- proxy log ---\n" + perr[-2000:], file=sys.stderr)
    for k, v in results.items():
        print(f"  {k:<16} {v}")
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
