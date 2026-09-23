"""Change detection: skip identical frames, wait out the typewriter reveal.

Two independent mechanisms that solve different problems:

* `ChangeDetector` - cheap "are the pixels different at all?" gate. Sitting at
  2 fps, most polls see an identical frame as long as no text is being drawn.
  Comparing a strided grayscale signature is ~50x cheaper than OCR, so this gate
  is what keeps an always-on poll loop affordable.

* `TextSettler` - "has the text finished appearing?" gate. Games reveal dialogue
  one character at a time, so a mid-reveal OCR result is a prefix of the final
  line and translating it shows a truncated sentence. We therefore wait until the
  text stops changing - where "stops changing" means near-identical reads, not
  exactly identical ones, because OCR jitter never repeats exactly.
"""
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
    """Whether two OCR reads are the same line seen twice, allowing for noise.

    Two tests, either of which is sufficient, because they cover different
    regimes:

    * **Few edits** - decisive on short lines, where a ratio is meaningless
      ("line" vs "line." scores 0.89 yet is obviously the same line).
    * **High similarity** - decisive on long lines, where jitter is *not* "a
      character or two". Measured on a live Limbus line (180-220 chars read off a
      region with animated UI chrome), consecutive reads of one unchanged line
      differed by a median of 5 characters and up to ~18% of the line, and their
      similarity ran down to 0.896. An edit-distance-only rule calls those
      different lines, which breaks the loop in two visible ways: the settle
      timer is reset by noise so the line is never translated, and the duplicate
      check lets the same line through twice.

    The ratio test is length-gated because a short line can drift a large
    *fraction* of itself while still being a different line; on lines that short
    the edit test alone decides. A genuine line change sits far below the
    threshold (measured: similarity 0.31 between two consecutive game lines,
    against 0.896+ for noise on one line).
    """
    if a == b:
        return True
    if not a or not b:
        return False
    if abs(len(a) - len(b)) <= max_edits and _edit_distance(a, b) <= max_edits:
        return True
    if max(len(a), len(b)) < 24:
        return False
    return similarity(a, b) >= min_similarity


# Characters that end a sentence or a quotation. Text ending in one of these is
# treated as finished; anything else is probably still being revealed.
TERMINAL_CHARS = frozenset('."\'!?…。！？」』）)]}')

# Characters that cannot end a sentence, so their presence at the end proves the
# line is unfinished even if a later glyph of a longer reading is not visible yet.
CONTINUATION_CHARS = frozenset(",:;—–-、")


def looks_complete(text: str) -> bool:
    """Whether OCR text looks like a finished sentence rather than a fragment.

    Deliberately asymmetric. Calling a finished line "unfinished" only delays it
    by `incomplete_grace`; calling a fragment "finished" puts a truncated
    translation in front of the user and then translates the line again when the
    rest appears. So the benefit of the doubt goes to unfinished, and a line is
    considered finished only when it ends in sentence-ending punctuation.

    Text ending in a continuation character (a comma, dash or colon) is always
    unfinished: those are the exact points where games hold a reveal before
    continuing.
    """
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
    """Return (fraction, count) of samples differing by at least `pixel_delta`.

    Deliberately not the mean difference: a localized change (the typewriter
    adding one word, a portrait appearing) barely moves the mean because most of
    the region is unchanged, yet it is exactly the change we must react to.
    Counting affected samples finds it regardless of area.
    """
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
    """Reports whether a frame differs enough from the previous one to re-OCR.

    The threshold is `max(min_changed_samples, ceil(fraction * n))`. A pure
    fraction is not enough: after 4x downsampling the region yields only ~1200
    samples, and revealing a single character can move fewer than 0.1% of them.
    An absolute floor keeps small regions as sensitive as large ones. The floor
    defaults to 1 rather than 3: at stride 4 a single changed character resolves
    to only ~1 sample, so a higher floor would silently miss it. False positives
    are kept in check by `pixel_delta`, which ignores the sub-12-level noise that
    video compression produces.
    """

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
    """Decides when an OCR result is final enough to translate.

    Two non-obvious requirements shaped this, both learned from live screens:

    1. **OCR only runs when pixels change**, and a live game is rarely
       pixel-stable (blinking advance cursor, animated portrait, drifting
       particles, or just the mouse moving over the region). Waiting for a second
       confirming read therefore waits forever. `tick` handles this: the caller
       re-reads the same frame on a timer and feeds the result back in.

    2. **An exact-match rule never settles a jittering read.** If OCR returns
       "line" and "line." on alternating polls, the text differs on *every* poll,
       so any timer keyed on "time since last change" is reset forever and nothing
       is ever translated - exactly what was observed on a live screen. The fix is
       to treat a near-identical read as the same line (`settle_max_edits` and the
       similarity floor), while a genuine typewriter reveal keeps growing and so
       differs by far more than that threshold.

    3. **A pause in the reveal is not the end of the sentence.** Games reveal
       dialogue in steps and hold briefly between them, so the text can sit still
       for longer than `settle_window` while the sentence is still unfinished.
       Releasing then puts a fragment in the panel ("...the proverbial poster
       child of Work") and translates it a second time when the rest arrives.
       Plain "stable for N seconds" cannot tell a finished line from a paused one,
       so unfinished text waits longer: `incomplete_grace`.
    """

    settle_frames: int = 2
    settle_max_wait: float = 8.0
    settle_window: float = 1.2
    # Two reads within this many characters count as the same line. OCR noise is
    # a character or two; a typewriter reveal grows by far more.
    settle_max_edits: int = 2
    # How long unfinished text must sit still before it is released anyway.
    #
    # Measured from the last change, like `settle_window`, so a reveal that is
    # still growing keeps pushing the deadline out. Longer than any pause a game
    # takes mid-sentence (observed: ~3 s), and well under `settle_max_wait`, so a
    # partial line surfaces eventually instead of being stuck forever.
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
            # First sighting: hold it and wait to see whether it stays put.
            self._held_text = text
            self._changed_at = now
            return self.settle_frames <= 1

        if self._is_reveal(text, self._held_text):
            # The line is still being revealed: keep the longer read and restart
            # the stability window, but do NOT treat it as a different line.
            self._held_text = text if len(text) >= len(self._held_text) else self._held_text
            self._changed_at = now
            return self._ready(now)

        if is_same_reading(text, self._held_text, self.settle_max_edits):
            # The same line seen again (OCR noise): do NOT restart the stability
            # window, or a jittering read would never settle.
            return self._ready(now)

        # A different line. It must be adopted whatever its length.
        #
        # "Keep the longer read" applies to a reveal and to jitter of one line -
        # never here. Keeping a longer *stale* line meant a shorter new line
        # could never replace it: the read differed from the held text on every
        # poll, so the stability window restarted forever and nothing was ever
        # translated again until a longer line happened to appear. Measured on a
        # live screen with our own status line inside the box, where filtering
        # that line out is exactly what makes the game's text shorter.
        self._held_text = text
        self._changed_at = now
        return self._ready(now)

    @staticmethod
    def _is_reveal(text: str, held: str) -> bool:
        """Whether `text` is `held` still being typed out, rather than a new line.

        A game reveals a line in steps, and a step can add a lot at once (the next
        word, not the next character). Judged by edit distance that is "a very
        different line" - 20 edits, say - so the completed sentence was treated as
        new, the paused fragment got translated first, and the panel showed the
        truncation the user reported. Growth is therefore recognised by content:
        the held text is a prefix of the new read, allowing for OCR noise at the
        junction.
        """
        if not text or not held or len(text) <= len(held):
            return False
        shared = len(held) - TextSettler.REVEAL_TAIL_ALLOWANCE
        if shared <= 0:
            return False
        return text[:shared] == held[:shared]

    # Characters of the held text allowed to differ where the reveal continued,
    # because the last glyph typed is the one OCR is most likely to misread.
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
        # Unfinished text is not released just because it stopped moving: a game
        # pauses mid-reveal, and translating the pause shows a fragment.
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
        """Seconds since the held text last changed, as of `now`.

        Takes `now` rather than reading the clock, so the whole settler works on
        one time base. Mixing an injected clock with wall-clock reads makes the
        settle window behave differently under test than in production.
        """
        if self._held_text is None or not self._changed_at:
            return 0.0
        return now - self._changed_at


@dataclass
class EmptyGuard:
    """Stops a region that lost its text from spamming the translator.

    During transitions (scene change, menu, fade) the configured box can be
    empty. Translating "nothing" is pure waste, so after a few consecutive empty
    reads we stop reporting until text returns.
    """

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
