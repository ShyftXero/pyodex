# Demo 17 — SSH over Direct Sockets

Run OpenSSH in a Chrome Isolated Web App. This example is a **project-owned
plugin**, not a pyodex feature. [`secure_shell_plugin.py`](secure_shell_plugin.py)
loads the bundled libraries, starts their worker, and handles the terminal and
authentication prompts in Python. [The app](main.py) imports that local module:

```python
from pyodex import start
from secure_shell_plugin import SshTarget, SshTerminal

@start  # Pyodex calls this after loading the app.
def main():
    terminal = SshTerminal.mount("#terminal")
    return terminal.connect(SshTarget("example.net", "alice"))
```

Read `main.py` for the form and `SshTerminal` for the plugin API:

- `mount()` puts the terminal in an existing page element. Call it once per page.
- `connect()` runs the bundled OpenSSH client, shows its prompts, and returns its
  exit code. Call it again to reconnect after the session ends.
- `disconnect()` cancels startup or ends the current session.

OpenSSH handles encryption and host-key verification. The Python plugin connects
it to the browser; it is not a second SSH client. `@blocking` lets the app use
plain `def` without freezing the browser. The private `_SocketCleanup` helper is
specific to Secure Shell 0.82, not something every pyodex plugin needs.

The TOML lists the local Python module in `python_files` and its files in
`assets`. The only pieces from pyodex are generic asset lookup, ES module import,
and a Trusted Types-safe URL for a bundled worker. There is no app-authored JS.

The bundled SSH client is **Google's
[Secure Shell](https://chromewebstore.google.com/detail/secure-shell/iodihamcpbpeioajjeobimgagajmlibd)
0.82 build of OpenSSH 10.6p1**. Secure Shell's `wassh` and WASI glue, hterm and
virtual filesystem are also bundled. `wassh` opens TCP via Chrome's `TCPSocket`;
no pyodex socket shim, relay, or native helper is involved.

## Run locally

From the pyodex checkout. No Secure Shell extension install is needed:

```sh
uv run pyodex iwa dev examples/experimental/17-ssh --direct-sockets --port 8177
```

1. Enable **Isolated Web Apps** and **Isolated Web App Developer Mode** in
   `chrome://flags` if needed, then restart Chrome.
2. In `chrome://iwa-dev`, install a **Dev Mode Proxy** with the printed
   `http://127.0.0.1:8177/` URL. Keep the server running while using the app.
3. Open the installed app. Enter the host, account and port of an SSH server you
   administer. Allow local network access if Chrome asks. Compare the server's
   first-seen host fingerprint with a trusted copy before accepting it. The app
   keeps its own `known_hosts` in IndexedDB; it does not copy your SSH settings.

The printed HTTP URL serves app files, not SSH traffic. Opening it directly in a
normal tab does not enable Direct Sockets. This demo currently uses password or
keyboard-interactive login, not private-key import or SSH agent forwarding.

## Attribution

The ChromiumOS Authors maintain the upstream
[libapps source for Secure Shell 0.82](https://chromium.googlesource.com/apps/libapps/+/refs/tags/nassh-0.82/).
The [vendored files](vendor/) are unchanged copies from Google's Chrome Web Store
release. See the [libapps license](vendor/LICENSE-libapps) and
[original third-party notices](vendor/secure-shell-notices.txt) for OpenSSH,
OpenSSL, and the other dependencies. Only the current `ssh.wasm` is needed;
Mosh, SCP, SFTP, keygen, and OpenSSH 8.6 are not included.

SHA-256 of the bundled `ssh.wasm`:
`2a014b46f5e880020d09d862ca95fde00c459917a3b45ad4a341b6e9d15c7bcd`.

## Browser smoke test

```sh
uv run --with asyncssh python tests/iwa_ssh_smoke.py
```

On a machine without a graphical session, prefix the command with `xvfb-run -a`.
The smoke test defaults to stable Chrome (`--channel chrome`) and uses a disposable
browser profile, app and SSH server. It checks the real IWA origin, accepts the
fixture's exact host fingerprint, verifies password authentication and terminal
round-trip, and separately tests binary TCP echo through Python's socket shim.
It does not connect to your SSH server or use your normal browser profile. Changing
`--channel` selects a different browser but does not enable Direct Sockets in an
ordinary extension or tab. This is not part of the default extension gate.
