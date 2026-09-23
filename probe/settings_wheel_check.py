"""Check the wheel no longer changes the display sliders.

Run:  .venv-gi/bin/python probe/settings_wheel_check.py

A `Gtk.Scale` changes value on every scroll it receives, and the settings dialog
is a tall scrolling column of them, so wheeling down to the buttons at the bottom
used to drag four style values with it. The guard is a capture-phase scroll
controller on each slider: capture runs on the way *down* the widget tree, before
the target's own (bubble-phase) handling, so the scale never sees the event.

GTK4 has no way to inject a pointer or wheel event, so the delivery order itself
cannot be exercised from a script. What this checks instead:

  * the phases really are capture-guard against bubble-built-in, which is the
    ordering the guard depends on;
  * the guard is on the path the wheel would take - `Gtk.Widget.pick()` at each
    slider returns the slider, and the guard hangs off it;
  * emitting the scale's own scroll handler really does change its value, so the
    bug being fixed is real and not imagined;
  * the guard's handler moves the dialog instead, and consumes the event.

A real dialog is shown for a few seconds. Nothing is captured or translated.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from tlkun import theme  # noqa: E402
from tlkun.config import Config  # noqa: E402
from tlkun.settings import SettingsDialog  # noqa: E402

CONFIG = Path("/home/chiba/workspace/tl-kun/config.json")
failures: list[str] = []

SLIDERS = ("font_scale", "width_scale", "target_lines", "source_lines")


def check(condition: bool, message: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        failures.append(message)


def pump(milliseconds: int) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def scroll_controllers(widget):
    return [
        c
        for c in widget.observe_controllers()
        if isinstance(c, Gtk.EventControllerScroll)
    ]


def main() -> int:
    cfg = Config.load(str(CONFIG))
    cfg.path = Path("/tmp/tlkun_wheel_probe.json")
    theme.install_for(cfg)
    dlg = SettingsDialog(None, cfg)
    dlg.set_default_size(780, 1080)
    dlg.present()
    pump(1200)

    print("-- every slider reacts to the wheel, and now has a capture guard --")
    adjustment = dlg.outer_scroller.get_vadjustment()
    for name in SLIDERS:
        slider = getattr(dlg, name)
        phases = [c.get_propagation_phase().value_nick for c in scroll_controllers(slider)]
        print(f"  {name:<14} scroll controllers by phase: {phases}")
        check(
            "capture" in phases,
            f"{name} has a capture-phase scroll controller",
        )
        check(
            phases.count("capture") == 1,
            f"{name} has exactly one guard (found {phases.count('capture')})",
        )

    print()
    print("-- the guard is on the path the wheel would take --")
    adjustment = dlg.outer_scroller.get_vadjustment()
    adjustment.set_upper(max(adjustment.get_upper(), 2000.0))
    for name in SLIDERS:
        slider = getattr(dlg, name)
        # Bring it into view first. A slider scrolled past the bottom of the
        # viewport has no pixels to aim at, and `pick()` correctly finds nothing
        # there - which would look like a failure of the guard rather than of
        # the aim.
        found, bounds = slider.compute_bounds(dlg)
        if found:
            adjustment.set_value(
                max(0.0, bounds.origin.y - dlg.get_height() / 2 + bounds.size.height / 2)
            )
            pump(200)
        # The slider's own coordinates are not the dialog's, so ask for its box
        # in dialog space and aim at the middle of it.
        found, bounds = slider.compute_bounds(dlg)
        if not found:
            check(False, f"could not locate {name} in the dialog")
            continue
        x = int(bounds.origin.x + bounds.size.width / 2)
        y = int(bounds.origin.y + bounds.size.height / 2)
        picked = dlg.pick(x, y, Gtk.PickFlags.DEFAULT)
        print(f"  {name:<14} wheel at ({x},{y}) targets {type(picked).__name__}")
        # Walk up from whatever was picked: the guard is on the slider, and has
        # to be on the route the event takes to reach it.
        node, on_path = picked, picked is slider
        while node is not None and not on_path:
            node = node.get_parent()
            on_path = node is slider
        check(on_path, f"the wheel over {name} is delivered through it")
        check(
            any(
                c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
                for c in scroll_controllers(slider)
            ),
            f"the guard on {name} is on that path",
        )

    print()
    print("-- the bug is real: the scale's own handler changes the value --")
    before = dlg.font_scale.get_value()
    builtin = [c for c in scroll_controllers(dlg.font_scale)]
    # Emitting on the controller runs whatever the scale connected to it, which
    # is exactly what happens when an unguarded wheel event reaches it.
    for controller in builtin:
        controller.emit("scroll", 0.0, 1.0)
    after = dlg.font_scale.get_value()
    check(
        after != before,
        f"the built-in handler does move the value ({before} -> {after})",
    )
    dlg.font_scale.set_value(before)

    print()
    print("-- the guard scrolls the dialog instead, and eats the event --")
    adjustment.set_value(0.0)
    adjustment.set_upper(max(adjustment.get_upper(), 2000.0))
    value_before = dlg.width_scale.get_value()
    guard = [
        c
        for c in scroll_controllers(dlg.width_scale)
        if c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
    ][0]
    consumed = dlg._scroll_the_dialog_instead(guard, 0.0, 1.0)
    pump(120)
    check(consumed is True, "the guard reports the event as handled")
    check(
        adjustment.get_value() > 0.0,
        f"the dialog scrolled instead (adjustment {adjustment.get_value():.0f})",
    )
    check(
        dlg.width_scale.get_value() == value_before,
        "the slider's value did not move",
    )

    adjustment.set_value(0.0)
    dlg._scroll_the_dialog_instead(guard, 0.0, -1.0)
    pump(120)
    check(adjustment.get_value() == 0.0, "scrolling up at the top is clamped, not negative")

    dlg.close()
    pump(200)
    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK — the wheel over a display slider scrolls the dialog, not the value")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
