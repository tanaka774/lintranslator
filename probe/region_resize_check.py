"""Can the selected box be resized? Every edge, in both directions.

The behaviour being checked: grabbing an edge or a corner of the selection and
dragging it *inwards* did nothing at all. `SelectionMath.apply_drag` rebuilt the
moving edge from `min`/`max` of the two pointer positions, so an edge could only
travel *away* from the one opposite it: dragging the right edge left pinned it to
where the pointer went down, and it never followed the drag. Growing the box
worked; shrinking it did not. That is the whole of "I can't resize the box".

This probe drives the real `RegionPicker` handlers (`_on_drag_begin` /
`_on_drag_update` / `_on_drag_end`) with the cumulative offsets a GtkGestureDrag
reports, and compares the box that comes back with the box the pointer describes.
It also measures the second half of the problem: how close to an edge the pointer
had to land to grab it at all (8 px of a 2560-wide screenshot is under 4 px on
screen once the shot is letterboxed into the canvas, which is a hard target).

The canvas is forced to a 1:1 mapping - widget pixel == screenshot pixel - so the
expected boxes can be read straight off the pointer positions. The scaled and
letterboxed mapping is covered by tests/test_selection.py. No window is presented
and no screen is captured: this is drag arithmetic, not pixels.

Run:  .venv-gi/bin/python probe/region_resize_check.py
"""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from PIL import Image  # noqa: E402

from tlkun import panel as panel_mod  # noqa: E402
from tlkun.config import Config  # noqa: E402
from tlkun.picker import RegionPicker  # noqa: E402
from tlkun.selection import SelectionMath  # noqa: E402

SCREEN = (2560, 1440)
# The box every case starts from: a wide dialogue box, so every edge is long
# enough to grab at its midpoint.
BOX = (1000, 700, 400, 200)
STEP = 120  # how far the pointer travels in a resize case
GRAB = 8  # the picker's HANDLE: px inside edge that still counts as grabbing it

# mode, what the drag means, press, release, expected box
CASES = [
    ("e", "grow right", (1400, 800), (1520, 800), (1000, 700, 520, 200)),
    ("e", "shrink left", (1400, 800), (1280, 800), (1000, 700, 280, 200)),
    ("w", "grow left", (1000, 800), (880, 800), (880, 700, 520, 200)),
    ("w", "shrink right", (1000, 800), (1120, 800), (1120, 700, 280, 200)),
    ("n", "grow up", (1200, 700), (1200, 580), (1000, 580, 400, 320)),
    ("n", "shrink down", (1200, 700), (1200, 820), (1000, 820, 400, 80)),
    ("s", "grow down", (1200, 900), (1200, 1020), (1000, 700, 400, 320)),
    ("s", "shrink up", (1200, 900), (1200, 780), (1000, 700, 400, 80)),
    ("nw", "grow out", (1000, 700), (880, 580), (880, 580, 520, 320)),
    ("nw", "shrink in", (1000, 700), (1120, 820), (1120, 820, 280, 80)),
    ("ne", "grow out", (1400, 700), (1520, 580), (1000, 580, 520, 320)),
    ("ne", "shrink in", (1400, 700), (1280, 820), (1000, 820, 280, 80)),
    ("sw", "grow out", (1000, 900), (880, 1020), (880, 700, 520, 320)),
    ("sw", "shrink in", (1000, 900), (1120, 780), (1120, 700, 280, 80)),
    ("se", "grow out", (1400, 900), (1520, 1020), (1000, 700, 520, 320)),
    ("se", "shrink in", (1400, 900), (1280, 780), (1000, 700, 280, 80)),
    ("move", "move box", (1200, 800), (1300, 850), (1100, 750, 400, 200)),
]


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", SCREEN, (30, 30, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def build_picker(app, cfg) -> RegionPicker:
    picker = RegionPicker(app, cfg, screenshot_png=_png())
    picker.present = lambda: None  # nothing lands on the real screen
    picker._panel.hotkey_enabled = False  # never take a global shortcut from a probe
    # 1:1 mapping: widget coordinates and screenshot pixels are the same numbers.
    picker.math = lambda: SelectionMath(
        screen_w=SCREEN[0], screen_h=SCREEN[1],
        widget_w=float(SCREEN[0]), widget_h=float(SCREEN[1]),
    )
    # The preview crops, runs tesseract on a worker and schedules GLib timers;
    # none of that is what this probe is measuring.
    picker._schedule_preview = lambda: None
    picker._refresh_preview = lambda: None
    return picker


def gesture(picker, press, release, events: int = 4):
    """A press-and-drag the way GtkGestureDrag reports it: drag-begin at the press
    point, then cumulative offsets, then drag-end."""
    picker.sel = BOX
    picker._on_drag_begin(None, press[0], press[1])
    mode = picker._drag_mode
    dx, dy = release[0] - press[0], release[1] - press[1]
    for i in range(1, events + 1):
        picker._on_drag_update(None, dx * i / events, dy * i / events)
    picker._on_drag_end(None, dx, dy)
    return mode, picker.sel


def drag_table(picker) -> list[tuple[str, bool]]:
    print(f"box under test: x={BOX[0]} y={BOX[1]} w={BOX[2]} h={BOX[3]} "
          f"(screen {SCREEN[0]}x{SCREEN[1]}, 1:1 canvas)")
    print(f"\n{'grab':<5} {'drag':<13} {'expected':<24} {'got':<24} verdict")
    results = []
    for mode, intent, press, release, expected in CASES:
        got_mode, got = gesture(picker, press, release)
        ok = got_mode == mode and got == expected
        results.append((f"{mode} {intent}", ok))
        print(
            f"{mode:<5} {intent:<13} {str(expected):<24} {str(got):<24} "
            f"{'ok' if ok else 'FAIL' + ('' if got_mode == mode else f' (grabbed as {got_mode})')}"
        )
    return results


def edge_cases(picker) -> list[tuple[str, bool]]:
    """The awkward ones: grabbing off-centre, and every clamp."""
    print("\nclamps and off-edge grabs:")
    results = []
    checks = [
        (
            f"grabbed {5}px inside the right edge and dragged {STEP}px out",
            (1400 - 5, 800), (1400 - 5 + STEP, 800), (1000, 700, 400 + STEP, 200),
        ),
        (
            "dragged the right edge far past the left one (must not invert)",
            (1400, 800), (900, 800), (1000, 700, 4, 200),
        ),
        (
            "dragged the top edge far past the bottom one (must not invert)",
            (1200, 700), (1200, 1200), (1000, 896, 400, 4),
        ),
        (
            "dragged the left edge past the screen edge",
            (1000, 800), (-200, 800), (0, 700, 1400, 200),
        ),
    ]
    for label, press, release, expected in checks:
        _mode, got = gesture(picker, press, release)
        ok = got == expected
        results.append((label, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {label}\n"
              f"       expected {expected}, got {got}")
    return results


def handle_reach() -> tuple[float, float, bool]:
    """How close to an edge the pointer has to land, in *screen* pixels.

    The tolerance is what the user aims with, so it is reported in widget px: the
    canvas is where the aiming happens, and the same 8 screenshot px is a much
    smaller target once the shot is scaled into the canvas.
    """
    # The picker sizes itself from the monitor: clamp(0.78 * geometry) on both
    # axes, minus the 320px sidebar.
    geo_w, geo_h = 2560, 1440
    window_w = max(900, min(1500, int(geo_w * 0.78)))
    window_h = max(600, min(980, int(geo_h * 0.78)))
    canvas_w, canvas_h = window_w - 320, window_h - 20
    m = SelectionMath(geo_w, geo_h, float(canvas_w), float(canvas_h))
    previous = GRAB * m.scale  # the old rule: 8 screenshot px, scaled, full stop
    now, _ = m.handle_tolerance(GRAB)
    print("\nhow close the pointer must land to grab an edge:")
    print(f"  canvas {canvas_w}x{canvas_h} for a {geo_w}x{geo_h} screen "
          f"(scale {m.scale:.3f})")
    print(f"  before: {previous:.1f} widget px ({GRAB} screenshot px)")
    print(f"  now   : {now:.1f} widget px "
          f"({now / m.scale:.0f} screenshot px at this zoom)")
    return previous, now, now >= 6.0


def main() -> int:
    panel_mod.TranslatorPanel.present = lambda self: None  # no window on screen

    if not Gtk.init_check():
        print("no display: this probe drives the picker's own drag handlers")
        return 2

    cfg = Config.load("/home/chiba/workspace/tl-kun/config.json")
    cfg.path = Path("/tmp/tlkun_resize_probe.json")  # never the real config

    app = Gtk.Application(
        application_id="dev.tlkun.resizecheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    results: list[tuple[str, bool]] = []

    def on_activate(_app):
        picker = build_picker(app, cfg)
        results.extend(drag_table(picker))
        results.extend(edge_cases(picker))
        picker._shutdown_panel()
        app.quit()

    app.connect("activate", on_activate)
    app.run([])

    previous, now, reachable = handle_reach()
    results.append(("the pointer can reach an edge without pixel-hunting", reachable))

    failed = [name for name, ok in results if not ok]
    shrinking = [name for name, ok in results if not ok and "shrink" in name]
    print("\n" + "=" * 70)
    print(f"cases checked  : {len(results)}")
    if shrinking:
        print(f"failed shrinks : {len(shrinking)} of 8 - the edge never follows the drag")
    print(f"failed         : {failed or 'none'}")
    print("PASS" if not failed else f"FAIL: {len(failed)} case(s) wrong")
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
