"""Check that a wheel over a display slider scrolls the dialog, not the value."""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from lintranslator import theme  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.settings import SettingsDialog  # noqa: E402

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
    cfg = Config.load()
    cfg.path = Path("/tmp/lintranslator_wheel_probe.json")
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
        # bring it into view: a slider past the bottom of the viewport has no pixels to pick
        found, bounds = slider.compute_bounds(dlg)
        if found:
            adjustment.set_value(
                max(0.0, bounds.origin.y - dlg.get_height() / 2 + bounds.size.height / 2)
            )
            pump(200)
        # the slider's own coordinates are not the dialog's, so ask for its box in dialog space
        found, bounds = slider.compute_bounds(dlg)
        if not found:
            check(False, f"could not locate {name} in the dialog")
            continue
        x = int(bounds.origin.x + bounds.size.width / 2)
        y = int(bounds.origin.y + bounds.size.height / 2)
        picked = dlg.pick(x, y, Gtk.PickFlags.DEFAULT)
        print(f"  {name:<14} wheel at ({x},{y}) targets {type(picked).__name__}")
        # walk up from whatever was picked: the guard has to be on the route the wheel takes
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
    # emitting on the controller runs what the scale connected to it, as a real wheel event would
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
