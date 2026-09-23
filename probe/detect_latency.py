"""Does the self-capture guard cost detection speed, or lose lines?

Deterministic and screen-free: a scripted screen that changes on every poll (a
blinking caret, like a live game), carrying one dialogue line at a time, and -
in the overlap scenario - a line of tl-kun's own UI inside the same read.

Three phases over the identical script, so the comparison is exact:

  1. `before`    - no self-text handling at all (the original pipeline).
  2. `drop read` - the first version of the guard: if any of our text is in the
                   read, the whole read is discarded.
  3. `per line`  - the current guard: only the lines that are ours are dropped.

Reported per line: was it translated, and how long after it appeared. Phase 2 is
the one that produced "it stopped detecting": the panel's status line changes
constantly, so every read looked like ours and the dialogue never settled.

Run:  .venv-gi/bin/python probe/detect_latency.py
"""
import sys
from dataclasses import dataclass

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

from PIL import Image, ImageDraw  # noqa: E402

from tlkun import pipeline as pipeline_mod  # noqa: E402
from tlkun.capture import Frame  # noqa: E402
from tlkun.config import Config  # noqa: E402
from tlkun.ocr import OcrLine, OcrResult  # noqa: E402
from tlkun.pipeline import Pipeline  # noqa: E402
from tlkun.selftext import looks_like_own_ui  # noqa: E402

# The panel's status line, changing every poll - the worst realistic intruder,
# because each change makes the frame change and keeps OCR running.
PANEL_LINE = "12:34:56 · conf 90 · {n} ms · openrouter · +2.1s"

# Realistic Limbus-style lines, complete sentences so the normal settle window
# applies (rather than the unfinished-text grace).
SCRIPT = [
    "Although the trial won't be open to the public, you may, as close associates, attend.",
    "Here, however... everyone seems to be mired... in ennui.",
    "If by \"beyond prediction\" he meant something that might get them killed, then yes.",
    "Transporting its passengers from station to station, but not to any destination.",
]
POLLS_PER_LINE = 10
FPS = 2.0


class ScriptedScreen:
    """Every grab differs, so change detection always asks for OCR."""

    def __init__(self) -> None:
        self.i = -1

    def grab(self):
        self.i += 1
        image = Image.new("RGB", (400, 60), (25, 25, 30))
        draw = ImageDraw.Draw(image)
        draw.rectangle([10, 10, 380, 40], fill=(40, 40, 44))
        draw.text((14, 20), "dialogue", fill=(235, 235, 235))
        # A blinking advance caret: a large, high-contrast change on every other
        # poll, which is what keeps a live game's frame "changed" (a subtle tint
        # shift is below the detector's noise floor, and the first version of this
        # probe measured almost nothing because of it).
        if self.i % 2:
            draw.rectangle([384, 12, 396, 38], fill=(250, 250, 250))
        return Frame(
            image=image, full_size=image.size, region=(0, 0, 400, 60), elapsed=0.001
        )

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.i + 1}


class ScriptedOcr:
    """Returns the current line, plus our own UI when `overlap` is on."""

    def __init__(self, overlap: bool) -> None:
        self.overlap = overlap
        self.calls = 0
        self.last_text = ""

    def ensure_ready(self):
        return None

    def read(self, image) -> OcrResult:
        line = SCRIPT[min(self.calls // POLLS_PER_LINE, len(SCRIPT) - 1)]
        self.calls += 1
        texts = [line]
        if self.overlap:
            texts.append(PANEL_LINE.format(n=800 + self.calls))
        lines = [OcrLine(t, 92.0, (0, i * 14, 400, 14)) for i, t in enumerate(texts)]
        result = OcrResult(lines=lines, elapsed=0.001, engine="stub", lang="eng")
        self.last_text = result.text
        return result


@dataclass
class StubTranslator:
    name = "stub"

    def __post_init__(self) -> None:
        self.seen: list[str] = []

        class _Cache:
            stats = {"entries": 0, "hits": 0, "misses": 0}

        self.cache = _Cache()
        self.last_was_cached = False

    def translate(self, text: str, force: bool = False):
        from tlkun.translate import Translation

        self.seen.append(text)
        return Translation(target=f"[ja] {text}", source=text, backend="stub", elapsed=0.001)

    def warmup(self):
        pass

    def close(self):
        pass


def run(phase: str, overlap: bool) -> tuple[list[str], list[float], int]:
    """Run the script under one guard configuration."""
    cfg = Config()
    cfg.translate.backend = "none"
    cfg.capture.fps = FPS
    cfg.detect.settle_window = 1.2
    cfg.detect.settle_max_wait = 8.0
    cfg.detect.incomplete_grace = 4.5

    ocr = ScriptedOcr(overlap=overlap)
    emitted: list[tuple[float, str]] = []

    pipe = Pipeline(cfg, on_event=lambda ev: emitted.append((now, ev.source)))
    pipe.grabber = ScriptedScreen()
    pipe.ocr = ocr
    pipe.translator = StubTranslator()
    pipe.warmup = lambda: None

    original_guard = pipeline_mod.looks_like_own_ui
    if phase == "before":
        # No self-text handling existed: nothing is ever recognised as ours.
        pipeline_mod.looks_like_own_ui = lambda text: False
    elif phase == "drop read":
        # The first guard version checked the *joined* read and discarded all of it.
        state = {"joined": ""}

        def whole_read(text: str) -> bool:
            return original_guard(state["joined"])

        pipeline_mod.looks_like_own_ui = whole_read
        original_read = ocr.read

        def read_with_state(image):
            result = original_read(image)
            state["joined"] = result.text
            return result

        ocr.read = read_with_state

    pipe.start(0.0)
    now = 0.0
    for _ in range(len(SCRIPT) * POLLS_PER_LINE + 12):
        now += 1.0 / FPS
        pipe.step(now)
    pipeline_mod.looks_like_own_ui = original_guard

    latency: list[float] = []
    sources: list[str] = []
    for index, line in enumerate(SCRIPT):
        appeared = index * POLLS_PER_LINE / FPS
        hit = next((t for t, src in emitted if line[:24] in src), None)
        if hit is None:
            latency.append(float("nan"))
            sources.append("")
        else:
            latency.append(hit - appeared)
            sources.append(next(src for t, src in emitted if line[:24] in src))
    misses = sum(1 for value in latency if value != value)  # NaN
    pipe.close()
    return sources, latency, misses


def main() -> int:
    print(f"{len(SCRIPT)} lines, {POLLS_PER_LINE} polls each at {FPS:g} fps "
          f"({POLLS_PER_LINE / FPS:.1f}s per line)\n")
    for overlap in (False, True):
        title = "clean box" if not overlap else "panel clipping the box"
        print("=" * 78)
        print(f"{title}")
        print("=" * 78)
        for phase in ("before", "drop read", "per line"):
            sources, latency, misses = run(phase, overlap)
            times = " ".join(
                "  --  " if value != value else f"{value:5.2f}s" for value in latency
            )
            print(f"  {phase:<10} detected {len(SCRIPT) - misses}/{len(SCRIPT)}   "
                  f"latency per line: {times}")
            if overlap and phase == "before":
                print(f"             first emission was: {sources[0][:60]!r}")
        print()
    print("latency = seconds from the line appearing to the translation being ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
