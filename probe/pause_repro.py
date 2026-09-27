"""Check whether a typewriter pause makes the pipeline emit a half-finished sentence."""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from lintranslator.config import Config
from lintranslator.pipeline import Pipeline

FULL = "You did not? Hm, odd. The inspector is the proverbial poster child of company-sponsored contractors."
STEPS = [
    "zz ... You did not? Hm, odd. The inspector is the proverbial poster child of company",
    FULL,
]
# polls, 0.5 s each
PAUSE = int(sys.argv[1]) if len(sys.argv) > 1 else 6


class _RevealingScreen:
    """Screen whose text grows through (text, polls) steps."""

    def __init__(self, timeline: list[tuple[str, int]]) -> None:
        self.timeline = timeline
        self.i = -1
        self.cursor = 0

    def grab(self):
        from PIL import Image, ImageDraw

        from lintranslator.capture import Frame

        self.i += 1
        idx = self.cursor
        img = Image.new("RGB", (200, 40), (40, 40, 40))
        if self.i and self.i % 2:
            # blinking cursor keeps pixels changing, so OCR keeps running while the text sits still
            ImageDraw.Draw(img).rectangle([180, 8, 188, 32], fill=(255, 255, 255))
        return Frame(image=img, full_size=img.size, region=(0, 0, 200, 40), elapsed=0.001)

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.i + 1}


class _RevealingOcr:
    """Returns the text for the current poll, driven by an explicit poll count."""

    def __init__(self, timeline: list[tuple[str, int]]) -> None:
        self.timeline = timeline
        self.poll = -1

    def ensure_ready(self):
        return None

    def read(self, image):
        from lintranslator.ocr import OcrLine, OcrResult

        self.poll += 1
        text, seen = "", 0
        for step_text, polls in self.timeline:
            if self.poll < seen + polls:
                text = step_text
                break
            seen += polls
        else:
            text = self.timeline[-1][0]
        lines = [OcrLine(text, 92.0, (0, 0, 10, 10))] if text else []
        return OcrResult(lines=lines, elapsed=0.001, engine="s", lang="eng")


def run(pause_polls: int) -> list[str]:
    timeline = [(STEPS[0], pause_polls), (STEPS[1], 20)]
    cfg = Config()
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0
    events: list[str] = []
    pipe = Pipeline(cfg, on_event=lambda ev: events.append(ev.source))
    pipe.grabber = _RevealingScreen(timeline)
    pipe.ocr = _RevealingOcr(timeline)
    from tests.test_pipeline import _StubTranslator

    pipe.translator = _StubTranslator()
    pipe.warmup = lambda: None
    pipe.start(0.0)
    now = 0.0
    for _ in range(pause_polls + 30):
        now += 0.5
        pipe.step(now)
    return events


print(f"settle_window={Config().detect.settle_window}s  pause={PAUSE * 0.5:.1f}s\n")
emitted = run(PAUSE)
print(f"emissions for a {PAUSE * 0.5:.1f}s pause mid-reveal: {len(emitted)}")
for e in emitted:
    kind = "FRAGMENT (prefix of the full line)" if e != FULL else "complete line"
    print(f"   [{kind}] {e!r}")
