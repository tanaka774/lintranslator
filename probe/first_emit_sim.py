"""What the panel translates first, and when, if the first frame is polluted.

Takes the frame the pipeline really captured 10 ms after Start was pressed
(`data/probe_first_crop.png`, which contains the picker's own widgets) and runs it
through the real `Pipeline` on a virtual clock, with real tesseract and the
echoing `none` backend. Deterministic - no screen, no network.

Run:  .venv/bin/python probe/first_emit_sim.py
"""
import sys
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from PIL import Image  # noqa: E402

from lintranslator.capture import Frame  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402


class FrozenScreen:
    """The exact frame captured right after the click, returned forever."""

    def __init__(self, image):
        self.image = image
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return Frame(
            image=self.image,
            full_size=self.image.size,
            region=(0, 0, *self.image.size),
            elapsed=0.003,
        )

    def close(self):
        pass

    @property
    def stats(self):
        return {"grabs": self.grabs}


def main():
    cfg = Config.load()
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0

    path = sys.argv[1] if len(sys.argv) > 1 else APP_DIR / "data" / "probe_first_crop.png"
    image = Image.open(path).convert("RGB")
    print(f"frame: {path}")

    emitted = []
    pipe = Pipeline(cfg, on_event=lambda ev: emitted.append(ev))
    pipe.grabber = FrozenScreen(image)
    pipe.warmup()
    pipe.start(0.0)

    t = 0.0
    step = 1.0 / cfg.capture.fps
    first_at = None
    for _ in range(40):
        t += step
        event = pipe.step(t)
        if event is not None and first_at is None:
            first_at = t
        if t >= 10.0:
            break

    print(f"settle_window={cfg.detect.settle_window}s "
          f"refresh_interval={cfg.detect.refresh_interval}s "
          f"fps={cfg.capture.fps}  min_confidence={cfg.ocr.min_confidence}")
    print(f"polls={pipe.stats.polls} ocr_runs={pipe.stats.ocr_runs} emits={len(emitted)}")
    print(f"first emission at t={first_at}s after Start (frame captured at t=0.01s)")
    for ev in emitted:
        print(f"\n  conf={ev.confidence:.1f}  source={ev.source!r}")
        print(f"  target={ev.target!r}")
    pipe.close()


if __name__ == "__main__":
    main()
