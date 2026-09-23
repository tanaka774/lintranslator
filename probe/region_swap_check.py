"""Does the captured area follow the selected box?

The regression this guards: pressing "Watch live" again after moving the box wrote
the new region into the config but left the running pipeline on the old one, and
only Save applied it - by tearing the panel down and rebuilding it, which reloaded
the model and let the compositor move the card.

Nothing here captures the screen: `PipelineThread` is replaced with a recorder and
the panel is never presented, so this is a wiring check, not a live one. The config
it writes goes to /tmp, never to the real one.

Run:  .venv/bin/python probe/region_swap_check.py
"""
import sys
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.portal import ScreenshotPortal  # noqa: E402

CONSTRUCTED = []


class RecordingPipelineThread:
    """Stands in for the real worker: records the region it was pointed at."""

    def __init__(self, config, outbox, gate=None):
        self.config = config
        self.gate = gate
        self.region = config.capture.region
        self.requested = []
        CONSTRUCTED.append(self)

    def start(self):
        pass

    def stop(self, timeout: float = 1.0):
        pass

    def request_region(self, region):
        self.requested.append(region)
        self.region = region


class QuietPanel(panel_mod.TranslatorPanel):
    """The real panel, but presenting it does nothing (no window on screen)."""

    def present(self):
        return None


def same(a, b) -> bool:
    return (a.x, a.y, a.w, a.h, a.mode) == (b.x, b.y, b.w, b.h, b.mode)


def main() -> int:
    panel_mod.PipelineThread = RecordingPipelineThread
    panel_mod.TranslatorPanel = QuietPanel

    cfg = Config.load()
    cfg.path = Path("/tmp/lintranslator_probe_config.json")  # never write the real config

    portal = ScreenshotPortal()
    png, size, elapsed = portal.grab()
    portal.close()
    print(f"screen {size} (portal {elapsed * 1000:.0f} ms)")

    app = Gtk.Application(
        application_id="dev.lintranslator.swapcheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    outcome = {}

    def on_activate(_app):
        from lintranslator.picker import RegionPicker

        picker = RegionPicker(app, cfg, screenshot_png=png)
        w, h = picker.screen_size

        picker._on_start()
        first = picker._panel.worker
        print(f"\npress 1: selection={picker.sel}")
        print(f"         worker built with={first.region}")

        # The user drags a different box, which ends the drag.
        picker.sel = (int(0.52 * w), int(0.80 * h), int(0.30 * w), int(0.10 * h))
        picker._on_drag_end(None, 0.0, 0.0)
        print(f"\ndragged:  selection={picker.sel}")
        print(f"         worker re-pointed to={first.region} (live, no restart)")

        picker._on_start()
        print(f"\npress 2: config region={cfg.capture.region}")
        print(f"         panel worker is the SAME object: {picker._panel.worker is first}")
        print(f"         worker capturing        ={picker._panel.worker.region}")

        outcome["config"] = cfg.capture.region
        outcome["worker"] = picker._panel.worker.region
        outcome["same_worker"] = picker._panel.worker is first
        outcome["workers"] = len(CONSTRUCTED)
        picker._shutdown_panel()
        app.quit()

    app.connect("activate", on_activate)
    app.run([])

    ok = (
        outcome["same_worker"]
        and outcome["workers"] == 1
        and same(outcome["config"], outcome["worker"])
    )
    print("\n" + "=" * 70)
    print(f"workers constructed         : {outcome['workers']}")
    print(f"config area == captured area: {same(outcome['config'], outcome['worker'])}")
    print("PASS" if ok else "FAIL: the box on screen is not what is being read")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
