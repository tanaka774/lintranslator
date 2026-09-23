"""What does the pipeline see in the seconds after "Watch live" is pressed?

The panel's worker starts capturing the instant `_on_start` runs, while the
picker window (the one that was just clicked) and the freshly-presented panel are
both still on screen. This probe drives that exact code path - the real
`RegionPicker._on_start`, the real `PipelineThread`, the real portal grabber and
tesseract - and records, per grab:

  * wall time since the click
  * whether the picker and panel windows were mapped at that instant
  * what tesseract reads from the crop
  * the full frame, so an early crop can be compared with a late one

Then it hides the app's own windows, one at a time, and keeps capturing. If the
early reads differ from the late ones, the app was reading itself.

Run:  .venv/bin/python probe/handoff_timing.py
"""
import sys
import time
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.occlusion import GUARD  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402
from lintranslator.portal import ScreenshotPortal  # noqa: E402

RUN_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 9.0
WATCH_AT = 1.5
HIDE_PICKER_AT = WATCH_AT + 4.0
HIDE_PANEL_AT = WATCH_AT + 6.5
DATA = APP_DIR / "data"

STATE = {
    "t0": None,
    "grabs": [],  # {t, wall, elapsed, crop, full}
    "visibility": [],  # (t, picker_vis, picker_mapped, panel_vis, panel_mapped)
    "started": None,
}


def mono() -> float:
    return time.monotonic()


class TracingPipeline(Pipeline):
    """Real pipeline; records when each grab happened and what it captured."""

    def __init__(self, *args, **kwargs):
        notify = kwargs.get("on_gate")

        def traced_gate(reason):
            STATE.setdefault("gates", []).append((mono(), reason))
            if notify:
                notify(reason)

        kwargs["on_gate"] = traced_gate
        super().__init__(*args, **kwargs)
        grabber = self.grabber
        original_full = grabber.grab_full

        def traced_full():
            t = mono()
            frame = original_full()
            done = mono()
            STATE["grabs"].append(
                {
                    "t": t,
                    "wall": done - t,
                    "elapsed": frame.elapsed,
                    "full": frame.image.copy(),
                }
            )
            return frame

        grabber.grab_full = traced_full

    def start(self, when=None):
        STATE["started"] = mono() if when is None else when
        return super().start(when)


panel_mod.Pipeline = TracingPipeline


class Probe:
    def __init__(self, app, config, screenshot_png):
        from lintranslator.picker import RegionPicker

        self.config = config
        self.app = app
        self.picker = RegionPicker(app, config, screenshot_png=screenshot_png)
        self.picker.present()
        self._sampler = GLib.timeout_add(50, self._sample)
        GLib.timeout_add(int(WATCH_AT * 1000), self._press_watch)
        GLib.timeout_add(int(HIDE_PICKER_AT * 1000), self._hide_picker)
        GLib.timeout_add(int(HIDE_PANEL_AT * 1000), self._hide_panel)
        GLib.timeout_add(int((WATCH_AT + RUN_SECONDS) * 1000), self._finish)

    # -- main-thread actions ---------------------------------------------- #
    def _sample(self):
        p = self.picker
        panel = getattr(p, "_panel", None)
        STATE["visibility"].append(
            (
                mono(),
                GUARD.reason(),
                p.get_visible(),
                p.get_mapped(),
                panel.get_visible() if panel else None,
                panel.get_mapped() if panel else None,
            )
        )
        return True

    def _press_watch(self):
        """Exactly what the 'Watch live' button runs."""
        print("\n--- pressing Watch live (picker._on_start) ---", flush=True)
        STATE["t0"] = mono()
        self.picker._on_start()
        print(
            f"    _on_start returned after {(mono() - STATE['t0']) * 1000:.0f} ms; "
            f"worker running: {self.picker._panel.worker is not None}",
            flush=True,
        )
        return False

    def _hide_picker(self):
        print("--- hiding the picker (what the user must do by hand) ---", flush=True)
        self.picker.set_visible(False)
        return False

    def _hide_panel(self):
        print("--- hiding the panel too (nothing of ours left on screen) ---", flush=True)
        panel = getattr(self.picker, "_panel", None)
        if panel:
            panel.set_visible(False)
        return False

    def _finish(self):
        self._report()
        self.picker._shutdown_panel()
        self.app.quit()
        return False

    # -- helpers ----------------------------------------------------------- #
    def _state_at(self, t):
        """Last sampled window state at or before `t`."""
        last = (None, None, None, None, None, None)
        for sample in STATE["visibility"]:
            if sample[0] <= t:
                last = sample
            else:
                break
        return last

    # -- report ------------------------------------------------------------ #
    def _report(self):
        from lintranslator.ocr import TesseractOcr

        t0 = STATE["t0"] or 0.0
        cfg = self.config
        region = cfg.capture.region

        print("\n" + "=" * 96)
        print("GATE (capture pause) transitions")
        print("=" * 96)
        for t, reason in STATE.get("gates", []):
            print(f"  t={t - t0:5.2f}s  {'PAUSED: ' + reason if reason else 'resumed — reading is allowed'}")
        grabs = STATE["grabs"]
        if grabs:
            first = grabs[0]["t"]
            print(f"  first grab starts at t={first - t0:5.2f}s")

        print("\n" + "=" * 96)
        print("GRABS AFTER THE CLICK  (t = seconds after pressing Watch live)")
        print("=" * 96)
        ocr = TesseractOcr(
            langs=cfg.ocr.langs,
            psm=cfg.ocr.psm,
            upscale=cfg.ocr.upscale,
            autocontrast=cfg.ocr.autocontrast,
            tessdata_dir=cfg.ocr.tessdata_dir,
            min_confidence=0.0,
        )
        if not STATE["grabs"]:
            print("no grabs recorded - the pipeline never captured anything")
            return
        x, y, w, h = region.to_pixels(*STATE["grabs"][0]["full"].size)
        crops = []
        for i, g in enumerate(STATE["grabs"]):
            t = g["t"] - t0
            _, gate, pv, pm, nv, nm = self._state_at(g["t"])
            crop = g["full"].crop((x, y, x + w, y + h))
            crops.append((t, crop))
            result = ocr.read(crop)
            text = (result.text or "").replace("\n", " / ")
            print(
                f"[{i:2d}] t={t:5.2f}s picker(vis={pv},mapped={pm}) "
                f"panel(vis={nv},mapped={nm}) guard={gate!r} "
                f"portal={g['elapsed'] * 1000:5.0f}ms "
                f"conf={result.confidence:5.1f} {text[:64]!r}"
            )
            if i == 0:
                crop.save(f"{DATA}/probe_first_crop.png")
                g["full"].save(f"{DATA}/probe_first_full.png")

        # Quantitative: how much of the region changed once our windows went away?
        if len(crops) > 2:
            from lintranslator.detect import changed_fraction

            print("\nregion pixels that differ from the LAST grab (windows hidden):")
            last = crops[-1][1]
            for t, crop in crops:
                frac = changed_fraction(crop.convert("L").tobytes(), last.convert("L").tobytes())
                print(f"   t={t:5.2f}s  {frac * 100:5.1f}% of region differs")

        if len(STATE["grabs"]) > 1:
            gaps = [
                STATE["grabs"][i + 1]["t"] - STATE["grabs"][i]["t"]
                for i in range(len(STATE["grabs"]) - 1)
            ]
            ordered = sorted(gaps)
            print(
                f"\ngrab-to-grab: min={ordered[0] * 1000:.0f}ms "
                f"median={ordered[len(ordered) // 2] * 1000:.0f}ms max={ordered[-1] * 1000:.0f}ms "
                f"(configured fps={cfg.capture.fps} -> {1000 / cfg.capture.fps:.0f}ms target)"
            )
        portals = sorted(g["elapsed"] for g in STATE["grabs"])
        print(
            f"portal round-trip: min={portals[0] * 1000:.0f}ms "
            f"median={portals[len(portals) // 2] * 1000:.0f}ms max={portals[-1] * 1000:.0f}ms"
        )
        print(f"region pixels: x={x} y={y} w={w} h={h}  crop size={crops[0][1].size if crops else None}")


def main():
    cfg = Config.load()
    cfg.translate.backend = "none"  # no network calls in a timing probe
    cfg.capture.fps = 2.0
    # `_on_start` calls config.save(); never let a probe write the user's config.
    from pathlib import Path

    cfg.path = Path("/tmp/lintranslator_probe_config.json")

    print("grabbing one real screenshot for the picker to display...", flush=True)
    portal = ScreenshotPortal()
    png, size, elapsed = portal.grab()
    portal.close()
    print(f"  {size} in {elapsed * 1000:.0f} ms", flush=True)

    app = Gtk.Application(
        application_id="dev.lintranslator.probe", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app):
        Probe(app, cfg, png)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
