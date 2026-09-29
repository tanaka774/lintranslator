"""The portal writes a screenshot the app did not choose the location of.

Every grab therefore leaves a full-screen PNG on disk until the app deletes it,
and a delete that fails silently is how `~/Pictures` fills up with one file per
poll. These tests pin the allow-list, the leak reporting, and the guarantee that
a grab which fails *after* the portal wrote the file still cleans it up.
"""
from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lintranslator import portal


def _png_bytes(size=(8, 4), colour=(10, 20, 30)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeBus:
    """Stands in for PortalBus: answers one Screenshot request with `uri`."""

    def __init__(self, uri: str | None, code: int = 0) -> None:
        self.uri = uri
        self.code = code
        self.calls: list[str] = []

    def portal_request(self, iface, method, signature, body, token, timeout=120.0):
        self.calls.append(method)
        results = {"uri": self.uri} if self.uri else {}
        return self.code, results, f"/org/freedesktop/portal/desktop/request/{token}"

    def close(self) -> None:
        pass


def _portal(uri, on_note=None, code: int = 0) -> portal.ScreenshotPortal:
    return portal.ScreenshotPortal(bus=FakeBus(uri, code=code), on_note=on_note)


@pytest.fixture
def allowlist(tmp_path, monkeypatch):
    """Make `tmp_path/allowed` the only directory the app will delete from."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(portal, "screenshot_dirs", lambda: (allowed,))
    return allowed


# --------------------------------------------------------------------------- #
# The allow-list
# --------------------------------------------------------------------------- #
def test_a_screenshot_inside_the_allowlist_is_deleted(allowlist):
    shot = allowlist / "Screenshot_20260101_000000.png"
    shot.write_bytes(b"png")
    assert portal.remove_screenshot(shot) is True
    assert not shot.exists()


def test_a_file_outside_the_allowlist_is_never_deleted(allowlist, tmp_path):
    """A peer that owns the portal bus must not be able to name ~/.ssh/id_rsa."""
    victim = tmp_path / "id_rsa"
    victim.write_bytes(b"secret")
    assert portal.remove_screenshot(victim) is False
    assert victim.exists()


def test_a_symlink_out_of_the_allowlist_is_not_followed(allowlist, tmp_path):
    victim = tmp_path / "id_rsa"
    victim.write_bytes(b"secret")
    link = allowlist / "shot.png"
    link.symlink_to(victim)
    # The link resolves outside the allow-list, so nothing is unlinked - least of
    # all the file it points at.
    assert portal.remove_screenshot(link) is False
    assert victim.exists()
    assert link.is_symlink()


def test_a_traversal_path_does_not_escape_the_allowlist(allowlist, tmp_path):
    victim = tmp_path / "id_rsa"
    victim.write_bytes(b"secret")
    assert portal.remove_screenshot(allowlist / ".." / "id_rsa") is False
    assert victim.exists()


def test_a_missing_file_is_reported_as_not_removed(allowlist):
    assert portal.remove_screenshot(allowlist / "never-existed.png") is False


# --------------------------------------------------------------------------- #
# Where the backends actually write
# --------------------------------------------------------------------------- #
def test_the_pictures_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_PICTURES_DIR", str(tmp_path / "Bilder"))
    assert portal.pictures_dir() == tmp_path / "Bilder"


def test_user_dirs_dirs_is_read_when_the_env_var_is_unset(monkeypatch, tmp_path):
    """The env var is normally unset, so the file is the only source of truth."""
    monkeypatch.delenv("XDG_PICTURES_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "user-dirs.dirs").write_text(
        '# comment\nXDG_PICTURES_DIR="$HOME/画像"\n', encoding="utf-8"
    )
    assert portal.pictures_dir() == Path.home() / "画像"


def test_a_localized_pictures_dir_still_falls_back_to_home_pictures(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("XDG_PICTURES_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert portal.pictures_dir() == Path.home() / "Pictures"


def test_the_allowlist_covers_every_known_backend_location(monkeypatch, tmp_path):
    """KDE/GNOME -> Pictures, wlroots -> /tmp, Hyprland -> XDG_RUNTIME_DIR."""
    monkeypatch.delenv("XDG_PICTURES_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    dirs = portal.screenshot_dirs()
    assert Path.home() / "Pictures" in dirs
    assert tmp_path / "run" in dirs
    assert tmp_path / "cache" in dirs
    assert Path(tempfile.gettempdir()) in dirs


# --------------------------------------------------------------------------- #
# The untrusted URI
# --------------------------------------------------------------------------- #
def test_a_non_file_uri_is_refused():
    with pytest.raises(portal.PortalError):
        portal.screenshot_path("https://example.invalid/shot.png")


def test_a_remote_host_uri_is_refused():
    with pytest.raises(portal.PortalError):
        portal.screenshot_path("file://elsewhere.invalid/shot.png")


# --------------------------------------------------------------------------- #
# grab(): the portal has written a file by the time anything can fail
# --------------------------------------------------------------------------- #
def test_a_successful_grab_deletes_the_portal_file(allowlist):
    shot = allowlist / "Screenshot_ok.png"
    shot.write_bytes(_png_bytes())
    p = _portal(shot.as_uri())
    png, size, _ = p.grab()
    assert size == (8, 4)
    assert not shot.exists()
    assert p.leaks == 0


def test_an_undecodable_image_is_still_deleted(allowlist):
    """The delete must not sit after the decode, or a bad image leaks the file."""
    shot = allowlist / "Screenshot_bad.png"
    shot.write_bytes(b"this is not a PNG")
    p = _portal(shot.as_uri())
    with pytest.raises(portal.PortalError):
        p.grab()
    assert not shot.exists()


def test_an_oversize_image_is_still_deleted(allowlist, monkeypatch):
    shot = allowlist / "Screenshot_huge.png"
    shot.write_bytes(b"x" * 4096)
    real = portal.read_screenshot
    monkeypatch.setattr(portal, "read_screenshot", lambda path, limit=16: real(path, limit))
    p = _portal(shot.as_uri())
    with pytest.raises(portal.PortalError):
        p.grab()
    assert not shot.exists()


def test_a_denied_grab_leaves_nothing_and_reports_nothing(allowlist):
    p = _portal(None, code=1)
    with pytest.raises(portal.PortalError):
        p.grab()
    assert p.leaks == 0


def test_a_failed_delete_is_counted_and_said_once(tmp_path, monkeypatch):
    """At the poll rate a warning per grab would be its own kind of unusable."""
    notes: list[str] = []
    # Nothing is deletable, so every grab leaves its file behind.
    monkeypatch.setattr(portal, "screenshot_dirs", lambda: ())
    shot = tmp_path / "Screenshot_stuck.png"
    shot.write_bytes(_png_bytes())
    p = _portal(shot.as_uri(), on_note=notes.append)

    for _ in range(3):
        p.grab()

    assert shot.exists()
    assert p.leaks == 3
    assert len(notes) == 1
    assert "still on disk" in notes[0]


def test_a_grab_that_left_nothing_behind_is_not_a_leak(allowlist):
    """The portal deleting its own file first is not the app leaking."""
    shot = allowlist / "Screenshot_gone.png"
    shot.write_bytes(_png_bytes())
    p = _portal(shot.as_uri())
    real = portal.read_screenshot

    def read_and_remove(path, limit=portal.MAX_SCREENSHOT_BYTES):
        data = real(path, limit)
        path.unlink()
        return data

    portal.read_screenshot, saved = read_and_remove, portal.read_screenshot
    try:
        p.grab()
    finally:
        portal.read_screenshot = saved
    assert p.leaks == 0


def test_the_leak_count_reaches_the_grabber_stats(allowlist, monkeypatch):
    from lintranslator.capture import ScreenGrabber
    from lintranslator.config import Region

    shot = allowlist / "Screenshot_stats.png"
    shot.write_bytes(_png_bytes())
    monkeypatch.setattr(portal, "screenshot_dirs", lambda: ())
    grabber = ScreenGrabber(Region(0, 0, 8, 4, "pixels"), portal=_portal(shot.as_uri()))
    grabber.grab()
    assert grabber.stats["leaks"] == 1
