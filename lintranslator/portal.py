"""xdg-desktop-portal clients: Screenshot (per-poll) and ScreenCast (PipeWire)."""
from __future__ import annotations

import os
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import paths

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQ_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


class PortalError(RuntimeError):
    """A portal method failed, was denied, or timed out."""


def is_signature(text: str) -> bool:
    """Whether `text` is exactly one complete D-Bus type, e.g. `s` or `a(ua{sv})`."""
    if not text:
        return False
    from jeepney.low_level import parse_signature

    # parse_signature consumes the list it is handed, and a variant holds exactly
    # one type - so anything left over means this was a value, not a signature.
    remaining = list(text)
    try:
        parse_signature(remaining)
    except Exception:  # noqa: BLE001 - any parse failure means "not a signature"
        return False
    return not remaining


def unwrap_variant(value):
    """jeepney represents `v` as (signature, value); peel all layers off.

    The signature is parsed rather than assumed to be one character. A container
    type is a longer signature, so the old one-character test left
    `('a(ua{sv})', [...])` wrapped and the ScreenCast `streams` result arrived as
    the literal string "a(ua{sv})" - which then failed to parse as a node id.
    """
    while (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and is_signature(value[0])
    ):
        value = value[1]
    return value


def unwrap_dict(raw: dict | None) -> dict:
    return {k: unwrap_variant(v) for k, v in (raw or {}).items()}


# Ceiling in bytes on what the app holds in memory for one screenshot
MAX_SCREENSHOT_BYTES = 64 * 1024 * 1024


def pictures_dir() -> Path:
    """Where KDE and GNOME write a screenshot, which is not always ~/Pictures.

    `$XDG_PICTURES_DIR` is normally unset - the value lives in user-dirs.dirs,
    which is not exported - and a localized system does not call it "Pictures".
    Getting this wrong is silent: the path falls outside the delete allow-list
    and every grab is left behind.
    """
    value = os.environ.get("XDG_PICTURES_DIR")
    if value:
        return Path(value)
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    try:
        text = (root / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_PICTURES_DIR="):
            continue
        raw = line.split("=", 1)[1].strip().strip('"')
        raw = raw.replace("$HOME", str(Path.home()))
        if raw:
            return Path(raw)
    return Path.home() / "Pictures"


def screenshot_dirs() -> tuple[Path, ...]:
    """Directories a screenshot portal may legitimately have written into.

    The delete allow-list: the path arrives over D-Bus, so a peer must not be
    able to aim a delete outside these. They cover where the known backends
    write - Pictures (KDE, GNOME), `/tmp` (wlroots), `$XDG_RUNTIME_DIR`
    (Hyprland) and the cache.
    """
    candidates = [pictures_dir()]
    for var in ("XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    candidates.append(paths.CACHE_DIR)
    candidates.append(Path(tempfile.gettempdir()))
    return tuple(candidates)


def screenshot_path(uri: str) -> Path:
    """The local path behind a portal `uri`, or a refusal."""
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "file":
        raise PortalError(
            f"the portal returned {uri!r}, which is not a local file:// URI; "
            "only local screenshots are supported"
        )
    if parsed.netloc not in ("", "localhost"):
        raise PortalError(f"refusing a screenshot from another host ({uri!r})")
    return Path(urllib.request.url2pathname(parsed.path))


def read_screenshot(path: Path, limit: int = MAX_SCREENSHOT_BYTES) -> bytes:
    """Read a portal screenshot, bounded, with the failures named."""
    try:
        if not path.is_file():
            raise PortalError(f"the portal named {path}, which is not a file")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise PortalError(f"cannot read portal screenshot {path}: {exc}") from exc
    if len(data) > limit:
        raise PortalError(
            f"the portal's screenshot at {path} is larger than "
            f"{limit // (1024 * 1024)} MB; refusing to read it"
        )
    return data


def remove_screenshot(path: Path) -> bool:
    """Delete a portal screenshot, but only where a portal may have put one."""
    try:
        # Test the resolved path, so a symlink out of these dirs is never
        # followed into a delete
        resolved = path.resolve()
    except OSError:
        return False
    for directory in screenshot_dirs():
        try:
            resolved.relative_to(directory.resolve())
        except (OSError, ValueError):
            continue
        try:
            resolved.unlink()
        except OSError:
            return False
        return True
    return False


class PortalBus:
    """Blocking D-Bus connection to the portal, with request/response plumbing."""

    def __init__(self) -> None:
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        try:
            # enable_fds: ScreenCast hands the PipeWire remote over as a unix fd
            self.conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        except KeyError as exc:
            # jeepney reads DBUS_SESSION_BUS_ADDRESS and lets the KeyError out
            raise PortalError(
                "no session D-Bus (DBUS_SESSION_BUS_ADDRESS is not set), so "
                "xdg-desktop-portal cannot be reached; screen capture needs a "
                "running desktop session"
            ) from exc
        except OSError as exc:
            raise PortalError(f"could not connect to the session D-Bus: {exc}") from exc
        self.unique_name = self.conn.unique_name
        self._addr = DBusAddress

    def call(self, iface: str, method: str, signature: str, body: tuple, path: str = PORTAL_PATH):
        from jeepney import new_method_call

        addr = self._addr(path, bus_name=PORTAL_BUS, interface=iface)
        return self.conn.send_and_get_reply(
            new_method_call(addr, method, signature, body), timeout=60
        )

    def get_property(self, iface: str, prop: str):
        reply = self.call("org.freedesktop.DBus.Properties", "Get", "ss", (iface, prop))
        return unwrap_variant(reply.body[0])

    def request_path(self, token: str) -> str:
        """Predict the Request object path the portal will use for `token`."""
        sender = self.unique_name.lstrip(":").replace(".", "_")
        return f"{PORTAL_PATH}/request/{sender}/{token}"

    def portal_request(
        self,
        iface: str,
        method: str,
        signature: str,
        body: tuple,
        token: str,
        timeout: float = 120.0,
    ) -> tuple[int, dict, str]:
        """Invoke a portal method returning a Request handle, then await Response."""
        from jeepney import MatchRule

        req_path = self.request_path(token)
        rule = MatchRule(type="signal", interface=REQ_IFACE, member="Response", path=req_path)
        # Install the filter before the call, or the reply can be missed
        with self.conn.filter(rule) as queue:
            reply = self.call(iface, method, signature, body)
            returned = unwrap_variant(reply.body[0])
            if isinstance(returned, str) and returned.startswith("/"):
                req_path = returned

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PortalError(f"timed out after {timeout:.0f}s waiting on {req_path}")
                try:
                    msg = self.conn.recv_until_filtered(queue, timeout=remaining)
                except TimeoutError as exc:
                    # jeepney raises a bare TimeoutError carrying no message, which
                    # would otherwise escape as an unexplained empty failure - the
                    # usual cause is a consent dialog nobody answered.
                    raise PortalError(
                        f"timed out after {timeout:.0f}s waiting on {req_path}; "
                        "the desktop never answered, which usually means a "
                        "permission dialog was left unanswered"
                    ) from exc
                code = int(msg.body[0])
                results = unwrap_dict(msg.body[1] if len(msg.body) > 1 else {})
                # code 0 = success, 1 = the user cancelled, 2 = another error
                return code, results, req_path

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "PortalBus":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@dataclass
class Screenshot:
    path: Path
    size: tuple[int, int]
    elapsed: float


class ScreenshotPortal:
    """Grabs the full screen through org.freedesktop.portal.Screenshot.

    The portal picks where the PNG lands, not the app, so `screenshot_dirs()` is
    the allow-list of places this is willing to delete from.
    """

    def __init__(
        self,
        bus: PortalBus | None = None,
        on_note: Callable[[str], None] | None = None,
    ) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._counter = 0
        #: Portal screenshots this app failed to delete. Non-zero means the user
        #: is accumulating full-screen PNGs they never asked for.
        self.leaks = 0
        #: Called with a short message when the first leak happens.
        self.on_note = on_note

    @property
    def bus(self) -> PortalBus:
        if self._bus is None:
            self._bus = PortalBus()
        return self._bus

    def _discard(self, path: Path) -> None:
        """Delete the portal's file, and never let a failure go unmentioned.

        An unlink fails for reasons that have nothing to do with the app - a
        read-only or full filesystem, a sandbox, a stale mount - and swallowing
        that is how one PNG per poll accumulates unnoticed.
        """
        if remove_screenshot(path):
            return
        # Not removed. Either it was already gone - nothing to report - or it is
        # still there and the user now owns a picture of their screen.
        try:
            left_behind = path.exists()
        except OSError:
            left_behind = False
        if not left_behind:
            return
        self.leaks += 1
        # Say it once: a failure here repeats at the poll rate, and a warning per
        # grab would be its own kind of unusable. `leaks` keeps counting.
        if self.leaks == 1:
            self._note(
                f"could not delete the screenshot the portal left at {path}; "
                "it is still on disk, and later ones may be too"
            )

    def _note(self, message: str) -> None:
        if self.on_note is None:
            return
        try:
            self.on_note(message)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def grab(self, timeout: float = 30.0) -> tuple[bytes, tuple[int, int], float]:
        """Capture the whole screen. Returns (png_bytes, (w, h), elapsed_seconds)."""
        self._counter += 1
        token = f"lintranslator_ss_{os.getpid()}_{self._counter}"
        t0 = time.monotonic()
        code, results, _ = self.bus.portal_request(
            "org.freedesktop.portal.Screenshot",
            "Screenshot",
            "sa{sv}",
            ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
            token=token,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        if code != 0:
            raise PortalError(
                "screenshot denied or cancelled by the compositor "
                f"(response code {code}); approve the screen-sharing prompt and retry"
            )
        uri = results.get("uri")
        if not uri:
            raise PortalError("portal returned success but no uri")
        path = screenshot_path(str(uri))

        # From here on the portal has put a file on disk, so every exit from this
        # block owes it a delete - including the oversize and undecodable cases,
        # which is what the `finally` is for.
        try:
            data = read_screenshot(path)

            from PIL import Image
            from io import BytesIO

            # Decode before deleting: a peer that answers with a non-image gets no
            # delete out of us for naming a path
            try:
                with Image.open(BytesIO(data)) as im:
                    size = im.size
            except Exception as exc:  # PIL raises a zoo of exception types here
                raise PortalError(
                    f"the portal's screenshot at {path} is not a readable image: {exc}"
                ) from exc

            return data, size, elapsed
        finally:
            self._discard(path)

    def close(self) -> None:
        if self._owns_bus and self._bus is not None:
            self._bus.close()
            self._bus = None

    def __enter__(self) -> "ScreenshotPortal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@dataclass
class CastSession:
    session_path: str
    node_id: int
    size: tuple[int, int]
    position: tuple[int, int]
    restore_token: str | None = None

    def close(self, bus: PortalBus | None = None) -> None:
        """Ask the portal to tear the session down."""
        owns = bus is None
        b = bus or PortalBus()
        try:
            b.call(SESSION_IFACE, "Close", "", (), path=self.session_path)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if owns:
                b.close()


def open_pipewire_remote(bus: PortalBus, session_path: str) -> int:
    """The PipeWire remote descriptor for a ScreenCast session.

    The caller owns the returned descriptor and must close it. This is the whole
    point of ScreenCast: frames arrive over this fd, so nothing is written to
    disk the way the Screenshot portal has to.
    """
    reply = bus.call(
        "org.freedesktop.portal.ScreenCast",
        "OpenPipeWireRemote",
        "oa{sv}",
        (session_path, {}),
    )
    fd = reply.body[0]
    if hasattr(fd, "to_raw_fd"):
        return fd.to_raw_fd()
    raise PortalError("the portal returned no PipeWire file descriptor")


def create_screencast(
    bus: PortalBus,
    *,
    types: int = 3,
    restore_token: str | None = None,
    multiple: bool = False,
    cursor_mode: int = 1,
    timeout: float = 180.0,
) -> CastSession:
    """Run CreateSession -> SelectSources -> Start and return the stream details."""
    token = f"lintranslator_sc_{os.getpid()}"

    # types is a bitmask: 1 = monitor, 2 = window
    code, results, _ = bus.portal_request(
        "org.freedesktop.portal.ScreenCast",
        "CreateSession",
        "a{sv}",
        # a{sv} is one argument, so the body is a 1-tuple around the vardict
        (
            {
                "handle_token": ("s", f"{token}_create"),
                "session_handle_token": ("s", token),
            },
        ),
        token=f"{token}_create",
        timeout=timeout,
    )
    if code != 0:
        raise PortalError(f"CreateSession failed (code {code})")
    session_path = results.get("session_handle")
    if not session_path:
        raise PortalError("CreateSession returned no session_handle")

    options: dict = {
        "handle_token": ("s", f"{token}_sel"),
        "types": ("u", types),
        "multiple": ("b", multiple),
        "cursor_mode": ("u", cursor_mode),
    }
    if restore_token:
        options["restore_token"] = ("s", restore_token)

    code, _, _ = bus.portal_request(
        "org.freedesktop.portal.ScreenCast",
        "SelectSources",
        "oa{sv}",
        (session_path, options),
        token=f"{token}_sel",
        timeout=timeout,
    )
    if code != 0:
        raise PortalError(f"SelectSources cancelled or denied (code {code})")

    code, results, _ = bus.portal_request(
        "org.freedesktop.portal.ScreenCast",
        "Start",
        "osa{sv}",
        (session_path, "", {"handle_token": ("s", f"{token}_start")}),
        token=f"{token}_start",
        timeout=timeout,
    )
    if code != 0:
        raise PortalError(f"Start cancelled or denied (code {code})")

    streams = results.get("streams") or []
    if not streams:
        raise PortalError("Start returned no streams")

    first = streams[0]
    node_id = int(first[0]) if isinstance(first, (list, tuple)) else int(first)
    props = unwrap_dict(first[1]) if isinstance(first, (list, tuple)) and len(first) > 1 else {}
    size = props.get("size")
    position = props.get("position")
    return CastSession(
        session_path=session_path,
        node_id=node_id,
        size=tuple(size) if isinstance(size, (list, tuple)) else (0, 0),
        position=tuple(position) if isinstance(position, (list, tuple)) else (0, 0),
        restore_token=results.get("restore_token"),
    )
