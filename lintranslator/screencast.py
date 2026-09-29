"""Live frames from `org.freedesktop.portal.ScreenCast`, which write nothing.

The Screenshot portal has to put a PNG somewhere and the app does not choose
where, so a loop reading the screen twice a second leaves one on disk per read
whenever the delete fails. A stream costs one consent dialog per session.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable

from PIL import Image

from .portal import (
    CastSession,
    PortalBus,
    PortalError,
    create_screencast,
    open_pipewire_remote,
)


class ScreenCastError(RuntimeError):
    """The ScreenCast session or its PipeWire stream could not be established."""


# The stream is the whole workspace; the configured region is cropped here.
# max-buffers=1 + drop=true keeps only the newest frame, so a consumer slower
# than the compositor reads the present rather than working through a backlog.
PIPELINE = (
    "pipewiresrc path={node} fd={fd} do-timestamp=true "
    "! videoconvert ! video/x-raw,format=RGB "
    "! appsink name=sink max-buffers=1 drop=true sync=false"
)

# How long a single grab will wait for a frame before calling the stream dead.
FRAME_TIMEOUT = 5.0


def _gst():
    """Import GStreamer late: the CLI and `check` must work without it."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        # GstApp must be loaded too, not just Gst: `appsink`'s pull methods
        # (try_pull_sample and friends) live in that namespace, and without it
        # the element comes back as a GstAppSink with no way to read a frame.
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gst, GstApp  # noqa: F401

        Gst.init(None)
        return Gst
    except Exception as exc:  # noqa: BLE001 - ImportError, ValueError, missing typelib
        raise ScreenCastError(
            "ScreenCast capture needs PyGObject with GStreamer and the pipewire "
            f"plugin, which this Python cannot import: {exc}"
        ) from exc


def screencast_ready() -> bool:
    """Whether this Python can run the ScreenCast backend at all.

    Checks the pieces `start()` needs - the GStreamer typelibs and the pipewire
    source element - without asking anyone for consent. This says nothing about
    whether the *desktop* will grant a stream; see `screencast_available`.
    """
    try:
        Gst = _gst()
    except ScreenCastError:
        return False
    try:
        return Gst.ElementFactory.find("pipewiresrc") is not None
    except Exception:  # noqa: BLE001 - a broken GStreamer is simply "not ready"
        return False


#: Desktops whose ScreenCast portal refuses an X11 session outright. KDE's
#: returns OtherError there, after showing its own "Screen Sharing Not
#: Available" dialog, so asking costs the user that dialog and buys nothing.
_X11_REFUSES_STREAMING = ("KDE", "PLASMA")


def screencast_available() -> tuple[bool, str]:
    """Whether a stream is worth asking for here, and why not when it is not.

    Distinct from `screencast_ready`: this is about the session, not the Python.
    """
    if not screencast_ready():
        return False, (
            "GStreamer or its pipewire plugin is missing, so frames could not be read"
        )
    if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "x11":
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if any(name in desktop for name in _X11_REFUSES_STREAMING):
            return False, (
                "this desktop's screen-sharing portal refuses X11 sessions, so a "
                "stream cannot be started in one"
            )
    return True, ""


class ScreenCastStream:
    """A live PipeWire stream, used as the frame source for the polling loop."""

    def __init__(
        self,
        restore_token: str | None = None,
        on_note: Callable[[str], None] | None = None,
        on_restore_token: Callable[[str], None] | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._bus: PortalBus | None = None
        self._session: CastSession | None = None
        self._pipeline = None
        self._sink = None
        self._fd: int | None = None
        self._gst = None
        self._started = False
        self._timeout = timeout
        #: Reusing this skips the consent dialog on later runs.
        self.restore_token = restore_token
        self.on_note = on_note
        #: Called with a fresh token, so the caller can persist it.
        self.on_restore_token = on_restore_token
        self.size: tuple[int, int] = (0, 0)
        self.frames = 0
        self.total_elapsed = 0.0

    @property
    def started(self) -> bool:
        return self._started

    def _note(self, message: str) -> None:
        if self.on_note is None:
            return
        try:
            self.on_note(message)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def start(self) -> None:
        """Ask for consent and bring the stream up. Raises ScreenCastError."""
        if self._started:
            return
        Gst = _gst()

        try:
            self._bus = PortalBus()
        except PortalError as exc:
            raise ScreenCastError(str(exc)) from exc

        try:
            # types=1 is monitor only; this app never wants a single window.
            self._session = create_screencast(
                self._bus,
                types=1,
                restore_token=self.restore_token,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - any failure to start is ours to name
            self._close_partial()
            # Some failures carry no message at all (jeepney's TimeoutError), so
            # fall back to the type name rather than reporting an empty reason.
            raise ScreenCastError(
                f"could not start a screen-cast session: "
                f"{str(exc) or type(exc).__name__}"
            ) from exc

        if self._session.restore_token:
            self.restore_token = self._session.restore_token
            if self.on_restore_token is not None:
                try:
                    self.on_restore_token(self.restore_token)
                except Exception:  # noqa: BLE001 - persisting must not kill setup
                    pass

        try:
            self._fd = open_pipewire_remote(self._bus, self._session.session_path)
        except (PortalError, AttributeError, IndexError) as exc:
            self._close_partial()
            raise ScreenCastError(f"could not open the PipeWire remote: {exc}") from exc

        description = PIPELINE.format(node=self._session.node_id, fd=self._fd)
        try:
            self._pipeline = Gst.parse_launch(description)
        except Exception as exc:  # noqa: BLE001 - Gst raises its own error type
            self._close_partial()
            raise ScreenCastError(
                f"GStreamer could not build the capture pipeline: {exc}"
            ) from exc

        self._sink = self._pipeline.get_by_name("sink")
        self._gst = Gst
        self._pipeline.set_state(Gst.State.PLAYING)
        self._started = True

    def latest_frame(self) -> Image.Image:
        """The newest frame, blocking only until one is available."""
        if not self._started:
            self.start()
        Gst = self._gst

        started = time.monotonic()
        sample = self._sink.try_pull_sample(int(FRAME_TIMEOUT * Gst.SECOND))
        if sample is None:
            raise ScreenCastError(
                "no frame arrived from the screen-cast stream within "
                f"{FRAME_TIMEOUT:.0f}s; the session may have been revoked"
            )

        buffer = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        width = int(caps.get_value("width"))
        height = int(caps.get_value("height"))
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise ScreenCastError("could not map the frame buffer for reading")
        try:
            # frombytes copies, so the frame survives the unmap
            image = Image.frombytes("RGB", (width, height), bytes(info.data))
        finally:
            buffer.unmap(info)

        self.frames += 1
        self.total_elapsed += time.monotonic() - started
        self.size = (width, height)
        return image

    def _close_partial(self) -> None:
        """Tear down whatever was established before a later step failed."""
        if self._session is not None:
            try:
                self._session.close(self._bus)
            except Exception:  # noqa: BLE001
                pass
            self._session = None
        if self._fd is not None:
            try:
                import os

                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def close(self) -> None:
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(self._gst.State.NULL)
            except Exception:  # noqa: BLE001
                pass
            self._pipeline = None
            self._sink = None
        self._close_partial()
        self._started = False

    def __enter__(self) -> "ScreenCastStream":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def stats(self) -> dict:
        return {
            "frames": self.frames,
            "size": self.size,
            "avg_frame_ms": (
                round(1000 * self.total_elapsed / self.frames, 1) if self.frames else 0.0
            ),
        }
