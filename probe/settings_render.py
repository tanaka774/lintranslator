"""Render the settings dialog to PNGs so UI changes can be eyeballed.

Run:  .venv/bin/python probe/settings_render.py

Drives GTK with an explicit GLib.MainLoop rather than app.run(): the settings
dialog is parented to nothing here, so an application would exit as soon as its
loop settles. Not part of the package.
"""
import sys
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from lintranslator import theme  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.settings import BACKENDS, SettingsDialog  # noqa: E402

OUT = APP_DIR / ".cache"
RENDER = ["openrouter", "openai", "deepl", "chat", "none"]
TIMEOUT_MS = 30_000  # hard stop so a stuck render can never hang the shell


def shoot(widget: Gtk.Widget, name: str) -> None:
    w, h = widget.get_width(), widget.get_height()
    if w <= 1 or h <= 1:
        print(f"  {name}: not laid out yet ({w}x{h})", flush=True)
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        # The widget's cached render node is dropped when its content changes and
        # is only restored by the next frame. Snapshotting in the same callback
        # that changed something lands here.
        print(f"  {name}: no render node yet", flush=True)
        return
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    node.draw(cairo.Context(surf))
    surf.write_to_png(str(OUT / f"{name}.png"))
    print(f"  wrote {name}.png {w}x{h}", flush=True)


def main() -> int:
    loop = GLib.MainLoop()
    cfg = Config.load()
    # The real app installs this in gui.on_activate, before any window.
    theme.install_for(cfg)
    dlg = SettingsDialog(None, cfg)
    # Deliberately not resized: the dialog now measures its own column, and a
    # fixed frame here would hide the thing worth eyeballing - whether the
    # toolbar sits under the content or in a band of dead space.
    dlg.present()

    steps = list(RENDER)
    state = {"i": 0, "shots": 0}

    def finish():
        print(f"done, {state['shots']} snapshot(s)", flush=True)
        loop.quit()
        return False

    def next_step():
        if state["i"] >= len(steps):
            return finish()
        backend = steps[state["i"]]
        state["i"] += 1
        idx = next(i for i, (k, _) in enumerate(BACKENDS) if k == backend)
        dlg.backend_dd.set_selected(idx)
        state["shots"] += 1
        # Let the backend handler and a layout pass settle before capturing.
        GLib.timeout_add(500, lambda: (shoot(dlg, f"settings_{backend}"),
                                       next_step(), False)[2])
        return False

    # Give the compositor a beat to map and allocate the window first.
    GLib.timeout_add(900, next_step)
    GLib.timeout_add(TIMEOUT_MS, finish)
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
