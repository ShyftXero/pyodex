"""Browser adapter for Demo 17's vendored Secure Shell client.

main.py owns the connection form. This plugin mounts the hterm display, starts
OpenSSH in a Wasm worker, and answers its prompts from the page. OpenSSH handles
the SSH protocol, encryption, and host-key checks; Secure Shell's socket code
opens the connection through Chrome Direct Sockets. This file does not implement SSH.

Start with SshTerminal.mount(), connect(), and disconnect(). The private helpers
below handle terminal setup, browser prompts, and socket teardown.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass

from pyodex import (
    asset_url,
    blocking,
    create_proxy,
    document,
    import_js_module,
    js_callback,
    jsobj,
    on,
    window,
    worker_script_url,
)


class SshTerminal:
    """Mount a terminal once, then run one interactive SSH session at a time."""

    def __init__(self, term, create_fs, background, receiver):
        self._term = term
        self._create_fs = create_fs
        self._background = background
        self._receiver = receiver
        self._process = None
        self._running = False
        self._cancelled = False
        self._ignore_input = js_callback(lambda *_: None)
        self._prompt = _Prompt(self.disconnect, term.focus)

    @classmethod
    @blocking  # App code calls this like an ordinary Python method.
    async def mount(cls, selector):
        """Load the bundled libraries and put a terminal in the given element."""
        if (window.location.protocol != "isolated-app:" or not window.crossOriginIsolated
                or not getattr(window, "TCPSocket", None)):
            raise RuntimeError("Install this as an IWA with Direct Sockets, not a regular tab or extension.")
        container = document.querySelector(selector)
        if not container:
            raise ValueError(f"Terminal container not found: {selector}")

        hterm, lib, fs, process, wasi = await asyncio.gather(*(import_js_module(path) for path in (
            "vendor/hterm/index.js",
            "vendor/libdot/index.js",
            "vendor/js/deps_indexeddb-fs.rollup.js",
            "vendor/wassh/js/process.js",
            "vendor/wassh/js/syscall_handler.js",
        )))
        term = await _mount_hterm(container, hterm.hterm, lib.lib)
        return cls(term, fs.createFs, process.Background, wasi.RemoteReceiverWasiPreview1)

    @blocking
    async def connect(self, target: SshTarget) -> int:
        """Run OpenSSH and return its exit code. The browser keeps handling input."""
        if self._running:
            raise RuntimeError("An SSH session is already running.")
        self._running, self._cancelled = True, False
        sockets = None

        async def secure_input(message, max_length, echo):
            if self._cancelled:
                return ""
            return await self._prompt.ask(str(target), message, max_length, echo)

        secure_callback = create_proxy(secure_input)
        try:
            self._term.wipeContents()
            receiver = await self._open_receiver(secure_callback)
            sockets = _SocketCleanup(receiver)
            if self._cancelled:
                raise RuntimeError("Cancelled by user.")

            self._process = _make_ssh_process(self._background, target, receiver)
            self._term.focus()
            result = await self._process.run()
            if self._cancelled:
                raise RuntimeError("Cancelled by user.")
            return _exit_code(result)
        finally:
            await self._finish_session(sockets, secure_callback)

    def disconnect(self):
        """Cancel startup or stop the worker for an active session."""
        if self._running:
            self._cancelled = True
            self._prompt.finish()
            if self._process is not None:
                self._process.terminate()

    async def _open_receiver(self, secure_callback):
        # Secure Shell supplies the WASI file/socket calls and its own known_hosts storage.
        filesystem = self._create_fs(jsobj({"objectStoreName": "pyodex-ssh"}))
        receiver = self._receiver.new(jsobj({
            "fileSystem": filesystem,
            "term": self._term,
            "secureInput": secure_callback,
        }))
        await receiver.init()
        return receiver

    async def _finish_session(self, sockets, secure_callback):
        self._prompt.finish()
        try:
            if sockets is not None:
                await sockets.close()
        finally:
            secure_callback.destroy()
            io = self._term.io
            io.onVTKeystroke = io.sendString = io.onTerminalResize = self._ignore_input
            self._process, self._running = None, False


@dataclass(frozen=True)
class SshTarget:
    """Validate a destination before passing it to OpenSSH as arguments."""

    host: str
    user: str
    port: int = 22

    def __post_init__(self):
        host = self.host.strip()
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?", host):
                raise ValueError("Enter a hostname or an IP address, without a scheme or port.") from None
            host = host.lower()
        user = self.user.strip()
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user):
            raise ValueError("Use a standard SSH account name (letters, digits, _, . or -).")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Port must be an integer between 1 and 65535.")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "user", user)

    def __str__(self):
        address = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.user}@{address}:{self.port}"


async def _mount_hterm(container, hterm, lib):
    term = hterm.Terminal.new(jsobj({"storage": lib.Storage.Memory.new()}))
    ready = asyncio.get_running_loop().create_future()
    callback = create_proxy(lambda *_: ready.set_result(None) if not ready.done() else None)
    original = term.onTerminalReady
    term.onTerminalReady = callback
    try:
        term.decorate(container)
        await ready  # hterm reports when its iframe is usable.
    finally:
        term.onTerminalReady = original
        callback.destroy()
    term.getPrefs().set("enable-clipboard-write", False)
    term.getPrefs().set("allow-images-inline", False)
    term.installKeyboard()
    return term


def _make_ssh_process(background, target, receiver):
    worker = worker_script_url("vendor/wassh/js/worker.js")
    return background.new(worker, jsobj({
        "executable": asset_url("vendor/plugin/wasm/ssh.wasm"),
        # Use a PTY and the demo's known_hosts; don't load an SSH config or forward ports.
        "argv": [
            "ssh", "-F", "/dev/null", "-tt", "-p", str(target.port), "-l", target.user,
            "-o", "StrictHostKeyChecking=ask", "-o", "UpdateHostKeys=no",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=15", "--", target.host,
        ],
        "environ": {"HOME": "/", "TERM": "xterm-256color", "USER": target.user, "LANG": "C.UTF-8"},
        "handler": receiver,
        "sabSize": 257 * 1024,  # Secure Shell's worker buffer fits a 256 KiB WASI read plus overhead.
    }))


def _exit_code(result):
    code = result if isinstance(result, int) else getattr(result, "status", None)
    if isinstance(code, int):
        return code
    raise RuntimeError(getattr(result, "message", None) or "OpenSSH stopped unexpectedly.")


class _Prompt:
    """Wait for the user to answer an OpenSSH prompt outside terminal output."""

    def __init__(self, disconnect, focus_terminal):
        self.dialog = document.element("pyodex-ssh-prompt")
        self.title = document.element("pyodex-ssh-prompt-title")
        self.target = document.element("pyodex-ssh-prompt-target")
        self.message = document.element("pyodex-ssh-prompt-message")
        self.answer = document.element("pyodex-ssh-prompt-answer")
        self.pending = None

        @on(document.element("pyodex-ssh-prompt-form"), "submit", prevent_default=True)
        def accept(_event):
            self.finish(str(self.answer.value))
            focus_terminal()

        @on(document.element("pyodex-ssh-prompt-cancel"), "click")
        @on(self.dialog, "cancel", prevent_default=True)
        def cancel(_event):
            disconnect()

    async def ask(self, target, message, max_length, echo):
        self.pending = asyncio.get_running_loop().create_future()
        self.title.textContent = "SSH confirmation" if echo else "SSH authentication"
        self.target.textContent = target
        self.message.textContent = message
        self.answer.type = "text" if echo else "password"
        self.answer.maxLength = max(1, max_length - 1)
        self.answer.value = ""
        self.dialog.showModal()
        self.answer.focus()
        return await self.pending

    def finish(self, value=""):
        self.answer.value = ""  # Don't leave a password in the form after it closes.
        if self.dialog.open:
            self.dialog.close()
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(value)
        self.pending = None


class _SocketCleanup:
    """Secure Shell 0.82 can close a socket twice; share one close promise."""

    def __init__(self, receiver):
        self.receiver = receiver
        self.sockets = []
        original = receiver.handle_sock_create

        async def create_socket(*args):
            result = await original(*args)
            if isinstance(result, int):
                return result
            socket = receiver.vfs.getFileHandle(result.socket)
            close = socket.close
            completion = None

            def close_once():
                nonlocal completion
                if completion is None:
                    completion = close()
                return completion

            callback = create_proxy(close_once)
            socket.close = callback
            self.sockets.append((socket, close, callback))
            return result

        self.callback = create_proxy(create_socket)
        receiver.handle_sock_create = self.callback

    async def close(self):
        fds = self.receiver.vfs.fds_
        # Get Map.keys from JavaScript, not JsMap's Python Mapping.keys view.
        keys = window.Reflect.get(fds, "keys").call(fds)
        for fd in getattr(window.Array, "from")(keys).to_py():
            self.receiver.vfs.close(fd)
        for socket, original, callback in self.sockets:
            try:
                await socket.close()
            finally:
                socket.close = original
                callback.destroy()
        self.callback.destroy()
