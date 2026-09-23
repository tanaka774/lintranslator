"""Render the two windows of the picker flow, to check the layout visually.

Run:  .venv-gi/bin/python probe/panel_render.py

Renders the picker and the panel it owns exactly as `lintranslator gui` builds them, and
exits. Used to confirm that the panel gained a reachable **Region** button (the
way back to a picker that now minimises itself) without the control row
overflowing, and that the picker's own chrome - sidebar and toolbar - reads
correctly both idle and while watching.

`render_picker_watching.png` is the second state: "Watch live" has become
"Apply box", the picker has taken itself off the screen, and the sidebar says
why it is paused. The picker is brought back on screen for that shot (a hidden
window has no render node at all), in a timer slot of its own - changing a
label and snapshotting it in the *same* slot gives "not paintable", because the
cached node is invalidated and the compositor has not drawn the replacement.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator import picker as picker_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.portal import ScreenshotPortal  # noqa: E402

OUT = Path("/home/chiba/workspace/lintranslator/data")
CONFIG = Path("/tmp/lintranslator_probe_config.json")  # never write the real config
failures: list[str] = []


def shoot(widget, name: str) -> None:
    w, h = widget.get_width(), widget.get_height()
    if w <= 1 or h <= 1:
        failures.append(f"{name} was never laid out ({w}x{h})")
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        failures.append(f"{name} not paintable")
        return
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    node.draw(cairo.Context(surface))
    surface.write_to_png(str(OUT / f"{name}.png"))
    print(f"  wrote {name}.png ({w}x{h})", flush=True)


def main() -> int:
    cfg = Config.load("/home/chiba/workspace/lintranslator/config.json")
    cfg.path = CONFIG
    cfg.translate.backend = "none"  # no network calls in a render probe

    print("grabbing the screen for the picker to display...", flush=True)
    portal = ScreenshotPortal()
    png, size, elapsed = portal.grab()
    portal.close()
    print(f"  {size} in {elapsed * 1000:.0f} ms", flush=True)

    app = Gtk.Application(
        application_id="dev.lintranslator.render", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app):
        picker = picker_mod.RegionPicker(app, cfg, screenshot_png=png)
        picker.present()
        panel = picker._panel

        def step_idle():
            shoot(picker, "render_picker_idle")
            shoot(panel, "render_panel_idle")
            # Now watch: the picker minimises itself and the panel takes over.
            picker._on_start()
            # This probe is about layout, not capture: stop the pipeline that
            # `_on_start` just started so it does not read the screen.
            if panel.worker is not None:
                panel.worker.stop()
                panel.worker = None
            # `_on_start` hides the picker a beat later (it must not be in the
            # screenshot the pipeline reads). Bring it back for its own portrait
            # - in this slot, so the snapshot slot below sees a drawn window.
            GLib.timeout_add(400, bring_picker_back)
            GLib.timeout_add(1000, step_watching)
            return False

        def bring_picker_back():
            picker.set_visible(True)
            picker.present()
            return False

        def step_watching():
            print(
                f"  after Watch live: picker mapped={picker.get_mapped()} "
                f"visible={picker.get_visible()}",
                flush=True,
            )
            shoot(panel, "render_panel_watching")
            shoot(picker, "render_picker_watching")
            print(f"  panel Region button visible: {panel.region_btn.get_visible()}")
            print(f"  picker watch button label  : {picker.watch_btn.get_label()!r}")
            print(f"  picker watch status        : {picker.watch_status.get_text()[:70]!r}")
            picker._shutdown_panel()
            app.quit()
            return False

        GLib.timeout_add(1200, step_idle)

    app.connect("activate", on_activate)
    GLib.timeout_add(15000, lambda: (app.quit(), False)[1])
    app.run([])

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK — both windows rendered", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
