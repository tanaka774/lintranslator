"""OCR via tesseract, tuned for game UI text.

Measured on a real Limbus Company dialogue box (1501x852 source):

    raw full screenshot, no preprocessing   -> garbage
    cropped + 3x upscale + autocontrast     -> exact, ~105 ms

So preprocessing is not optional, it is the difference between working and not.
PP-OCR (rapidocr) was 4-6x slower here and dropped word spaces, which harms
translation quality; tesseract with `tessdata_fast` won on both axes.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from . import paths

# tessdata_fast keeps tesseract fast; the slower `tessdata_best` is not needed
# for clean UI fonts.
#
# Pinned to a revision, and verified against a digest. This used to fetch from
# the branch tip with no check, which makes the download an auto-updating binary
# that tesseract's C++ model loader then parses - a supply-chain hole with no
# user-visible symptom when it is exploited. The digests below are of the files
# at `TESSDATA_REVISION`.
TESSDATA_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_URL = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
    f"{TESSDATA_REVISION}/{{lang}}.traineddata"
)

# Languages this app will fetch by itself. A language that is not listed is not
# refused forever - it is refused *silently*, which is the point: install it from
# the distro, or opt in with `ocr.allow_unverified_tessdata`.
TESSDATA_SHA256 = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "jpn": "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d",
    "kor": "6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2",
    "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    "chi_tra": "529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b",
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "deu": "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d",
    "fra": "ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca",
    "spa": "6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464",
    "por": "c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb",
    "ita": "b8f89e1e785118dac4d51ae042c029a64edb5c3ee42ef73027a6d412748d8827",
    "pol": "c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2",
    "tur": "7393381111e1152420fc4092cb44eef4237580d21b92bf30d7d221aad192c6b7",
    "vie": "79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1",
    "tha": "294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c",
    "ara": "e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729",
}

# A tesseract language name, and therefore also the filename it is loaded from.
LANG_NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")

# Searched in order; the first one containing every required language wins.
SYSTEM_TESSDATA_CANDIDATES = (
    "/usr/share/tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/tessdata",
    "/usr/local/share/tessdata",
    "/opt/homebrew/share/tessdata",
)


class OcrError(RuntimeError):
    """OCR could not run: missing binary or missing language data."""


# A trailing single-character token is usually the game's blinking advance
# cursor, not text. Measured live it appeared as " l", " +", " O", " é", " 4" and
# " |" on the same unchanged line, which polluted the translation and made
# consecutive reads look like different lines.
#
# It is NOT safe to strip any lone trailing character: "l" and "O" are letters,
# but so are real words ("... a", "... I") and real sentence endings (".", "?",
# "!", quotes with a space before them). Stripping those corrupted lines during
# live testing - "needed maintenance." became "needed maintena", and a line lost
# its closing quote. Only characters that cannot end an English sentence are
# treated as the caret.
_SAFE_TRAILING = frozenset('."\'!?…,;:。！？、」』)]}')
# Letters and digits the caret was actually misread as, from live OCR. Restricted
# to this set so real one-letter words ("I") and trailing numbers ("1 2 3") are
# left intact. A lone "|", "+" or "~" is punctuation that cannot end a sentence,
# so it is always treated as the caret. "f" and "é" are here because both were
# observed as the caret's misreading; neither occurs as a standalone English word.
_CARET_GLYPHS = frozenset("lO0147f|+~_/\\^`*éè")
_TRAILING_CURSOR = re.compile(r"\s+(\S)$")
# The caret is drawn after the sentence is finished, so OCR often returns it
# AFTER the terminal punctuation ("...the entire battlefield. f", "...formation...
# é"). Anchoring the caret check at the end of the string alone therefore missed
# exactly the finished sentences it matters for, and the stray glyph was sent to
# the translator. This matches the punctuation, the caret, and nothing else -
# a sentence-final token of two or more characters is untouched.
_CARET_AFTER_STOP = re.compile(r"([.!?…])\s+(\S)$")


def _is_caret_token(token: str) -> bool:
    """Whether a lone character is the caret rather than a real word ending.

    A standalone letter or digit counts only if it is one the caret was actually
    misread as; anything else is assumed to be text. That keeps "Was it you? I"
    and "1 2 3" intact while still catching "battlefield. f".
    """
    if token.isalnum():
        return token in _CARET_GLYPHS
    return True  # bare punctuation as a token is never a sentence ending


def strip_trailing_cursor(text: str) -> str:
    """Drop a lone trailing character left behind by the advance caret.

    Handles both places the caret shows up: as the whole trailing token
    ("first line. l"), and after the terminal punctuation it is drawn next to
    ("the battlefield. f"). Only a single character is ever removed, and only
    when it looks like the caret, so "needed maintenance." keeps its period,
    "Wait for me" keeps its "me" and "Was it you? I" keeps its pronoun.
    """
    after_stop = _CARET_AFTER_STOP.search(text)
    if after_stop and _is_caret_token(after_stop.group(2)):
        return _CARET_AFTER_STOP.sub(r"\1", text)
    if text[-1:] in _SAFE_TRAILING:
        return text
    match = _TRAILING_CURSOR.search(text)
    if match is None or not _is_caret_token(match.group(1)):
        return text
    return text[: match.start()]


@dataclass
class OcrLine:
    text: str
    confidence: float
    box: tuple[int, int, int, int]  # x, y, w, h within the cropped region

    @property
    def left(self) -> int:
        return self.box[0]

    @property
    def top(self) -> int:
        return self.box[1]


@dataclass
class OcrResult:
    lines: list[OcrLine]
    elapsed: float
    engine: str
    lang: str
    dropped: int = 0  # tokens rejected for low confidence

    def without_lines(self, predicate) -> "OcrResult":
        """A copy with the lines matching `predicate` removed.

        Per line, not per read: when one of our windows clips the edge of the box
        the read contains both its text and the game's, and dropping the whole
        read loses the dialogue line underneath. Only the intruding lines go.
        """
        kept = [line for line in self.lines if not predicate(line.text)]
        removed = len(self.lines) - len(kept)
        if not removed:
            return self
        return OcrResult(
            lines=kept,
            elapsed=self.elapsed,
            engine=self.engine,
            lang=self.lang,
            dropped=self.dropped + removed,
        )

    @property
    def text(self) -> str:
        """Lines joined into one passage for translation.

        Limbus wraps dialogue mid-sentence, so tesseract returns line 1 and line 2
        of a single sentence. Joining with spaces keeps that sentence intact,
        which is what the translator needs.
        """
        joined = " ".join(line.text for line in self.lines)
        return strip_trailing_cursor(" ".join(joined.split()))

    @property
    def display_text(self) -> str:
        """The passage with visual line breaks preserved, for display.

        `text` is correct for translation but reads as a wall of text in a UI,
        and the break positions are information the OCR already recovered.
        """
        return "\n".join(line.text for line in self.lines if line.text)

    @property
    def visual_rows(self) -> int:
        """Number of distinct text rows, i.e. how many lines were wrapped."""
        return len(self.lines)

    @property
    def confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)

    def __bool__(self) -> bool:
        return bool(self.lines)


def preprocess(
    image: Image.Image,
    *,
    upscale: float = 3.0,
    autocontrast: bool = True,
    invert: bool = False,
) -> Image.Image:
    """Grayscale, upscale, stretch contrast - the recipe that makes OCR work."""
    gray = image.convert("L")
    if upscale and upscale != 1.0:
        gray = gray.resize(
            (max(1, round(gray.width * upscale)), max(1, round(gray.height * upscale))),
            Image.Resampling.LANCZOS,
        )
    if invert:
        gray = ImageOps.invert(gray)
    if autocontrast:
        gray = ImageOps.autocontrast(gray)
    return gray


def split_langs(langs: str) -> list[str]:
    """Split `eng+jpn` / `eng jpn` into names, without validating them.

    The UI wants to show a bad value rather than have it silently replaced, so
    splitting and checking are separate steps.
    """
    return [x for x in langs.replace("+", " ").split() if x]


def bad_langs(langs: str) -> list[str]:
    """The names in `langs` that are not plain tesseract language names."""
    return [name for name in split_langs(langs) if not LANG_NAME_RE.match(name)]


def parse_langs(langs: str) -> list[str]:
    """Split and validate, refusing anything that is not a language name.

    `ocr.langs` is a config value, and it ends up in two places that matter: as
    tesseract's `-l` argument, and as `<name>.traineddata` under the tessdata
    directory. A value like `../../evil` would escape that directory, and
    tesseract accepts a path in `-l` too, so every caller goes through here
    rather than splitting the string itself.
    """
    names = split_langs(langs)
    bad = [name for name in names if not LANG_NAME_RE.match(name)]
    if bad:
        joined = ", ".join(repr(name) for name in bad)
        raise OcrError(
            f"invalid tesseract language name(s): {joined}\n"
            "  a language name is lowercase letters, digits and `_` - "
            "e.g. eng, jpn, chi_sim"
        )
    return names


def download_tessdata(
    langs: list[str], dest: Path | None = None, *, allow_unverified: bool = False
) -> Path:
    """Fetch `tessdata_fast` language files into the app data dir (no root).

    Every file is checked against the digest for the pinned revision before it is
    written. A language with no pinned digest is only downloaded when
    `allow_unverified` is set, because the alternative - fetching whatever the
    URL serves today and handing it to tesseract - is the thing this avoids.
    """
    dest = dest or (paths.DATA_DIR / "tessdata")
    dest.mkdir(parents=True, exist_ok=True)
    for lang in langs:
        if not LANG_NAME_RE.match(lang):
            raise OcrError(
                f"refusing to download a language named {lang!r}: a tesseract "
                "language name is lowercase letters, digits and `_` "
                "(e.g. eng, chi_sim)"
            )
        target = dest / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 0:
            continue
        expected = TESSDATA_SHA256.get(lang)
        if expected is None and not allow_unverified:
            raise OcrError(
                f"no pinned checksum for {lang}.traineddata, so it will not be "
                "downloaded automatically.\n"
                f"  install it system-wide (tesseract-data-{lang} / "
                f"tesseract-ocr-{lang}), drop the file into {dest}/ yourself,\n"
                "  or set ocr.allow_unverified_tessdata: true to download it "
                "without verification."
            )
        url = TESSDATA_URL.format(lang=lang)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
                payload = resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OcrError(
                f"could not download {lang}.traineddata from {url}: {exc}\n"
                f"Download it manually into {dest}/, or install it system-wide."
            ) from exc
        if len(payload) < 1024:
            raise OcrError(f"downloaded {lang}.traineddata looks truncated")
        digest = hashlib.sha256(payload).hexdigest()
        if expected is not None and digest != expected:
            raise OcrError(
                f"{lang}.traineddata does not match the pinned checksum for "
                f"tessdata_fast {TESSDATA_REVISION[:12]}:\n"
                f"  expected {expected}\n"
                f"  got      {digest}\n"
                "  refusing to install it; nothing was written."
            )
        # Write through a temporary name: a half-written file that exists is
        # worse than no file, because the next run treats it as ready.
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
    return dest


def find_tessdata(
    langs: list[str], preferred: str | None = None, *, allow_unverified: bool = False
) -> Path:
    """Locate a tessdata dir containing all `langs`, downloading if necessary."""
    for lang in langs:
        if not LANG_NAME_RE.match(lang):
            raise OcrError(
                f"invalid tesseract language name {lang!r}: a language name is "
                "lowercase letters, digits and `_` (e.g. eng, jpn, chi_sim)"
            )

    candidates: list[Path] = []
    if preferred:
        candidates.append(Path(preferred))
    candidates.append(paths.DATA_DIR / "tessdata")
    # An install from before the move out of the source tree. Read, not written:
    # re-downloading 4 MB of eng/JPN data because a path changed would be silly.
    candidates.append(paths.LEGACY_DATA_DIR / "tessdata")
    candidates.extend(Path(p) for p in SYSTEM_TESSDATA_CANDIDATES)

    for path in candidates:
        if path.is_dir() and all((path / f"{lang}.traineddata").exists() for lang in langs):
            return path

    # Nothing usable: fetch into the app data dir rather than failing outright.
    return download_tessdata(langs, allow_unverified=allow_unverified)


def tesseract_version() -> str | None:
    try:
        out = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.splitlines()[0] if out.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


class TesseractOcr:
    """Thin, cached wrapper around pytesseract's per-token output."""

    def __init__(
        self,
        *,
        langs: str = "eng",
        psm: int = 6,
        upscale: float = 3.0,
        autocontrast: bool = True,
        tessdata_dir: str | None = None,
        min_confidence: float = 40.0,
        invert: bool = False,
        allow_unverified_tessdata: bool = False,
    ) -> None:
        self.langs = langs
        self.psm = psm
        self.upscale = upscale
        self.autocontrast = autocontrast
        self.min_confidence = min_confidence
        self.invert = invert
        self.allow_unverified_tessdata = allow_unverified_tessdata
        self._explicit_dir = tessdata_dir
        self._tessdata: Path | None = None

    @property
    def lang_list(self) -> list[str]:
        """The configured languages, validated.

        Raises `OcrError` on a name that is not a plain language name: the value
        reaches both tesseract's `-l` argument and a filename under the tessdata
        directory, so `ocr.langs` is not free text.
        """
        return parse_langs(self.langs)

    def ensure_ready(self) -> Path:
        """Validate the binary and language data, downloading data if needed."""
        import pytesseract  # noqa: F401  (import here so the error is actionable)

        if tesseract_version() is None:
            raise OcrError(
                "the `tesseract` binary was not found on PATH.\n"
                "  Arch/CachyOS: sudo pacman -S tesseract\n"
                "  Debian/Ubuntu: sudo apt install tesseract-ocr"
            )
        if self._tessdata is None:
            self._tessdata = find_tessdata(
                self.lang_list,
                self._explicit_dir,
                allow_unverified=self.allow_unverified_tessdata,
            )
        return self._tessdata

    def read(self, image: Image.Image) -> OcrResult:
        import pytesseract
        from pytesseract import Output

        tessdata = self.ensure_ready()
        prepared = preprocess(
            image,
            upscale=self.upscale,
            autocontrast=self.autocontrast,
            invert=self.invert,
        )
        config = f"--psm {self.psm}"
        kwargs: dict = {"config": config, "output_type": Output.DICT}
        if tessdata:
            kwargs["lang"] = self.langs
            kwargs["config"] = f"{config} --tessdata-dir {tessdata}"

        import time

        t0 = time.monotonic()
        data = pytesseract.image_to_data(prepared, **kwargs)
        elapsed = time.monotonic() - t0

        scale = self.upscale or 1.0
        # Group tokens into their original visual lines.
        buckets: dict[tuple[int, int, int], list[tuple[str, float, tuple[int, int, int, int]]]] = {}
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if conf < 0:  # tesseract uses -1 for non-text elements
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            box = (
                round(data["left"][i] / scale),
                round(data["top"][i] / scale),
                round(data["width"][i] / scale),
                round(data["height"][i] / scale),
            )
            buckets.setdefault(key, []).append((text, conf, box))

        lines: list[OcrLine] = []
        dropped = 0
        for key in sorted(buckets, key=lambda k: (k[0], k[1], k[2])):
            tokens = buckets[key]
            text = " ".join(t[0] for t in tokens)
            conf = sum(t[1] for t in tokens) / len(tokens)
            # A low-confidence line is almost always HUD chrome or background
            # texture that leaked into the region. Feeding it to the translator
            # both wastes time and corrupts an otherwise good passage, so drop it.
            if conf < self.min_confidence:
                dropped += 1
                continue
            xs = [t[2][0] for t in tokens]
            ys = [t[2][1] for t in tokens]
            x2 = [t[2][0] + t[2][2] for t in tokens]
            y2 = [t[2][1] + t[2][3] for t in tokens]
            lines.append(
                OcrLine(
                    text=text,
                    confidence=conf,
                    box=(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)),
                )
            )

        return OcrResult(
            lines=lines,
            elapsed=elapsed,
            engine="tesseract",
            lang=self.langs,
            dropped=dropped,
        )


def _main(argv: list[str]) -> int:  # pragma: no cover - tiny CLI
    """python -m tlkun.ocr some.png [more.png ...]"""
    if not argv:
        print("usage: python -m tlkun.ocr <image> [image ...]", file=sys.stderr)
        return 2
    engine = TesseractOcr()
    for name in argv:
        with Image.open(name) as im:
            result = engine.read(im)
        print(f"--- {name} ({result.elapsed * 1000:.0f} ms, conf {result.confidence:.1f}) ---")
        for line in result.lines:
            print(f"  {line.confidence:5.1f}  {line.text}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
