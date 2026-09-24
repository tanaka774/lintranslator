"""Text that can only have come from lintranslator's own windows.

The occlusion gate (`lintranslator.occlusion`) keeps the pipeline from capturing while the
picker or the settings dialog is on screen. The panel cannot be gated - it is the
output and stays visible while watching - so a panel dragged over the box is
caught here instead, by recognising our own text in the OCR result.

Two rules, in order of confidence:

* **Phrases.** Long, specific strings that no game dialogue contains
  ("drag over the dialogue text", "press Start to begin translating", or the
  picker's status line, which always contains `captured <w>x<h>`).
* **Whole reads.** A read that is *nothing but* one of our short button labels
  ("Pause", "Quit", "no selection"). Kept to whole reads on purpose: "Start" or
  "Close" can appear inside a real sentence, and flagging those would cost the
  user a translation.

Anything flagged here is skipped rather than translated, and the panel says so -
a false positive must be visible and explainable, never a silent missing line.
"""
from __future__ import annotations

import re

# Substrings that only our own UI produces. Matched case-insensitively anywhere in
# the read, so they survive OCR noise around them.
SELF_PHRASES = (
    "drag over the dialogue text",
    "still produces plausible ocr",
    # The picker's Preview dropdown. One phrase per entry rather than the bare
    # word: "raw" or "threshold" alone are words a game line can contain, while
    # the label as drawn ("Preview: raw") is not.
    "preview: raw",
    "preview: ocr input",
    "preview: threshold",
    "no text found in this region",
    "press start to begin translating",
    "press start to resume",
    "waiting for dialogue",
    "settings saved",
    "copied translation to clipboard",
    "not always-on-top",
    "kwin window rule",
    "add a kwin window rule",
    "the picker is on screen",
    "move the panel clear",
    "lintranslator",
)

# Reads that are nothing but a control label. Compared against the whole
# normalised text, never as a substring.
SELF_LABELS = frozenset(
    {
        "no selection",
        "apply box",
        "watching",
        "watching...",
        "watching…",
        "capture",
        "settings",
        "close",
        "quit",
        "copy",
        "translate",
        "pause",
        "start",
        "region",
    }
)

# The picker's status line and the panel's status line, both of which carry
# numbers that identify them:
#   "captured 2560x1440 — drag over the dialogue text"
#   "12:34:56 · conf 90 · 812 ms · openrouter"
_SELF_PATTERNS = (
    re.compile(r"captured\s+\d{3,5}\s*[x×]\s*\d{3,5}"),
    re.compile(r"\bconf\s+\d{1,3}\s*[·.\-*]\s*(cached|\d+\s*ms)"),
)

# Trailing punctuation/whitespace is dropped before the whole-read comparison, so
# "Pause." and "Pause …" still count as the label.
_EDGE = " \t\r\n.,;:!?…·-—*_'\"“”‘’()[]{}|"

# A read needs at least this many letters to be dialogue at all. Background art
# and UI chrome come back as one or two glyphs, often with punctuation glued on:
# measured on a patterned poster inside the box, tesseract returned "e¢" at 62.5%
# confidence - above the ordinary gate, and it was translated.
MIN_LETTERS = 2
# Below this many letters+digits a read carries no context to judge it by, so it
# has to be *more* confident than a normal line before a model is asked about it.
# Real short lines ("Yes.", "Hm?") come off a clean game font at 90%+.
SHORT_CHARS = 12
DEFAULT_SHORT_CONFIDENCE = 75.0


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def looks_like_own_ui(text: str) -> bool:
    """Whether `text` came from lintranslator's own window rather than the game."""
    if not text or not text.strip():
        return False
    folded = _normalise(text)
    for phrase in SELF_PHRASES:
        if phrase in folded:
            return True
    for pattern in _SELF_PATTERNS:
        if pattern.search(folded):
            return True
    return folded.strip(_EDGE) in SELF_LABELS


def noise_reason(
    text: str, confidence: float, short_confidence: float = DEFAULT_SHORT_CONFIDENCE
) -> str | None:
    """Why `text` is not dialogue, or None if it might be.

    The other rails - nothing to read, low confidence, our own window - all miss
    the same case: *confident nonsense*. Background art and UI chrome can read
    cleanly at 60-80% confidence, which is above the ordinary gate, and a
    translation of "e¢" is worse than none at all.

    Two cheap tests, both asking whether there is enough language present to be
    worth translating. Deliberately conservative: nothing longer than `SHORT_CHARS`
    is judged on confidence at all, and a real short line off a clean game font is
    high-confidence and passes. A false positive here costs one missing line.
    """
    if not text or not text.strip():
        return None  # empty is a different case, handled by the empty path
    letters = sum(1 for char in text if char.isalpha())
    alnum = sum(1 for char in text if char.isalnum())
    if letters < MIN_LETTERS or alnum < MIN_LETTERS:
        return "there is not enough text there to be dialogue"
    if alnum < SHORT_CHARS and confidence < short_confidence:
        return "it is too short to be sure of"
    return None
