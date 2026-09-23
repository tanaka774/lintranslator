"""xdg-desktop-portal clients: Screenshot (per-poll) and ScreenCast (PipeWire).

Why the portal: on Wayland a client cannot read the framebuffer directly. The
only supported path is xdg-desktop-portal, which KWin backs with
`zkde_screencast_unstable_v1`. This module speaks D-Bus to it via jeepney
(pure Python, no GLib dependency).

Screenshot flow (used by default):
    Screenshot("", {interactive: false}) -> Response(code, {uri}) -> read PNG

ScreenCast flow (optional, for a persistent 60fps stream):
    CreateSession -> SelectSources -> Start -> {node_id, restore_token}
    The returned fd is consumed by a GStreamer pipeline in `screencast_worker`.
"""
from __future__ import annotations

import os
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQ_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


class PortalError(RuntimeError):
    """A portal method failed, was denied, or timed out."""


# --------------------------------------------------------------------------- #
# Variant helpers
# --------------------------------------------------------------------------- #
def unwrap_variant(value):
    """jeepney represents `v` as (signature, value); peel all layers off."""
    while (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], str)
        and len(value[0]) == 1
    ):
        value = value[1]
    return value


def unwrap_dict(raw: dict | None) -> dict:
    return {k: unwrap_variant(v) for k, v in (raw or {}).items()}


# --------------------------------------------------------------------------- #
# The file behind a Screenshot response
# --------------------------------------------------------------------------- #
# A PNG of any screen is a few MB; this is a ceiling on what the app is willing
# to hold in memory for one, not a realistic size.
MAX_SCREENSHOT_BYTES = 64 * 1024 * 1024


def screenshot_dirs() -> tuple[Path, ...]:
    """Directories a screenshot portal may legitimately have written into.

    These are what the app is willing to *delete* from. The portal names a file
    and the app removes it afterwards, which is what stops `~/Pictures` filling
    up with one PNG per poll - but the name comes over D-Bus, and a peer that
    owns `org.freedesktop.portal.Desktop` (a name that is free when no portal is
    running) could otherwise name `~/.ssh/id_rsa` and have the app delete it.
    """
    candidates = []
    pictures = os.environ.get("XDG_PICTURES_DIR")
    candidates.append(Path(pictures) if pictures else Path.home() / "Pictures")
    for var in ("XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    candidates.append(paths.CACHE_DIR)
    candidates.append(Path(tempfile.gettempdir()))
    return tuple(candidates)


def screenshot_path(uri: str) -> Path:
    """The local path behind a portal `uri`, or a refusal.

    Only `file://` is accepted: a `http://` URI here would have the app fetch a
    URL of the peer's choosing, and an empty scheme would have it resolve a
    relative path against the working directory.
    """
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
    """Delete a portal screenshot, but only where a portal may have put one.

    Returns whether anything was removed. The resolved path is what gets tested,
    so a symlink pointing out of the screenshots directory is never followed into
    a delete, and never removed either - leaving it is the safe mistake.
    """
    try:
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


# --------------------------------------------------------------------------- #
# Bus
# --------------------------------------------------------------------------- #
class PortalBus:
    """Blocking D-Bus connection to the portal, with request/response plumbing."""

    def __init__(self) -> None:
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        try:
            self.conn = open_dbus_connection(bus="SESSION")
        except KeyError as exc:
            # jeepney reads DBUS_SESSION_BUS_ADDRESS and lets the KeyError out.
            # From a TTY, a cron job or a systemd unit without a session that is
            # simply the state of the world, so it is reported as one.
            raise PortalError(
                "no session D-Bus (DBUS_SESSION_BUS_ADDRESS is not set), so "
                "xdg-desktop-portal cannot be reached; screen capture needs a "
                "running desktop session"
            ) from exc
        except OSError as exc:
            raise PortalError(f"could not connect to the session D-Bus: {exc}") from exc
        self.unique_name = self.conn.unique_name
        self._addr = DBusAddress

    # -- low level --------------------------------------------------------- #
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
        """Predict the Request object path the portal will use for `token`.

        /org/freedesktop/portal/desktop/request/<sender>/<token> where <sender>
        is our unique name with ':' stripped and '.' replaced by '_'.
        """
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
        """Invoke a portal method returning a Request handle, then await Response.

        The signal filter is installed *before* the call so the reply can never
        be missed. Returns (response_code, results, request_path); code 0 means
        success, 1 means the user cancelled, 2 means another error.
        """
        from jeepney import MatchRule

        req_path = self.request_path(token)
        rule = MatchRule(type="signal", interface=REQ_IFACE, member="Response", path=req_path)
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
                msg = self.conn.recv_until_filtered(queue, timeout=remaining)
                code = int(msg.body[0])
                results = unwrap_dict(msg.body[1] if len(msg.body) > 1 else {})
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


# --------------------------------------------------------------------------- #
# Screenshot portal
# --------------------------------------------------------------------------- #
@dataclass
class Screenshot:
    path: Path
    size: tuple[int, int]
    elapsed: float


class ScreenshotPortal:
    """Grabs the full screen through org.freedesktop.portal.Screenshot.

    The portal writes a PNG that we must read before removing. We only ever keep
    the cropped region in memory, so the temporary file is deleted immediately.
    """

    def __init__(self, bus: PortalBus | None = None) -> None:
        self._bus = bus
        self._owns_bus = bus is None
        self._counter = 0

    @property
    def bus(self) -> PortalBus:
        if self._bus is None:
            self._bus = PortalBus()
        return self._bus

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
        data = read_screenshot(path)

        from PIL import Image
        from io import BytesIO

        # Decode before deleting anything. The file is the portal's, and a peer
        # that answers with something that is not an image does not get a delete
        # out of us just for naming a path.
        try:
            with Image.open(BytesIO(data)) as im:
                size = im.size
        except Exception as exc:  # PIL raises a zoo of exception types here
            raise PortalError(
                f"the portal's screenshot at {path} is not a readable image: {exc}"
            ) from exc

        # The portal stores these in ~/Pictures; don't leave litter behind.
        remove_screenshot(path)
        return data, size, elapsed

    def close(self) -> None:
        if self._owns_bus and self._bus is not None:
            self._bus.close()
            self._bus = None

    def __enter__(self) -> "ScreenshotPortal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# ScreenCast portal
# --------------------------------------------------------------------------- #
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


def create_screencast(
    bus: PortalBus,
    *,
    types: int = 3,
    restore_token: str | None = None,
    multiple: bool = False,
    cursor_mode: int = 1,
    timeout: float = 180.0,
) -> CastSession:
    """Run CreateSession -> SelectSources -> Start and return the stream details.

    `types` is a bitmask: 1 = monitor, 2 = window. SelectSources triggers the
    compositor's picker dialog, so `timeout` must allow for human interaction.
    """
    token = f"lintranslator_sc_{os.getpid()}"

    code, results, _ = bus.portal_request(
        "org.freedesktop.portal.ScreenCast",
        "CreateSession",
        "a{sv}",
        {
            "handle_token": ("s", f"{token}_create"),
            "session_handle_token": ("s", token),
        },
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
