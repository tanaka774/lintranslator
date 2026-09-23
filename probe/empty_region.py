"""What happens when the box has no text, or text that makes no sense?

Four regions, each run through the real OCR (tesseract, your `min_confidence`) and
then through the real pipeline, to show what actually reaches the translator:

  1. blank region            - nothing to read at all
  2. background art          - pattern, no text
  3. dim/noisy small glyphs  - text-ish, but OCR is not sure
  4. clean UI gibberish      - nonsense OCR is *confident* about

Run:  .venv-gi/bin/python probe/empty_region.py
"""
import random
import sys

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

from PIL import Image, ImageDraw  # noqa: E402

from lintranslator.capture import Frame  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.ocr import TesseractOcr  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402

CONFIG = "/home/chiba/workspace/lintranslator/config.json"
WIDTH, HEIGHT = 900, 120


def blank() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), (18, 18, 22))


def background_art() -> Image.Image:
    """Patterned, high-contrast, zero text - a menu backdrop or a poster."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (22, 20, 30))
    draw = ImageDraw.Draw(image)
    random.seed(7)
    for x in range(0, WIDTH, 26):
        shade = 30 + (x * 7) % 90
        draw.polygon([(x, 0), (x + 16, 0), (x + 34, HEIGHT), (x + 10, HEIGHT)],
                     fill=(shade, shade // 2, shade // 3))
    for _ in range(120):
        x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
        draw.ellipse([x, y, x + 5, y + 5], fill=(200, 180, 140))
    return image


def dim_glyphs() -> Image.Image:
    """Text, but small and low contrast - the case min_confidence exists for."""
    image = blank()
    ImageDraw.Draw(image).text((20, 50), "wv f] |<a5te QFjnd = . 4/7 ae", fill=(70, 70, 74))
    return image


def ui_gibberish() -> Image.Image:
    """Clean, large, meaningless - OCR will be sure of itself.

    The exact reading below came off a live screen in an earlier phase of this
    project (a text editor's toolbar caught inside the region).
    """
    image = Image.new("RGB", (WIDTH, HEIGHT), (250, 250, 250))
    ImageDraw.Draw(image).text((24, 45), "Ww Vv F] Paste QFind = .", fill=(10, 10, 10))
    return image


class Screen:
    """Serves one image forever (or alternates two, to keep pixels changing)."""

    def __init__(self, images: list[Image.Image]) -> None:
        self.images = images
        self.i = 0

    def grab(self):
        image = self.images[self.i % len(self.images)]
        self.i += 1
        return Frame(image=image, full_size=image.size, region=(0, 0, *image.size), elapsed=0.3)

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.i}


class Translator:
    """Records what the model would have been asked to translate."""

    name = "stub"

    def __init__(self) -> None:
        class _Cache:
            stats = {"entries": 0, "hits": 0, "misses": 0}

        self.cache = _Cache()
        self.asked: list[str] = []
        self.last_was_cached = False

    def translate(self, text: str, force: bool = False):
        from lintranslator.translate import Translation

        self.asked.append(text)
        return Translation(target=f"（訳）{text}", source=text, backend="stub", elapsed=0.4)

    def warmup(self):
        pass

    def close(self):
        pass


def describe(title: str, images: list[Image.Image], engine: TesseractOcr, cfg: Config,
             changing: bool = False) -> None:
    print(f"\n{'=' * 78}\n{title}")
    if changing:
        print("  (the image alternates, so the box changes on every poll - a live background)")
    print("=" * 78)

    read = engine.read(images[0])
    print(f"  OCR alone : {len(read.lines)} line(s), confidence {read.confidence:.1f}, "
          f"{read.dropped} dropped by min_confidence={cfg.ocr.min_confidence:.0f}")
    for line in read.lines:
        print(f"              kept  conf={line.confidence:5.1f}  {line.text[:64]!r}")
    print(f"              -> pipeline would see: {read.text[:64]!r}")

    translator = Translator()
    pipe = Pipeline(cfg, on_event=lambda ev: None)
    pipe.grabber = Screen(images if changing else images[:1])
    pipe.ocr = engine
    pipe.translator = translator
    pipe.warmup = lambda: None
    pipe.start(0.0)

    decisions: dict[str, int] = {}
    now = 0.0
    for _ in range(10):
        now = round(now + 1.0 / cfg.capture.fps, 3)
        pipe.step(now)
        decisions[pipe.last_decision] = decisions.get(pipe.last_decision, 0) + 1

    print(f"  pipeline  : ocr_runs={pipe.stats.ocr_runs} empty_reads={pipe.stats.empty_reads} "
          f"self_reads={pipe.stats.self_reads} translations={pipe.stats.translations}")
    print(f"              decisions: {decisions}")
    print(f"              model asked to translate: {translator.asked if translator.asked else 'nothing'}")
    pipe.close()


def main() -> int:
    cfg = Config.load(CONFIG)
    cfg.translate.backend = "none"  # the stub translator stands in for the model
    engine = TesseractOcr(
        langs=cfg.ocr.langs,
        psm=cfg.ocr.psm,
        upscale=cfg.ocr.upscale,
        autocontrast=cfg.ocr.autocontrast,
        tessdata_dir=cfg.ocr.tessdata_dir,
        min_confidence=cfg.ocr.min_confidence,
    )
    print(f"empty_streak_limit={cfg.detect.empty_streak_limit}  "
          f"settle_window={cfg.detect.settle_window}s  min_confidence={cfg.ocr.min_confidence:.0f}")

    describe("1. blank region (nothing on screen)", [blank()], engine, cfg)
    describe("2. background art (pattern, no text)", [background_art()], engine, cfg)
    describe("3. small dim glyphs (text, but OCR unsure)", [dim_glyphs()], engine, cfg)
    describe("4. clean UI gibberish (nonsense OCR is sure about)", [ui_gibberish()], engine, cfg)
    describe(
        "5. background art that changes every poll",
        [background_art(), background_art().transpose(Image.FLIP_LEFT_RIGHT)],
        engine,
        cfg,
        changing=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
