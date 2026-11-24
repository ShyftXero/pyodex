"""Connection form for the example's local SSH plugin."""

from secure_shell_plugin import SshTarget, SshTerminal

from pyodex import ctx, ext_page, handler, start


@start
def main():
    # attach_to takes the element, so which document is decided before the call.
    ctx.terminal = SshTerminal.attach_to(ext_page.get("#terminal"))
    ext_page.connection_status.text = "Ready. Enter the address of an SSH server you administer."
    ext_page.connect.disabled = False


@handler  # main.html: pyodex-submit.prevent="connect"
def connect():
    target = SshTarget(ext_page.host.value, ext_page.user.value, ext_page.port.as_int())
    set_busy(True)
    ext_page.connection_status.text = f"Session active · {target}"
    try:
        code = ctx.terminal.connect(target)
        ext_page.connection_status.text = f"Disconnected · OpenSSH exited with status {code}"
    except Exception as exc:  # noqa: BLE001 - the message belongs on the status line
        ext_page.connection_status.text = f"Disconnected · {exc}"
    finally:
        set_busy(False)
        ext_page.connect.focus()


@handler  # main.html: pyodex-click="disconnect"
def disconnect():
    ctx.terminal.disconnect()


def set_busy(busy):
    for widget in (ext_page.host, ext_page.user, ext_page.port, ext_page.connect):
        widget.disabled = busy
    ext_page.disconnect.disabled = not busy
