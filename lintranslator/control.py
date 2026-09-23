"""A one-line control channel into the running GUI.

There are things you want to tell lintranslator while the game has the keyboard: re-read
the box, and (later) pause or resume. On Wayland an application cannot read global
keys, so the path is: something outside the app - a KDE global shortcut, a
keyboard macro, a Stream Deck, a shell - runs a command, and that command talks to
the running window over this socket.

    lintranslator reread          # what a KDE custom shortcut should be bound to

The socket lives in `$XDG_RUNTIME_DIR` (per-user, cleaned up on logout), or in a
per-user cache directory when the runtime dir is not writable. Both are
directories only this user can write to, and the socket itself is chmod 0600, so
it is not reachable by other accounts. An absent socket means "no GUI running",
which the CLI reports as such rather than as a mysterious failure.

Protocol: one command per connection, one line of reply. Deliberately trivial -
this is not a public API, and a protocol with framing would be more to get wrong
than to use.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

from . import paths
from typing import Callable

SOCKET_NAME = "lintranslator.sock"
# Long enough for a compositor to have started the GUI, short enough that a
# mistyped command does not appear to hang.
CLIENT_TIMEOUT = 5.0

Command = Callable[[str], str]


def candidate_paths() -> list[Path]:
    """Where the socket may live, best first.

    `$XDG_RUNTIME_DIR` is the right home - per-user, mode 0700, wiped on logout.
    It is not always writable (a sandboxed or unusual session), so a per-user
    cache directory is the one fallback. There is deliberately no `/tmp`
    candidate any more: `/tmp` is world-writable, so another user can create
    `lintranslator-<uid>.sock` first and whoever holds that path receives the commands.
    The choice has to be deterministic - the GUI and `lintranslator reread` are different
    processes and must agree without talking first - so the fallback is a
    directory only this user can write to.
    """
    socket_paths: list[Path] = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        socket_paths.append(Path(runtime) / SOCKET_NAME)
    # `lintranslator.paths` owns the XDG rules, so the socket lands in the same tree
    # as the rest of the app's state (and `LINTRANSLATOR_HOME` moves it with them).
    socket_paths.append(paths.CACHE_DIR / SOCKET_NAME)
    return socket_paths


def socket_path() -> Path:
    """The first candidate that can actually be written to."""
    candidates = candidate_paths()
    for path in candidates:
        parent = path.parent
        try:
            # 0700: the directory is the access control for the socket in it,
            # because a socket file's own mode is not enough on its own (and is
            # whatever the umask happened to be).
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            continue
        if os.access(parent, os.W_OK):
            return path
    return candidates[-1]


def send(command: str, timeout: float = CLIENT_TIMEOUT) -> str:
    """Send one command to the running GUI and return its reply.

    Raises `ConnectionError` when nothing is listening, with a message that says
    what to do about it.
    """
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
    """Listens for control commands and hands them to `handler`.

    Every command is answered, including unknown ones: a caller that gets no
    reply cannot tell a busy app from a broken one. Failure to listen is never
    fatal - a GUI without a control socket still translates, it just cannot be
    told to re-read from outside, so `error` says why.
    """

    def __init__(self, handler: Command) -> None:
        self.handler = handler
        self.path = socket_path()
        self.error: str | None = None
        self._server: socket.socket | None = None
        self._watching = False

    def start(self) -> None:
        """Open the socket and answer commands lazily (one per read)."""
        # Only ever remove a socket *this* server created: unlinking the path
        # unconditionally would cut off a GUI that is already running on it.
        self._close_socket(unlink=self._server is not None)
        self.error = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                if _someone_is_listening(self.path):
                    # Never steal the socket from a GUI that is actually running.
                    self.error = "another lintranslator GUI owns the control socket"
                    return
                self.path.unlink()  # stale, left by a crash
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.path))
            # Bind creates the socket with the process umask, which on a
            # permissive umask would let another local user connect. The
            # protocol is a single verb, but nothing else about this app should
            # be reachable by another account.
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
            # Short: this runs on the GUI's main loop, so a client that goes
            # quiet must not freeze the window while we wait for it.
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
