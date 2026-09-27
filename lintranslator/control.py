"""A one-line control channel into the running GUI."""
from __future__ import annotations

import os
import socket
from pathlib import Path

from . import paths
from typing import Callable

SOCKET_NAME = "lintranslator.sock"
# seconds
CLIENT_TIMEOUT = 5.0
MAX_REPLY_BYTES = 64 * 1024

Command = Callable[[str], str]


def candidate_paths() -> list[Path]:
    """Where the socket may live, best first."""
    socket_paths: list[Path] = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        socket_paths.append(Path(runtime) / SOCKET_NAME)
    socket_paths.append(paths.CACHE_DIR / SOCKET_NAME)
    return socket_paths


def socket_path() -> Path:
    """The first candidate that can actually be written to."""
    candidates = candidate_paths()
    for path in candidates:
        parent = path.parent
        try:
            # 0700: the directory is the access control for the socket in it,
            # since a socket file's own mode follows the umask
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            continue
        if os.access(parent, os.W_OK):
            return path
    return candidates[-1]


def send(command: str, timeout: float = CLIENT_TIMEOUT) -> str:
    """Send one command to the running GUI and return its reply."""
    path = socket_path()
    if not path.exists():
        raise ConnectionError(
            f"no running lintranslator GUI (no control socket at {path}); "
            "start one with `lintranslator gui --panel` or `lintranslator gui`"
        )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(str(path))
        except OSError as exc:
            raise ConnectionError(
                f"could not reach the lintranslator GUI at {path}: {exc}"
            ) from exc
        client.sendall(command.strip().encode() + b"\n")
        data = b""
        try:
            while not data.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
                # one line in, one line out: a reply that keeps arriving is a
                # broken or hostile peer, not something to buffer without a bound
                if len(data) > MAX_REPLY_BYTES:
                    raise ConnectionError(
                        "the lintranslator GUI sent an oversized reply; ignoring it"
                    )
        except TimeoutError as exc:
            raise ConnectionError(
                f"the lintranslator GUI did not answer within {timeout:.1f}s "
                "(it may be busy translating)"
            ) from exc
    return data.decode(errors="replace").strip()


def _someone_is_listening(path: Path, timeout: float = 0.3) -> bool:
    """Whether a live process answers on `path` (as opposed to a stale socket)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect(str(path))
        except OSError:
            return False
    return True


class ControlServer:
    """Listens for control commands and hands them to `handler`."""

    def __init__(self, handler: Command) -> None:
        self.handler = handler
        self.path = socket_path()
        self.error: str | None = None
        self._server: socket.socket | None = None
        self._watching = False

    def start(self) -> None:
        """Open the socket and answer commands lazily (one per read)."""
        # only unlink a socket this server created: removing the path
        # unconditionally would cut off a GUI that is already running on it
        self._close_socket(unlink=self._server is not None)
        self.error = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                if _someone_is_listening(self.path):
                    self.error = "another lintranslator GUI owns the control socket"
                    return
                self.path.unlink()  # stale, left by a crash
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.path))
            # bind creates the socket with the process umask, which on a
            # permissive umask would let another local user connect
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            server.listen(4)
            server.setblocking(False)
        except OSError as exc:
            self.error = f"cannot listen on {self.path}: {exc}"
            return

        self._server = server
        self._watching = True
        try:
            import gi

            gi.require_version("GLib", "2.0")
            from gi.repository import GLib

            GLib.io_add_watch(server.fileno(), GLib.IO_IN, self._on_readable)
        except Exception as exc:  # noqa: BLE001 - no GLib: the socket goes unused
            self.error = f"no GLib main loop for the control socket: {exc}"
            self._watching = False

    def _on_readable(self, _fd, _condition) -> bool:
        server = self._server
        if server is None:
            return False
        try:
            connection, _ = server.accept()
        except OSError:
            return True
        with connection:
            # short: this runs on the GUI's main loop, so a client that goes
            # quiet must not freeze the window while we wait for it
            connection.settimeout(0.5)
            try:
                command = connection.recv(1024).decode(errors="replace").strip()
                reply = self.handler(command) if command else "empty command"
            except Exception as exc:  # noqa: BLE001 - never kill the watch
                reply = f"error: {type(exc).__name__}: {exc}"
            try:
                connection.sendall(reply.encode() + b"\n")
            except OSError:
                pass
        return True

    @property
    def listening(self) -> bool:
        return self._watching

    def stop(self) -> None:
        self._close_socket(unlink=self._server is not None)

    def _close_socket(self, unlink: bool) -> None:
        """Drop our socket, and the path only if it was ours."""
        self._watching = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if unlink:
            try:
                self.path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "ControlServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
