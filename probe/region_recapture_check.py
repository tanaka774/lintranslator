"""Does the panel's Region button come back on a *fresh* screenshot?"""
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from lintranslator import capture as capture_mod  # noqa: E402
from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.portal import ScreenshotPortal  # noqa: E402

GRABS = []
FAILURES = []


class CountingGrabber(capture_mod.ScreenGrabber):
    """The real grabber, recording what it was asked for."""

    def grab_full(self):
        started = time.monotonic()
        try:
            full = super().grab_full()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"picker grab failed: {type(exc).__name__}: {exc}")
            raise
        GRABS.append((round((time.monotonic() - started) * 1000), full.size))
        return full


class QuietPanel(panel_mod.TranslatorPanel):
    """The real panel, but presenting it does nothing (no window on screen)."""

    def present(self):
        return None


class RecordingPipelineThread:
    """Stands in for the worker: this probe is about the picker, not the loop."""

    def __init__(self, config, outbox, gate=None):
        self.config = config
        self.gate = gate
        self.region = config.capture.region
        self.requested = []

    def start(self):
        pass

    def stop(self, timeout: float = 1.0):
        pass

    def request_region(self, region):
        self.requested.append(region)
        self.region = region


def concurrent_grab_check(cfg, rounds: int = 4) -> bool:
    """Grab while another thread polls the same portal, the way watching does."""
    stop = threading.Event()

    def poll():
        grabber = capture_mod.ScreenGrabber(cfg.capture.region)
        while not stop.is_set():
            try:
                grabber.grab_full()
            except Exception as exc:  # noqa: BLE001
                FAILURES.append(f"pipeline grab failed: {type(exc).__name__}: {exc}")
            stop.wait(0.05)
        grabber.close()

    reader = threading.Thread(target=poll, daemon=True)
    reader.start()
    time.sleep(0.3)  # let the reader thread get going, so the grabs really overlap
    for _ in range(rounds):
        try:
            grabber = capture_mod.ScreenGrabber(cfg.capture.region)
            try:
                grabber.grab_full()
            finally:
                grabber.close()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"picker grab failed: {type(exc).__name__}: {exc}")
    stop.set()
    reader.join(timeout=2.0)
    return not FAILURES


def main() -> int:
    panel_mod.PipelineThread = RecordingPipelineThread
    panel_mod.TranslatorPanel = QuietPanel

    cfg = Config.load()
    cfg.path = Path("/tmp/lintranslator_recapture_probe.json")  # never the real config

    portal = ScreenshotPortal()
    png, size, elapsed = portal.grab()
    portal.close()
    print(f"screen {size[0]}x{size[1]} (portal {elapsed * 1000:.0f} ms)")

    print("\nconcurrent grabs while another thread polls the portal:")
    ok_concurrent = concurrent_grab_check(cfg)
    print(f"  failures: {FAILURES or 'none'}")

    # only from here on is the picker's grabber counted, so GRABS holds just the Region grab
    capture_mod.ScreenGrabber = CountingGrabber

    app = Gtk.Application(
        application_id="dev.lintranslator.recapturecheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    outcome = {}

    def on_activate(_app):
        from lintranslator.picker import RegionPicker

        picker = RegionPicker(app, cfg, screenshot_png=png)
        picker.present = lambda: None  # nothing lands on the real screen
        picker._panel.hotkey_enabled = False

        # the state when Region is pressed: watching, and this window already off screen
        picker._on_start()
        picker.set_visible(False)
        box_before = picker.sel
        first_shot = picker.screen_image.tobytes()

        started = time.monotonic()
        picker._panel.region_btn.emit("clicked")  # the real button, not restore()
        elapsed_ms = round((time.monotonic() - started) * 1000)

        outcome["grabs"] = len(GRABS)
        outcome["box_kept"] = picker.sel == box_before
        outcome["canvas_replaced"] = picker.screen_image.tobytes() != first_shot
        outcome["screen_size"] = picker.screen_size
        outcome["visible"] = picker.get_visible()
        outcome["status"] = picker.status_label.get_text()
        outcome["elapsed_ms"] = elapsed_ms

        print("\npress Region while watching (window already off screen):")
        print(f"  full-screen grabs issued : {outcome['grabs']}")
        print(f"  last grab                : {GRABS[-1] if GRABS else None} (ms, size)")
        print(f"  canvas replaced          : {outcome['canvas_replaced']}")
        print(f"  dragged box kept         : {outcome['box_kept']} ({picker.sel})")
        print(f"  screen size              : {outcome['screen_size']}")
        print(f"  picker visible again     : {outcome['visible']}")
        print(f"  click -> fresh shot      : {outcome['elapsed_ms']} ms (no 1 s wait)")
        print(f"  status                   : {outcome['status']!r}")

        picker._shutdown_panel()
        app.quit()

    app.connect("activate", on_activate)
    app.run([])

    ok = (
        ok_concurrent
        and outcome.get("grabs") == 1
        and outcome.get("canvas_replaced")
        and outcome.get("box_kept")
        and outcome.get("visible")
        and outcome.get("screen_size") == size
        and outcome.get("elapsed_ms", 10_000) < 1000
        and not FAILURES
    )
    print("\n" + "=" * 70)
    print(f"concurrent portal grabs ok : {ok_concurrent and not FAILURES}")
    print(f"failed grabs               : {FAILURES or 'none'}")
    print(f"Region re-captured         : {outcome.get('grabs')} grab(s), box kept")
    print("PASS" if ok else "FAIL: Region did not reopen on a fresh capture")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
