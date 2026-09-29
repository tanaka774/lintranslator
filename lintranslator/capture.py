"""Frame acquisition: full-screen grab (portal) -> crop to the configured region."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image

from .config import Region
from .portal import PortalError, ScreenshotPortal
from .screencast import ScreenCastStream, screencast_available


@dataclass
class Frame:
    image: Image.Image
    full_size: tuple[int, int]
    region: tuple[int, int, int, int]  # x, y, w, h in screen pixels
    elapsed: float  # total grab cost: portal + decode + crop
    portal_elapsed: float = 0.0  # the D-Bus round trip inside `elapsed`
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    def save(self, path) -> None:
        self.image.save(path)


@dataclass
class FullFrame:
    """A whole-screen capture, used by the region picker."""

    image: Image.Image
    size: tuple[int, int]
    elapsed: float
    portal_elapsed: float = 0.0

    def to_png_bytes(self) -> bytes:
        buffer = BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


class ScreenGrabber:
    """Grabs the configured region, from a live stream where there is one.

    The polling loop reads the stream; `grab_full` keeps the screenshot portal,
    because the picker's single user-initiated grab is what that API is for.
    """

    def __init__(
        self,
        region: Region,
        portal: ScreenshotPortal | None = None,
        on_note: Callable[[str], None] | None = None,
        stream: ScreenCastStream | None = None,
    ) -> None:
        self.region = region
        self._portal = portal or ScreenshotPortal(on_note=on_note)
        self._stream = stream
        self._on_note = on_note
        self._screen_size: tuple[int, int] | None = None
        self.grabs = 0
        self.failures = 0
        #: Set once a stream has failed and the portal took over for good.
        self.stream_failures = 0
        self.total_elapsed = 0.0
        self.total_portal_elapsed = 0.0

    @property
    def screen_size(self) -> tuple[int, int] | None:
        return self._screen_size

    def region_pixels(self) -> tuple[int, int, int, int]:
        if self._screen_size is None:
            raise PortalError("screen size unknown; grab a frame first")
        return self.region.to_pixels(*self._screen_size)

    def _note(self, message: str) -> None:
        if self._on_note is None:
            return
        try:
            self._on_note(message)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the loop
            pass

    def _stream_full(self) -> FullFrame | None:
        """A frame from the live stream, or None to use the portal instead.

        A failed stream is not retried: a revoked session fails on every poll,
        and a warning per poll is worse than the fallback.
        """
        if self._stream is None:
            return None
        started = time.monotonic()
        try:
            image = self._stream.latest_frame()
        except Exception as exc:  # noqa: BLE001 - any stream failure falls back
            self._stream.close()
            self._stream = None
            self.stream_failures += 1
            # Not every exception carries a message, and "( )" tells the user
            # nothing; the type name at least says what failed. An exception is
            # always truthy, so the message itself is what has to be checked.
            reason = str(exc) or type(exc).__name__
            self._note(
                f"screen-cast capture stopped working ({reason}); "
                "falling back to the screenshot portal, which writes a "
                "temporary file per frame"
            )
            return None
        elapsed = time.monotonic() - started
        self._screen_size = image.size
        return FullFrame(image=image, size=image.size, elapsed=elapsed)

    def grab_full(self) -> FullFrame:
        """Capture the entire screen (no cropping), preferring the live stream."""
        streamed = self._stream_full()
        if streamed is not None:
            self.grabs += 1
            self.total_elapsed += streamed.elapsed
            return streamed

        started = time.monotonic()
        png, size, portal_elapsed = self._portal.grab()
        with Image.open(BytesIO(png)) as im:
            full = im.convert("RGB")
            full.load()
        elapsed = time.monotonic() - started
        self._screen_size = size
        self.grabs += 1
        self.total_elapsed += elapsed
        self.total_portal_elapsed += portal_elapsed
        return FullFrame(
            image=full, size=size, elapsed=elapsed, portal_elapsed=portal_elapsed
        )

    def grab(self) -> Frame:
        full = self.grab_full()
        x, y, w, h = self.region.to_pixels(*full.size)
        crop = full.image.crop((x, y, x + w, y + h))
        return Frame(
            image=crop,
            full_size=full.size,
            region=(x, y, w, h),
            elapsed=full.elapsed,
            portal_elapsed=full.portal_elapsed,
        )

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._portal.close()

    def __enter__(self) -> "ScreenGrabber":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def stats(self) -> dict:
        return {
            "grabs": self.grabs,
            "failures": self.failures,
            # Screenshots the portal left on disk; anything but 0 is litter.
            "leaks": self._portal.leaks,
            # "screencast" writes nothing; "portal-screenshot" writes a PNG per
            # grab that is deleted again, and leaks if the delete fails.
            "source": "screencast" if self._stream is not None else "portal-screenshot",
            "stream_failures": self.stream_failures,
            "screen": self._screen_size,
            "avg_grab_ms": round(1000 * self.total_elapsed / self.grabs, 1) if self.grabs else 0.0,
            "avg_portal_ms": (
                round(1000 * self.total_portal_elapsed / self.grabs, 1) if self.grabs else 0.0
            ),
        }


#: Read a live PipeWire stream; writes nothing to disk.
SCREENCAST = "portal-screencast"
#: One-shot portal screenshots; each grab writes a PNG the app then deletes.
PORTAL_SCREENSHOT = "portal-screenshot"
BACKENDS = (PORTAL_SCREENSHOT, SCREENCAST)


def build_grabber(
    config,
    on_note: Callable[[str], None] | None = None,
    on_restore_token: Callable[[str], None] | None = None,
) -> ScreenGrabber:
    """The grabber for `config.capture.backend`.

    An unknown backend is refused rather than ignored: silently reading it as
    "portal-screenshot" is a file per grab nobody asked for.
    """
    backend = config.capture.backend
    if backend not in BACKENDS:
        # There is no capture row in Settings, so name the config key itself
        # rather than sending the user to a window that cannot change it.
        raise ValueError(
            f"capture.backend {backend!r} is not a backend this version has; "
            f"set it to {' or '.join(BACKENDS)} in the config file"
        )
    stream = None
    if backend == SCREENCAST:
        available, why = screencast_available()
        if available:
            stream = ScreenCastStream(
                restore_token=config.capture.restore_token,
                on_note=on_note,
                on_restore_token=on_restore_token,
            )
        elif on_note is not None:
            # Not worth asking: on KDE/X11 the desktop answers with its own
            # "screen sharing is not available" dialog and an error code that
            # says nothing. Say what is happening once, calmly, and read the
            # screen the older way instead.
            try:
                on_note(
                    f"reading the screen with the screenshot portal, because {why}. "
                    "Each read writes a screenshot file that is deleted again."
                )
            except Exception:  # noqa: BLE001 - a UI callback must not break startup
                pass
    return ScreenGrabber(config.capture.region, on_note=on_note, stream=stream)
