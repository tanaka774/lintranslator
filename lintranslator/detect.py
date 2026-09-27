"""Change detection: skip identical frames, wait out the typewriter reveal."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from PIL import Image


def similarity(a: str, b: str) -> float:
    """How alike two OCR reads are, 0..1."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_same_reading(a: str, b: str, max_edits: int = 2, min_similarity: float = 0.85) -> bool:
    """Whether two OCR reads are the same line seen twice, allowing for noise."""
    if a == b:
        return True
    if not a or not b:
        return False
    if abs(len(a) - len(b)) <= max_edits and _edit_distance(a, b) <= max_edits:
        return True
    if max(len(a), len(b)) < 24:
        return False
    return similarity(a, b) >= min_similarity


# Characters that end a sentence or a quotation; text ending in one counts as
# finished
TERMINAL_CHARS = frozenset('."\'!?…。！？」』）)]}')

# Characters that cannot end a sentence; their presence at the end proves the
# line is unfinished
CONTINUATION_CHARS = frozenset(",:;—–-、")


def looks_complete(text: str) -> bool:
    """Whether OCR text looks like a finished sentence rather than a fragment."""
    stripped = text.strip()
    if not stripped:
        return False
    last = stripped[-1]
    if last in CONTINUATION_CHARS:
        return False
    return last in TERMINAL_CHARS


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, two-row implementation."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(
                    previous[j] + 1,        # deletion
                    current[j - 1] + 1,     # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current
    return previous[-1]


def signature(image: Image.Image, stride: int = 4) -> bytes:
    """Compact fingerprint of an image: every `stride`-th grayscale pixel."""
    gray = image.convert("L")
    if stride > 1:
        gray = gray.resize(
            (max(1, gray.width // stride), max(1, gray.height // stride)),
            Image.Resampling.BILINEAR,
        )
    return bytes(gray.tobytes())


def mean_abs_delta(a: bytes, b: bytes) -> float:
    """Mean absolute difference between two equal-length byte signatures."""
    if len(a) != len(b):
        return 255.0
    if not a:
        return 0.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def changed_ratio(a: bytes, b: bytes, pixel_delta: int = 12) -> tuple[float, int]:
    """Return (fraction, count) of samples differing by at least `pixel_delta`."""
    if len(a) != len(b):
        return 1.0, max(len(a), len(b))
    if not a:
        return 0.0, 0
    hits = 0
    for x, y in zip(a, b):
        if abs(x - y) >= pixel_delta:
            hits += 1
    return hits / len(a), hits


def changed_fraction(a: bytes, b: bytes, pixel_delta: int = 12) -> float:
    """Fraction of samples differing by at least `pixel_delta`."""
    return changed_ratio(a, b, pixel_delta)[0]


@dataclass
class ChangeDetector:
    """Reports whether a frame differs enough from the previous one to re-OCR."""

    min_changed_fraction: float = 0.0005
    min_changed_samples: int = 1
    pixel_delta: int = 12
    stride: int = 4
    _last: bytes | None = field(default=None, repr=False)
    frames: int = 0
    changes: int = 0
    last_fraction: float = 0.0
    last_changed_samples: int = 0

    def update(self, image: Image.Image) -> bool:
        """Feed a frame; True if it is meaningfully different from the last one."""
        sig = signature(image, self.stride)
        self.frames += 1
        if self._last is None:
            self._last = sig
            self.changes += 1
            self.last_fraction = 1.0
            self.last_changed_samples = len(sig)
            return True

        fraction, count = changed_ratio(self._last, sig, self.pixel_delta)
        self.last_fraction = fraction
        self.last_changed_samples = count
        self._last = sig

        required = max(
            self.min_changed_samples,
            int(self.min_changed_fraction * len(sig)) if sig else 0,
        )
        changed = count >= max(1, required)
        if changed:
            self.changes += 1
        return changed

    def reset(self) -> None:
        self._last = None

    @property
    def change_rate(self) -> float:
        return self.changes / self.frames if self.frames else 0.0


@dataclass
class TextSettler:
    """Decides when an OCR result is final enough to translate."""

    settle_frames: int = 2
    settle_max_wait: float = 8.0
    settle_window: float = 1.2
    # Two reads within this many characters count as the same line
    settle_max_edits: int = 2
    # Seconds unfinished text must sit still before it is released anyway;
    # measured from the last change, and must stay under settle_max_wait
    incomplete_grace: float = 4.5
    _held_text: str | None = field(default=None, repr=False)
    _changed_at: float = 0.0
    _last_emit_at: float = 0.0

    def observe(self, text: str, now: float) -> bool:
        """Feed freshly OCR'd text. True when it is ready to translate."""
        text = text.strip()
        if not text:
            self.reset()
            return False

        if self._held_text is None:
            self._held_text = text
            self._changed_at = now
            return self.settle_frames <= 1

        if self._is_reveal(text, self._held_text):
            # Still being revealed: keep the longer read and restart the
            # stability window
            self._held_text = text if len(text) >= len(self._held_text) else self._held_text
            self._changed_at = now
            return self._ready(now)

        if is_same_reading(text, self._held_text, self.settle_max_edits):
            # Same line seen again (OCR noise): do not restart the stability
            # window
            return self._ready(now)

        # A different line: adopt it whatever its length, so a shorter line can
        # replace a longer stale one
        self._held_text = text
        self._changed_at = now
        return self._ready(now)

    @staticmethod
    def _is_reveal(text: str, held: str) -> bool:
        """Whether `text` is `held` still being typed out, rather than a new line."""
        if not text or not held or len(text) <= len(held):
            return False
        shared = len(held) - TextSettler.REVEAL_TAIL_ALLOWANCE
        if shared <= 0:
            return False
        return text[:shared] == held[:shared]

    # Characters of the held text allowed to differ where the reveal continued
    REVEAL_TAIL_ALLOWANCE = 4

    def tick(self, now: float) -> bool:
        """Time-based release for text that is not changing any more."""
        if self._held_text is None:
            return False
        return self._ready(now)

    def _ready(self, now: float) -> bool:
        if self._held_text is None:
            return False
        if self._last_emit_at and (now - self._last_emit_at) < self.settle_window:
            return False  # do not re-emit a line we just translated
        stable_for = now - self._changed_at
        # Unfinished text waits longer: a game pauses mid-reveal, and translating
        # the pause shows a fragment
        required = self.settle_window
        if not looks_complete(self._held_text):
            required += max(0.0, self.incomplete_grace)
        if stable_for >= required:
            return True
        return stable_for >= self.settle_max_wait

    def confirm(self, now: float) -> None:
        """Record that the held text was translated, so it is not re-emitted."""
        self._last_emit_at = now

    def reset(self) -> None:
        self._held_text = None
        self._changed_at = 0.0

    @property
    def held(self) -> str | None:
        return self._held_text

    def stable_seconds(self, now: float) -> float:
        """Seconds since the held text last changed, as of `now`."""
        if self._held_text is None or not self._changed_at:
            return 0.0
        return now - self._changed_at


@dataclass
class EmptyGuard:
    """Stops a region that lost its text from spamming the translator."""

    limit: int = 6
    _streak: int = 0
    _muted: bool = False

    def observe(self, has_text: bool) -> bool:
        """Returns True when text should be acted on."""
        if has_text:
            self._streak = 0
            if self._muted:
                self._muted = False
            return True
        self._streak += 1
        if self._streak >= self.limit:
            self._muted = True
        return False

    def reset(self) -> None:
        """Forget the streak, e.g. because the region being read has changed."""
        self._streak = 0
        self._muted = False

    @property
    def muted(self) -> bool:
        return self._muted

    def stats(self) -> dict[str, Any]:
        return {"muted": self._muted, "empty_streak": self._streak}
