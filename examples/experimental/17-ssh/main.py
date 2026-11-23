"""Connection form for the example's local SSH plugin."""

from pyodex import document, on, start
from secure_shell_plugin import SshTarget, SshTerminal

form = document.element("connection")
connect_button = document.element("connect")
disconnect_button = document.element("disconnect")
status = document.element("connection-status")
host, user, port = (document.element(name) for name in ("host", "user", "port"))
terminal: SshTerminal | None = None


@start  # Pyodex calls this once after loading the app.
def main():
    global terminal
    try:
        terminal = SshTerminal.mount("#terminal")
    except Exception as exc:
        status.textContent = str(exc)
        return

    status.textContent = "Ready. Enter the address of an SSH server you administer."
    set_busy(False)  # Enable Connect only after mounting succeeds.


@on(form, "submit", prevent_default=True)  # Keep form submission from navigating away.
def connect(_event):
    if terminal is None or connect_button.disabled:
        return
    try:
        target = SshTarget(host.value, user.value, int(port.value))
    except ValueError as exc:
        status.textContent = str(exc)
        return

    set_busy(True)
    status.textContent = f"Session active · {target}"
    try:
        code = terminal.connect(target)  # Wait for SSH to exit without blocking the browser.
        status.textContent = f"Disconnected · OpenSSH exited with status {code}"
    except Exception as exc:
        status.textContent = f"Disconnected · {exc}"
    finally:
        set_busy(False)
        connect_button.focus()


@on(disconnect_button, "click")
def disconnect(_event):
    if terminal is not None:
        terminal.disconnect()


def set_busy(busy):
    connect_button.disabled = busy
    disconnect_button.disabled = not busy
    for field in (host, user, port):
        field.disabled = busy  # Keep the destination fixed during a session.
