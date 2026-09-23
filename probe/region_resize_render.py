"""Draw the region picker's canvas offscreen and check its grab handles.

Two things this covers that nothing else does:

* `RegionPicker._on_draw` is never exercised by the tests - they stub the preview
  and the canvas is never allocated - so a mistake in the drawing code is a
  runtime crash on the one screen the user needs to work;
* the handles are the affordance for resizing. "You can drag the edges" is only
  discoverable if the edges look grabbable, so where they land is checked in
  pixels rather than by eye: a grip at every corner and at the middle of every
  edge, and none in the middle of the box (that is the move handle).

Each grip is sampled a couple of pixels *inside* the edge, at a point the 2px
border cannot reach - sampling on the anchor itself would report the border and
call a missing handle a pass.

Writes .cache/region_resize_handles.png so the result can be looked at.

Run:  .venv/bin/python probe/region_resize_render.py
"""
import sys
from io import BytesIO
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from PIL import Image  # noqa: E402

from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.picker import RegionPicker  # noqa: E402

OUT = APP_DIR / ".cache" / "region_resize_handles.png"
SCREEN = (1600, 1000)
CANVAS = (800, 500)  # what the screenshot is letterboxed into
BOX = (300, 200, 400, 200)
HANDLE_RGB = (255, 217, 76)  # cr.set_source_rgb(1.0, 0.85, 0.3)
INSIDE = 2  # how far into the grip to sample, clear of the border line


def _png() -> bytes:
    """A screenshot with structure, so the crop and the dimming are visible."""
    image = Image.new("RGB", SCREEN, (40, 44, 52))
    for y in range(0, SCREEN[1], 40):
        for x in range(0, SCREEN[0], 40):
            if (x // 40 + y // 40) % 2:
                image.paste(
                    (70, 74, 82), (x, y, min(x + 40, SCREEN[0]), min(y + 40, SCREEN[1]))
                )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sample(surface: cairo.ImageSurface, x: float, y: float) -> tuple[int, int, int]:
    """The pixel at (x, y) as (r, g, b)."""
    surface.flush()
    data = surface.get_data()
    offset = int(y) * surface.get_stride() + int(x) * 4
    blue, green, red = data[offset], data[offset + 1], data[offset + 2]  # ARGB32 is BGRA
    return (red, green, blue)


def near(pixel, expected, slack: int = 12) -> bool:
    return all(abs(a - b) <= slack for a, b in zip(pixel, expected))


def main() -> int:
    panel_mod.TranslatorPanel.present = lambda self: None  # no window on screen
    if not Gtk.init_check():
        print("no display: GTK cannot be initialised")
        return 2

    cfg = Config.load()
    cfg.path = Path("/tmp/lintranslator_resize_render.json")  # never the real config

    app = Gtk.Application(
        application_id="dev.lintranslator.resizerender", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    failures: list[str] = []

    def on_activate(_app):
        picker = RegionPicker(app, cfg, screenshot_png=_png())
        picker.present = lambda: None
        picker._panel.hotkey_enabled = False
        picker.sel = BOX
        # The canvas is never allocated without a presented window; pretend it
        # was, so the draw path runs with real numbers.
        picker.area.get_width = lambda: CANVAS[0]
        picker.area.get_height = lambda: CANVAS[1]

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, *CANVAS)
        try:
            picker._on_draw(picker.area, cairo.Context(surface), *CANVAS)
        except Exception as exc:  # noqa: BLE001 - a draw crash is the finding
            failures.append(f"_on_draw raised {type(exc).__name__}: {exc}")
        else:
            surface.write_to_png(str(OUT))

        m = picker.math()
        x0, y0 = m.to_widget(BOX[0], BOX[1])
        x1, y1 = m.to_widget(BOX[0] + BOX[2], BOX[1] + BOX[3])
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        print(f"canvas {CANVAS[0]}x{CANVAS[1]} (scale {m.scale:.2f}), box {BOX}")
        print(f"box in widget space: {x0:.0f},{y0:.0f} to {x1:.0f},{y1:.0f}")

        # A 3x3 grid of anchors, less the centre: corners and edge midpoints.
        for ax in (x0, mx, x1):
            for ay in (y0, my, y1):
                if ax == mx and ay == my:
                    continue
                # Step inside the box, away from the border the anchor sits on.
                sx = ax + (INSIDE if ax == x0 else -INSIDE if ax == x1 else 0)
                sy = ay + (INSIDE if ay == y0 else -INSIDE if ay == y1 else 0)
                pixel = sample(surface, sx, sy)
                ok = near(pixel, HANDLE_RGB)
                if not ok:
                    failures.append(f"no grip at {ax:.0f},{ay:.0f} (pixel {pixel})")
                print(f"  grip {ax:6.1f},{ay:6.1f}: {pixel} {'ok' if ok else 'MISSING'}")

        middle = sample(surface, mx, my)
        if near(middle, HANDLE_RGB):
            failures.append("the middle of the box was painted as a grip")
        print(f"  middle {mx:6.1f},{my:6.1f}: {middle} (move handle, not a grip)")

        picker._shutdown_panel()
        app.quit()

    app.connect("activate", on_activate)
    app.run([])

    print(f"\nwrote {OUT}" if OUT.exists() else "\nnothing written")
    print("=" * 70)
    print(f"failed: {failures or 'none'}")
    print("PASS" if not failures else f"FAIL: {len(failures)} problem(s)")
    print("=" * 70)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
