"""End-to-end live latency: scripted dialogue -> real capture -> translation.

A fullscreen window draws one dialogue line at a time inside the configured
region, while the real pipeline (real xdg-desktop-portal capture, real tesseract,
echoing translator) runs in a worker thread. For each line it reports the time
from the line appearing on screen to the translation being ready - the number a
player actually feels.

Run:  .venv/bin/python probe/live_lines.py [seconds-per-line]
"""
import sys
import threading
import time
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from lintranslator.config import Config  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402

SECONDS_PER_LINE = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
# --overlap draws a lintranslator status line inside the box, above the dialogue, which
# is what a panel clipping the top of the region looks like to OCR.
OVERLAP = "--overlap" in sys.argv

LINES = [
    "Although the trial won't be open to the public, you may, as close associates, attend.",
    "If by \"beyond prediction\" he meant something that might get them killed, then yes.",
    "Transporting its passengers from station to station, but not to any destination.",
]

state = {
    "line": "",
    "appeared": 0.0,
    "emitted": [],  # (line index, seconds after appearing, source)
    "all": [],  # every emission, for diagnosis
    "t0": 0.0,
    "done": threading.Event(),
}


class Screen(Gtk.ApplicationWindow):
    def __init__(self, app, config, index_holder):
        super().__init__(application=app, title="lintranslator latency probe")
        self.config = config
        self.index_holder = index_holder
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self.fullscreen()

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(0.06, 0.06, 0.08)
        cr.paint()
        line = state["line"]
        if not line:
            return
        # Draw where the pipeline is looking, so the region really contains it.
        region = self.config.capture.region
        x, y, w, h = region.to_pixels(width, height)
        cr.set_source_rgb(0.03, 0.03, 0.05)
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgb(0.93, 0.91, 0.85)
        cr.select_font_face("DejaVu Sans", cairo_font_slant(), cairo_font_weight())
        cr.set_font_size(max(16, h * 0.32))
        if OVERLAP:
            # Our own status line, changing every frame - the worst case: it keeps
            # the read changing, so it can never settle on its own.
            cr.set_source_rgb(0.85, 0.85, 0.88)
            cr.set_font_size(max(11, h * 0.19))
            cr.move_to(x + 8, y + h * 0.24)
            cr.show_text(f"12:34:5{int(time.monotonic()) % 10} · conf 90 · {int(time.monotonic() * 100) % 1000} ms · openrouter")
            cr.set_source_rgb(0.93, 0.91, 0.85)
            cr.set_font_size(max(16, h * 0.30))
        cr.move_to(x + 8, y + h * (0.58 if OVERLAP else 0.45))
        cr.show_text(line[:64])
        cr.move_to(x + 8, y + h * (0.92 if OVERLAP else 0.85))
        cr.show_text(line[64:128])


def cairo_font_slant():
    import cairo

    return cairo.FONT_SLANT_NORMAL


def cairo_font_weight():
    import cairo

    return cairo.FONT_WEIGHT_NORMAL


def pipeline_worker(config):
    pipe = Pipeline(config, on_event=lambda ev: _on_event(ev))
    pipe.warmup()
    pipe.start()
    while not state["done"].is_set():
        pipe.step()
        time.sleep(pipe.sleep_time())
    print(f"\nstats: {pipe.stats.as_dict()}")
    print(f"capture: {pipe.grabber.stats}")
    pipe.close()


def _on_event(event):
    state["all"].append((round(time.monotonic() - state["t0"], 2), event.source))
    if not state["line"]:
        return
    index = state["index"]
    if index is not None and event.source.startswith(LINES[index][:24]):
        state["emitted"].append((index, time.monotonic() - state["appeared"], event.source))


def main() -> int:
    cfg = Config.load()
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0

    app = Gtk.Application(application_id="dev.lintranslator.latency", flags=Gio.ApplicationFlags.NON_UNIQUE)
    state["index"] = None

    def on_activate(_app):
        index = {"value": -1}

        def window():
            win = Screen(app, cfg, index)
            win.present()

            def repaint():
                if state["line"]:
                    win.area.queue_draw()
                return True

            if OVERLAP:
                GLib.timeout_add(300, repaint)

            def advance():
                index["value"] += 1
                if index["value"] >= len(LINES):
                    state["done"].set()
                    _report()
                    app.quit()
                    return False
                state["index"] = index["value"]
                state["line"] = LINES[index["value"]]
                state["appeared"] = time.monotonic()
                print(
                    f"\n[t={time.monotonic() - state['t0']:5.1f}s] showing line "
                    f"{index['value'] + 1}/{len(LINES)}: {state['line'][:48]!r}",
                    flush=True,
                )
                win.area.queue_draw()
                GLib.timeout_add(int(SECONDS_PER_LINE * 1000), advance)
                return False

            GLib.timeout_add(500, advance)
            return False

        window()

    def _report():
        print("\n" + "=" * 78)
        print(f"END TO END: line on screen -> translation ready ({SECONDS_PER_LINE:.0f}s per line)")
        print("\nall emissions the pipeline produced:")
        for when, source in state["all"]:
            print(f"  [+{when:6.2f}s] {source[:96]!r}")
        print("=" * 78)
        for index, line in enumerate(LINES):
            hit = next((dt for i, dt, _ in state["emitted"] if i == index), None)
            label = f"line {index + 1}"
            if hit is None:
                print(f"  {label}: NOT DETECTED")
            else:
                print(f"  {label}: {hit:5.2f}s   {line[:44]!r}")
        print("=" * 78)

    app.connect("activate", on_activate)
    state["t0"] = time.monotonic()
    worker = threading.Thread(target=pipeline_worker, args=(cfg,), daemon=True)
    worker.start()
    GLib.timeout_add(int((len(LINES) * SECONDS_PER_LINE + 25) * 1000), lambda: (app.quit(), False)[1])
    app.run([])
    state["done"].set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
