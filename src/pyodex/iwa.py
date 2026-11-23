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
import threading
from functools import partial
from html import escape
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
2. Start a loopback dev server:
     uv run pyodex iwa dev <your project>
3. Open chrome://iwa-dev (older Chrome: chrome://web-app-internals) and
   install a Dev Mode Proxy using the http://127.0.0.1:PORT/ URL from step 2.

What this gives you
-------------------
* A standalone `isolated-app://` window running the same Python app.
* With --direct-sockets, the manifest opts in to Direct Sockets and local
  network access. Chrome still decides which capabilities are available.

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


def _iwa_index(name: str, body: str, scripts: list[str]) -> str:
    tags = "\n".join(f'  <script src="{escape(s, quote=True)}"></script>' for s in scripts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>{escape(name)}</title>
  <link rel="manifest" href="/.well-known/manifest.webmanifest" />
  <link rel="stylesheet" href="iwa.css" />
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


def _iwa_css(css: str) -> str:
    return f"""
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
"""


def build_iwa(target: Path, out: Path | None = None, *, direct_sockets: bool = False) -> Path:
    """Build a dev-installable IWA from a project with bundled Pyodide."""
    project, cfg = resolve_project(target)
    if cfg.runtime not in ("", "pyodide") or cfg.runtime_base != "bundled":
        raise ValueError("IWA builds require the bundled Pyodide runtime")
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
    (out / "index.html").write_text(_iwa_index(cfg.name, body, cfg.scripts))
    (out / "iwa.css").write_text(_iwa_css(css))
    (out / "iwa-icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192" viewBox="0 0 192 192">'
        '<rect width="192" height="192" rx="32" fill="#18181b"/>'
        '<path d="m48 58 42 38-42 38m54 0h42" fill="none" stroke="#f43f5e" '
        'stroke-linecap="round" stroke-linejoin="round" stroke-width="12"/></svg>\n'
    )
    (out / ".well-known").mkdir()
    (out / ".well-known" / "manifest.webmanifest").write_text(json.dumps({
        "id": "/",
        "version": json.loads((out / "manifest.json").read_text())["version"],
        "name": cfg.name,
        "short_name": cfg.slug,
        "description": cfg.description,
        "start_url": "/index.html",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "background_color": "#0b0b0d",
        "theme_color": "#0b0b0d",
        "icons": [{"src": "/iwa-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        "permissions_policy": {
            "cross-origin-isolated": ["self"],
            **({feature: ["self"] for feature in (
                "direct-sockets", "local-network", "loopback-network",
            )} if direct_sockets else {}),
        },
    }, indent=2) + "\n")

    (out / "IWA-INSTALL.txt").write_text(IWA_NOTES)
    return out


def serve_iwa(built: Path, *, port: int = 0) -> ThreadingHTTPServer:
    """Serve the IWA to Chrome's dev proxy over loopback HTTP only."""
    handler = partial(_Handler, directory=str(built))
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def dev(project: Path, *, port: int = 0, direct_sockets: bool = False) -> str:
    """Build, serve, and print sideload instructions. Returns the URL."""
    built = build_iwa(project, direct_sockets=direct_sockets)
    with serve_iwa(built, port=port) as srv:
        url = f"http://127.0.0.1:{srv.server_port}/"
        print(f"serving {built} at {url}")
        print(IWA_NOTES, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            srv.shutdown()
    return url
