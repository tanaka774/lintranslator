"""OCR via tesseract, tuned for game UI text."""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from . import paths

# Pinned revision, digest-checked before install: the file is parsed by
# tesseract's C++ model loader, so an unchecked fetch is a supply-chain hole.
# The digests below are of the files at `TESSDATA_REVISION`.
TESSDATA_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_URL = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
    f"{TESSDATA_REVISION}/{{lang}}.traineddata"
)

# Ceiling in bytes on one model file held in memory; chi_sim is ~20 MB.
MAX_TESSDATA_BYTES = 64 * 1024 * 1024

# Languages the app fetches by itself; others need ocr.allow_unverified_tessdata.
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

SYSTEM_TESSDATA_CANDIDATES = (
    "/usr/share/tessdata",
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/tessdata",
    "/usr/local/share/tessdata",
    "/opt/homebrew/share/tessdata",
)


class OcrError(RuntimeError):
    """OCR could not run: missing binary or missing language data."""


# Only characters that cannot end an English sentence are treated as the caret:
# stripping any lone trailing character corrupts real sentence endings.
_SAFE_TRAILING = frozenset('."\'!?…,;:。！？、」』)]}')
# Letters and digits the caret was misread as; other alphanumerics are text, so
# real one-letter words ("I") and trailing numbers stay intact.
_CARET_GLYPHS = frozenset("lO0147f|+~_/\\^`*éè")
_TRAILING_CURSOR = re.compile(r"\s+(\S)$")
# The caret is often drawn after the terminal punctuation ("battlefield. f"), so
# it cannot be detected by looking at the end of the string alone.
_CARET_AFTER_STOP = re.compile(r"([.!?…])\s+(\S)$")


def _is_caret_token(token: str) -> bool:
    """Whether a lone character is the caret rather than a real word ending."""
    if token.isalnum():
        return token in _CARET_GLYPHS
    return True  # bare punctuation as a token is never a sentence ending


def strip_trailing_cursor(text: str) -> str:
    """Drop a lone trailing character left behind by the advance caret."""
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
    # Lines removed for any reason: the confidence gate, or our own UI in the box.
    dropped: int = 0
    # Gate-rejected lines, kept whole so the UI can show what the gate threw away.
    rejected: list[OcrLine] = field(default_factory=list)

    def without_lines(self, predicate) -> "OcrResult":
        """A copy with the lines matching `predicate` removed."""
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
            rejected=self.rejected,
        )

    @property
    def text(self) -> str:
        """Lines joined into one passage for translation."""
        joined = " ".join(line.text for line in self.lines)
        return strip_trailing_cursor(" ".join(joined.split()))

    @property
    def display_text(self) -> str:
        """The passage with visual line breaks preserved, for display."""
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


#: Tesseract page segmentation modes (`ocr.psm`) this app names, by box shape.
PSM_NAMES = {
    6: "one block of text",
    7: "a single line",
    11: "scattered text",
}


def threshold_value(value) -> int:
    """A usable ink/paper cut in 1..255, or 0 for "leave the image grey"."""
    try:
        cut = int(value)
    except (TypeError, ValueError):
        return 0
    return cut if 1 <= cut <= 255 else 0


def preprocess(
    image: Image.Image,
    *,
    upscale: float = 3.0,
    autocontrast: bool = True,
    invert: bool = False,
    threshold: int = 0,
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
    cut = threshold_value(threshold)
    if cut:
        gray = gray.point(lambda p: 255 if p > cut else 0)
    return gray


def otsu_threshold(gray: Image.Image) -> int:
    """The cut tesseract picks for itself: Otsu, from the image's histogram."""
    hist = gray.convert("L").histogram()[:256]
    total = sum(hist)
    if total == 0:
        return 128
    weighted = sum(level * count for level, count in enumerate(hist))
    background_weight = 0
    background_sum = 0.0
    best_cut, best_variance = 128, -1.0
    for cut in range(256):
        background_weight += hist[cut]
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += cut * hist[cut]
        mean_background = background_sum / background_weight
        mean_foreground = (weighted - background_sum) / foreground_weight
        variance = (
            background_weight
            * foreground_weight
            * (mean_background - mean_foreground) ** 2
        )
        if variance > best_variance:
            best_variance, best_cut = variance, cut
    return best_cut


def split_langs(langs: str) -> list[str]:
    """Split `eng+jpn` / `eng jpn` into names, without validating them."""
    return [x for x in langs.replace("+", " ").split() if x]


def bad_langs(langs: str) -> list[str]:
    """The names in `langs` that are not plain tesseract language names."""
    return [name for name in split_langs(langs) if not LANG_NAME_RE.match(name)]


def parse_langs(langs: str) -> list[str]:
    """Split and validate; the names become `-l` arguments and tessdata filenames."""
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


def _file_digest(path: Path) -> str:
    """SHA-256 of a file, read in blocks so a model file is never held twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download_tessdata(
    langs: list[str],
    dest: Path | None = None,
    *,
    allow_unverified: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """Fetch `tessdata_fast` language files into the app data dir (no root)."""
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
        expected = TESSDATA_SHA256.get(lang)
        if target.exists() and target.stat().st_size > 0:
            # An existing file is not proof it is the pinned one: re-check the digest.
            if expected is None:
                continue
            try:
                already_pinned = _file_digest(target) == expected
            except OSError:
                already_pinned = False
            if already_pinned:
                continue
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
                if on_progress is not None:
                    length = resp.headers.get("Content-Length")
                    size = f" ({int(length) / 1e6:.1f} MB)" if length else ""
                    on_progress(f"downloading {lang}.traineddata{size} -> {dest}")
                payload = resp.read(MAX_TESSDATA_BYTES + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OcrError(
                f"could not download {lang}.traineddata from {url}: {exc}\n"
                f"Download it manually into {dest}/, or install it system-wide."
            ) from exc
        if len(payload) > MAX_TESSDATA_BYTES:
            raise OcrError(
                f"{lang}.traineddata is larger than "
                f"{MAX_TESSDATA_BYTES // (1024 * 1024)} MB; refusing to install it"
            )
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
        # Write via a temporary name: a half-written file would look ready next run.
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
    return dest


def find_tessdata(
    langs: list[str],
    preferred: str | None = None,
    *,
    allow_unverified: bool = False,
    on_progress: Callable[[str], None] | None = None,
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
    candidates.extend(Path(p) for p in SYSTEM_TESSDATA_CANDIDATES)

    for path in candidates:
        if path.is_dir() and all((path / f"{lang}.traineddata").exists() for lang in langs):
            return path

    return download_tessdata(
        langs, allow_unverified=allow_unverified, on_progress=on_progress
    )


def installed_models(extra_dir: str | None = None) -> set[str]:
    """Every language model already on this machine, by stem."""
    dirs: list[Path] = []
    if extra_dir:
        dirs.append(Path(extra_dir))
    dirs.append(paths.DATA_DIR / "tessdata")
    dirs.extend(Path(p) for p in SYSTEM_TESSDATA_CANDIDATES)

    found: set[str] = set()
    for path in dirs:
        if path.is_dir():
            found.update(
                entry.name[: -len(".traineddata")]
                for entry in path.glob("*.traineddata")
            )
    return found


def model_state(stem: str, installed: set[str] | None = None) -> str:
    """How OCR would get this model: already here, downloaded, or installed by hand."""
    if stem in (installed_models() if installed is None else installed):
        return "installed"
    return "download" if stem in TESSDATA_SHA256 else "missing"


def tesseract_version() -> str | None:
    """The version string of the system binary, or None when it is not there."""
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
        invert: bool = False,
        threshold: int = 0,
        tessdata_dir: str | None = None,
        min_confidence: float = 40.0,
        allow_unverified_tessdata: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.langs = langs
        self.psm = psm
        self.upscale = upscale
        self.autocontrast = autocontrast
        self.invert = invert
        self.threshold = threshold
        self.min_confidence = min_confidence
        self.allow_unverified_tessdata = allow_unverified_tessdata
        # Called before a language file is fetched; the GUI passes its status row.
        self.on_progress = on_progress
        self._explicit_dir = tessdata_dir
        self._tessdata: Path | None = None

    @property
    def lang_list(self) -> list[str]:
        """The configured languages, validated."""
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
                on_progress=self.on_progress,
            )
        return self._tessdata

    def prepared(self, image: Image.Image) -> Image.Image:
        """The exact image `read` hands to tesseract."""
        return preprocess(
            image,
            upscale=self.upscale,
            autocontrast=self.autocontrast,
            invert=self.invert,
            threshold=self.threshold,
        )

    def read(self, image: Image.Image) -> OcrResult:
        import pytesseract
        from pytesseract import Output

        tessdata = self.ensure_ready()
        prepared = self.prepared(image)
        config = f"--psm {self.psm}"
        kwargs: dict = {"config": config, "output_type": Output.DICT}
        if tessdata:
            # `-l` separates names with `+` only: "kor eng" is read as one model
            # name and tesseract loads no language at all.
            kwargs["lang"] = "+".join(self.lang_list)
            kwargs["config"] = f"{config} --tessdata-dir {tessdata}"

        import time

        t0 = time.monotonic()
        data = pytesseract.image_to_data(prepared, **kwargs)
        elapsed = time.monotonic() - t0

        scale = self.upscale or 1.0
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
        rejected: list[OcrLine] = []
        for key in sorted(buckets, key=lambda k: (k[0], k[1], k[2])):
            tokens = buckets[key]
            text = " ".join(t[0] for t in tokens)
            conf = sum(t[1] for t in tokens) / len(tokens)
            xs = [t[2][0] for t in tokens]
            ys = [t[2][1] for t in tokens]
            x2 = [t[2][0] + t[2][2] for t in tokens]
            y2 = [t[2][1] + t[2][3] for t in tokens]
            line = OcrLine(
                text=text,
                confidence=conf,
                box=(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)),
            )
            # Drop low-confidence HUD chrome and texture, but keep them for the picker.
            if conf < self.min_confidence:
                rejected.append(line)
                continue
            lines.append(line)

        return OcrResult(
            lines=lines,
            elapsed=elapsed,
            engine="tesseract",
            lang=self.langs,
            dropped=len(rejected),
            rejected=rejected,
        )


def _main(argv: list[str]) -> int:  # pragma: no cover - tiny CLI
    """python -m lintranslator.ocr some.png [more.png ...]"""
    if not argv:
        print("usage: python -m lintranslator.ocr <image> [image ...]", file=sys.stderr)
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
