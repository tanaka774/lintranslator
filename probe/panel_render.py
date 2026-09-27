"""Render the two windows of the picker flow, to check the layout visually."""
import os
import sys
from io import BytesIO
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import cairo  # noqa: E402
import gi  # noqa: E402
from PIL import Image  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator import picker as picker_mod  # noqa: E402
from lintranslator.config import Config, Region  # noqa: E402
from lintranslator.portal import ScreenshotPortal  # noqa: E402

OUT = APP_DIR / "data"
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
    cfg = Config.load()
    cfg.path = CONFIG
    cfg.translate.backend = "none"  # no network calls in a render probe
    # Pinned so a render meant to be published does not drift with the
    # developer's own config; the README's model for local translation.
    cfg.translate.model = "hy-mt2:1.8b"

    # `LINTRANSLATOR_RENDER_SOURCE=<png>` renders over a supplied frame instead of
    # grabbing the screen; a live grab must never be the frame that gets published.
    supplied = os.environ.get("LINTRANSLATOR_RENDER_SOURCE")
    if supplied:
        raw = Path(supplied).read_bytes()
        with Image.open(BytesIO(raw)) as im:
            size = im.size
        png, elapsed = raw, 0.0
        # The synthetic frame draws its dialogue box at the app's default region
        # (see `make_render_frame.py`), so point the picker at it.
        cfg.capture.region = Region(0.10, 0.78, 0.80, 0.12, "fraction")
        print(f"rendering over {supplied} {size}", flush=True)
    else:
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
            # The probe opens the card, in the state a card opened without
            # `--start` is in. Its own slot below: a window presented and
            # snapshotted in the same slot is not painted.
            panel.show_idle()
            panel.present()
            GLib.timeout_add(400, step_idle_panel)
            return False

        def step_idle_panel():
            shoot(panel, "render_panel_idle")
            picker._on_start()
            # This probe is about layout, not capture: stop the pipeline that
            # `_on_start` just started so it does not read the screen.
            if panel.worker is not None:
                panel.worker.stop()
                panel.worker = None
            # `_on_start` hides the picker a beat later (it must not be in the
            # screenshot the pipeline reads); bring it back in this slot, so the
            # snapshot slot below sees a drawn window.
            GLib.timeout_add(400, bring_picker_back)
            GLib.timeout_add(1000, step_watching)
            return False

        def bring_picker_back():
            picker.set_visible(True)
            picker.present()
            return False

        def step_watching():
            print(
                f"  after Start: picker mapped={picker.get_mapped()} "
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
