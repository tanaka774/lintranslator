"""The one-line control channel behind `tlkun reread`.

Wayland forbids reading global keys, so a shortcut outside the app runs a command
and that command talks to the running window over this socket. What matters here:
a round trip works, an absent socket reports something a user can act on, and a
second GUI never steals the socket from a live one.
"""
from __future__ import annotations

import threading
import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except (ImportError, ValueError):  # pragma: no cover - no GLib typelib
    pytest.skip("GLib unavailable", allow_module_level=True)

from tlkun.control import ControlServer, send, socket_path  # noqa: E402


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path, monkeypatch):
    """Keep the socket out of the real session (and out of other tests)."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    yield


def _pump_until(predicate, timeout: float = 5.0):
    """Run the main context until `predicate` is true, as the GUI would."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def test_the_socket_lives_in_the_runtime_dir(tmp_path):
    assert socket_path().parent == tmp_path


def test_a_round_trip_answers_the_caller():
    server = ControlServer(lambda command: f"got {command}")
    server.start()
    assert server.listening, server.error

    replies: list[str] = []

    def client():
        replies.append(send("reread"))
        return False

    threading.Thread(target=client, daemon=True).start()
    _pump_until(lambda: bool(replies))

    assert replies == ["got reread"]
    server.stop()


def test_stopping_removes_the_socket():
    server = ControlServer(lambda command: "ok")
    server.start()
    assert socket_path().exists()
    server.stop()
    assert not socket_path().exists()


def test_no_gui_reports_something_actionable():
    with pytest.raises(ConnectionError) as caught:
        send("reread")
    message = str(caught.value)
    assert "no running tl-kun GUI" in message
    assert "tlkun gui" in message


def test_a_second_gui_does_not_steal_the_socket():
    """Two GUIs must not fight over the path: the live one keeps it."""
    first = ControlServer(lambda command: "first")
    first.start()
    assert first.listening

    second = ControlServer(lambda command: "second")
    second.start()

    assert second.listening is False
    assert second.error and "another tl-kun GUI" in second.error
    # And the first one still answers.
    replies: list[str] = []
    threading.Thread(target=lambda: replies.append(send("hi")), daemon=True).start()
    _pump_until(lambda: bool(replies))
    assert replies == ["first"]
    first.stop()


def test_unknown_commands_are_answered_not_ignored():
    seen: list[str] = []

    def handler(command: str) -> str:
        seen.append(command)
        return f"unknown command {command!r}"

    server = ControlServer(handler)
    server.start()
    replies: list[str] = []
    threading.Thread(target=lambda: replies.append(send("nonsense")), daemon=True).start()
    _pump_until(lambda: bool(replies))

    assert replies == ["unknown command 'nonsense'"], "a caller must never get silence"
    assert seen == ["nonsense"]
    server.stop()


def test_a_crashing_handler_still_answers():
    def handler(_command: str) -> str:
        raise RuntimeError("boom")

    server = ControlServer(handler)
    server.start()
    replies: list[str] = []
    threading.Thread(target=lambda: replies.append(send("reread")), daemon=True).start()
    _pump_until(lambda: bool(replies))

    assert replies and "boom" in replies[0]
    server.stop()


# -- who else can reach it -------------------------------------------------- #
def test_the_socket_is_never_placed_in_a_world_writable_directory():
    """The old fallback was a flat `/tmp/tl-kun-<uid>.sock`, which another user
    can create first - and whoever holds the path receives the commands. A
    candidate inside a private subdirectory is fine; a direct child of the
    shared temp root is not."""
    import tempfile
    from pathlib import Path

    from tlkun.control import candidate_paths

    temp_root = Path(tempfile.gettempdir()).resolve()
    for path in candidate_paths():
        assert path.parent.resolve() != temp_root, path


def test_the_socket_file_is_mode_0600():
    server = ControlServer(lambda command: "ok")
    server.start()
    assert server.listening, server.error
    try:
        mode = socket_path().stat().st_mode & 0o777
        assert mode == 0o600, oct(mode)
    finally:
        server.stop()


def test_the_fallback_directory_is_private(tmp_path, monkeypatch):
    """Without XDG_RUNTIME_DIR the socket falls back under the user's cache."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = socket_path()
    assert tmp_path in path.parents
    assert path.parent.stat().st_mode & 0o777 == 0o700
