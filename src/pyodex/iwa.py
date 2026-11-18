"""Isolated Web App packaging (experimental).

An IWA is a signed web bundle served over `isolated-app://`. Unlike an MV3
extension it can use Direct Sockets (real TCP/UDP) and `<controlledframe>`, but
it cannot see other tabs' traffic. This module produces an IWA-shaped app from
the same project an extension is built from, and serves it locally so Chrome's
dev-mode sideloader can install it.

Not supported here: signing (`.swbn` + integrity block), update manifests, and
enterprise policy deployment. Those are prerequisites for anything beyond local
sideloading.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .builder import build_extension, resolve_project

IWA_NOTES = """Isolated Web App (experimental)
==============================

Install with developer flags (no signing, no policy):

1. chrome://flags
     #enable-isolated-web-apps          -> Enabled
     #enable-isolated-web-app-dev-mode -> Enabled
   Restart Chrome.
2. Serve this directory over HTTPS:
     uv run pyodex iwa dev <your project>
3. chrome://web-app-internals -> "Install IWA with Dev Mode Proxy"
     URL: the https://127.0.0.1:PORT/ URL printed by step 2.

What this gives you
-------------------
* A standalone `isolated-app://` window running the same Python app.
* Direct Sockets (TCPSocket / UDPSocket / TCPServerSocket) where the browser
  exposes them, so plain TCP/UDP without the local proxy.

What this does NOT give you
---------------------------
* Traffic inspection for other tabs (by design; IWAs are isolated).
* A signed bundle or update channel. For distribution you need `.swbn` signing
  plus `IsolatedWebAppInstallForceList` (enterprise) or a hosted update manifest.
* Any reach into the extension APIs (tabs, declarativeNetRequest, ...).
"""

# Content types Chrome's installer can trip over if served wrong.
EXTRA_TYPES = {
    ".wasm": "application/wasm",
    ".whl": "application/octet-stream",
    ".zip": "application/zip",
    ".mjs": "text/javascript",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
}


class _Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **EXTRA_TYPES}

    def end_headers(self) -> None:
        # IWA requires cross-origin isolation (COOP/COEP) and a strict-ish CSP.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  [iwa] {self.address_string()} {fmt % args}")


def _iwa_index(name: str, css: str, body: str, scripts: list[str]) -> str:
    tags = "\n".join(f'  <script src="{s}"></script>' for s in scripts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>{name}</title>
  <link rel="manifest" href="manifest.webmanifest" />
  <style>
    html, body {{ margin: 0; padding: 0; height: 100%; background: #0b0b0d; color: #fafafa;
                 font: 14px/1.4 system-ui, sans-serif; }}
    #pyodex-status {{ position: fixed; bottom: 8px; right: 8px; z-index: 2147483647;
      font: 11px/1.3 system-ui, sans-serif; color: #fff; background: rgba(0,0,0,.6);
      padding: 4px 8px; border-radius: 4px; opacity: .9; }}
    #pyodex-status.hidden {{ display: none; }}
    #pyodex-status.error {{ background: #b91c1c; }}
    #pyodex-error {{ position: fixed; inset: auto 0 0 0; max-height: 45vh; overflow: auto;
      margin: 0; padding: 10px 12px; font: 12px/1.45 ui-monospace, monospace;
      color: #fecaca; background: rgba(12,10,9,.96); border-top: 2px solid #b91c1c;
      white-space: pre-wrap; display: none; }}
    #pyodex-error.show {{ display: block; }}
    {css}
  </style>
</head>
<body>
{body}
  <div id="pyodex-status">Loading Python…</div>
  <pre id="pyodex-error"></pre>
{tags}
  <script type="module" src="bootstrap.js"></script>
</body>
</html>
"""


def build_iwa(target: Path, out: Path | None = None) -> Path:
    """Build an IWA-shaped app directory from an extension project."""
    project, cfg = resolve_project(target)
    base = build_extension(target)
    out = out or (project / "dist" / "iwa" / cfg.slug)
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(base, out)

    # IWA entry point replaces the click-through overlay page.
    css_file = project / "overlay.css"
    css = cfg.overlay_css or (css_file.read_text() if css_file.exists() else "")
    body_file = project / "overlay.body.html"
    body = cfg.overlay_body or (body_file.read_text() if body_file.exists() else "")
    (out / "index.html").write_text(_iwa_index(cfg.name, css, body, cfg.scripts))

    (out / "manifest.webmanifest").write_text(json.dumps({
        "name": cfg.name,
        "short_name": cfg.slug,
        "description": cfg.description,
        "start_url": "/index.html",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#0b0b0d",
        "theme_color": "#0b0b0d",
        **({"icons": [{"src": cfg.icon, "sizes": "128x128", "type": "image/png"}]} if cfg.icon else {}),
    }, indent=2) + "\n")

    (out / "IWA-INSTALL.txt").write_text(IWA_NOTES)
    return out


def _make_cert(dirpath: Path) -> tuple[Path, Path]:
    cert, key = dirpath / "iwa-cert.pem", dirpath / "iwa-key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "2", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


def serve_iwa(built: Path, *, port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Serve an IWA build over HTTPS (self-signed). Returns the running server."""
    certdir = built.parent / ".certs"
    certdir.mkdir(exist_ok=True)
    cert, key = certdir / "iwa-cert.pem", certdir / "iwa-key.pem"
    if not (cert.exists() and key.exists()):
        cert, key = _make_cert(certdir)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    handler = partial(_Handler, directory=str(built))
    srv = ThreadingHTTPServer((host, port), handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def dev(project: Path, *, port: int = 0) -> str:
    """Build, serve, and print sideload instructions. Returns the URL."""
    built = build_iwa(project)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    srv = serve_iwa(built, port=port or free)
    url = f"https://127.0.0.1:{srv.server_address[1]}/"
    print(f"serving {built} at {url}")
    print(IWA_NOTES)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
    return url
