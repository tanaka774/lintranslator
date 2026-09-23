"""When exactly does a translation fire? A timeline, on your own settings.

Runs the real pipeline on a virtual clock (no screen, no network) so every decision
is visible:

  A. a normal line, revealed in steps, then left on screen
  B. a line that pauses mid-sentence - does the fragment get translated?
  C. a line on a screen that never changes a pixel

The knobs it exercises are the ones in config.json: capture.fps, settle_window,
incomplete_grace, settle_max_wait, refresh_interval, ocr_min_interval.

Run:  .venv-gi/bin/python probe/trigger_timing.py
"""
import sys

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

from PIL import Image, ImageDraw  # noqa: E402

from lintranslator.capture import Frame  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.ocr import OcrLine, OcrResult  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402

CONFIG = "/home/chiba/workspace/lintranslator/config.json"

FRAGMENT = "Herr Gregor is the proverbial poster child of Work"
FULL = "Herr Gregor is the proverbial poster child of Workshop-sponsored Fixers."
STEP_ONE = "Herr Gregor is the proverbial"
STEP_TWO = "Herr Gregor is the proverbial poster child of Work"


class Screen:
    """Changes pixels only when the text changes (unless told to blink).

    `frame_reading` is the text *of the frame that was just grabbed*, so the OCR
    stub reads what the pixels actually show. An earlier version truncated the
    rendered text but returned the full string from OCR, which made the change
    detector and the OCR text disagree - and the timings it produced were of the
    fixture, not the pipeline.
    """

    def __init__(self, readings: list[str], blinking: bool = False) -> None:
        self.readings = readings
        self.blinking = blinking
        self.i = -1
        self.frame_reading = ""

    def grab(self):
        self.i = min(self.i + 1, len(self.readings) - 1)
        self.frame_reading = self.readings[self.i]
        image = Image.new("RGB", (900, 90), (30, 30, 34))
        draw = ImageDraw.Draw(image)
        draw.text((8, 30), self.frame_reading, fill=(235, 235, 235))
        if self.blinking and self.i % 2:
            draw.rectangle([880, 8, 896, 82], fill=(250, 250, 250))
        return Frame(image=image, full_size=image.size, region=(0, 0, 900, 90), elapsed=0.001)

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.i + 1}


class Ocr:
    def __init__(self, screen: Screen) -> None:
        self.screen = screen
        self.calls = 0

    def ensure_ready(self):
        return None

    def read(self, image) -> OcrResult:
        self.calls += 1
        text = self.screen.frame_reading
        return OcrResult(
            lines=[OcrLine(text, 93.0, (0, 0, 880, 30))] if text else [],
            elapsed=0.12,
            engine="stub",
            lang="eng",
        )


class Translator:
    name = "stub"
    last_was_cached = False

    def __init__(self, call_seconds: float = 0.0) -> None:
        self.call_seconds = call_seconds

        class _Cache:
            stats = {"entries": 0, "hits": 0, "misses": 0}

        self.cache = _Cache()

    def translate(self, text: str, force: bool = False):
        from lintranslator.translate import Translation

        return Translation(target=f"（訳）{text[:12]}…", source=text, backend="stub", elapsed=self.call_seconds)

    def warmup(self):
        pass

    def close(self):
        pass


def run(title: str, readings: list[str], *, blinking: bool = False,
        call_seconds: float = 0.0, note: str = "") -> None:
    cfg = Config.load(CONFIG)
    cfg.translate.backend = "none"
    fps = cfg.capture.fps
    step = 1.0 / fps

    screen = Screen(readings, blinking=blinking)
    emits: list[tuple[float, str]] = []
    pipe = Pipeline(cfg, on_event=lambda ev: emits.append((now[0], ev.source)))
    pipe.grabber = screen
    pipe.ocr = Ocr(screen)
    pipe.translator = Translator(call_seconds)
    pipe.warmup = lambda: None
    pipe.start(0.0)

    print(f"\n{title}")
    if note:
        print(f"  ({note})")
    print(f"  settle_window={cfg.detect.settle_window}s  incomplete_grace={cfg.detect.incomplete_grace}s  "
          f"settle_max_wait={cfg.detect.settle_max_wait}s  refresh={cfg.detect.refresh_interval}s  "
          f"poll={step}s")
    print(f"  {'t':>5}  {'decision':<22} {'held text':<44} event")

    now = [0.0]
    last = None
    for _ in range(len(readings) + 20):
        now[0] = round(now[0] + step, 3)
        before = len(emits)
        pipe.step(now[0])
        line = f"  {now[0]:5.1f}  {pipe.last_decision:<22} {(pipe.settler.held or '-')[:44]:<44}"
        if len(emits) > before:
            t, source = emits[-1]
            kind = "COMPLETE" if source == FULL else "fragment"
            line += f" -> TRANSLATED ({kind})"
        if pipe.last_decision != last or len(emits) > before:
            print(line)
        last = pipe.last_decision

    print(f"  result: {len(emits)} translation(s) at "
          + ", ".join(f"t={t:.1f}s" for t, _ in emits))
    pipe.close()


def main() -> int:
    # A reveal in three steps, then the line sits still.
    run(
        "A. normal line: revealed in steps, then left on screen",
        [STEP_ONE, STEP_TWO, FULL] + [FULL] * 12,
        call_seconds=0.6,
        note="the model call itself is 0.6s in this run; a real one is 0.3-1.5s",
    )

    # The game pauses mid-sentence before finishing (the case the grace exists for).
    run(
        "B. the same line, but the game pauses mid-sentence for 4s",
        [STEP_ONE, FRAGMENT] + [FRAGMENT] * 8 + [FULL] * 8,
        call_seconds=0.6,
        note="an unfinished line ('...of Work') is held; how long?",
    )

    # A screen that never changes a pixel: only the timed re-read can confirm it.
    run(
        "C. line appears on a screen that then never changes a pixel",
        [FULL] * 16,
        note="no pixel change after the first frame, so OCR is driven by refresh_interval",
    )

    # A blinking caret: changes every poll, so OCR keeps running.
    run(
        "D. same, but with a blinking advance caret (a live game)",
        [FULL] * 16,
        blinking=True,
        note="every poll looks changed; ocr_min_interval caps the re-reads",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
