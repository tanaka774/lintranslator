"""Frame acquisition: full-screen grab (portal) -> crop to the configured region.

The portal always returns the whole screen, so the crop happens here. Only the
cropped region is kept, which is what makes the rest of the pipeline cheap.

Timing note: `Frame.elapsed` is the *whole* cost of one grab - portal round trip,
PNG decode and crop - because that is the number the poll budget has to cover.
The portal round trip alone (measured 171-425 ms on this machine, against ~33 ms
of decode) is reported separately as `portal_elapsed`, so a slow compositor can
be told apart from a slow decode.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image

from .config import Region
from .portal import PortalError, ScreenshotPortal


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
    """Grabs the configured region through the portal.

    Screen size is discovered on the first grab and cached, so a
    fraction-based region is resolved once per run (and re-resolved if the
    resolution changes underneath us).
    """

    def __init__(self, region: Region, portal: ScreenshotPortal | None = None) -> None:
        self.region = region
        self._portal = portal or ScreenshotPortal()
        self._screen_size: tuple[int, int] | None = None
        self.grabs = 0
        self.failures = 0
        self.total_elapsed = 0.0
        self.total_portal_elapsed = 0.0

    @property
    def screen_size(self) -> tuple[int, int] | None:
        return self._screen_size

    def region_pixels(self) -> tuple[int, int, int, int]:
        if self._screen_size is None:
            raise PortalError("screen size unknown; grab a frame first")
        return self.region.to_pixels(*self._screen_size)

    def grab_full(self) -> FullFrame:
        """Capture the entire screen (no cropping)."""
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
            "screen": self._screen_size,
            # Total cost per grab, which is what the poll interval must cover.
            "avg_grab_ms": round(1000 * self.total_elapsed / self.grabs, 1) if self.grabs else 0.0,
            # The compositor's share of it.
            "avg_portal_ms": (
                round(1000 * self.total_portal_elapsed / self.grabs, 1) if self.grabs else 0.0
            ),
        }
