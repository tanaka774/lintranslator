"""Text that can only have come from lintranslator's own windows."""
from __future__ import annotations

import re

# substrings only our own UI produces, matched case-insensitively anywhere in the
# read so they survive OCR noise around them
SELF_PHRASES = (
    "drag over the dialogue text",
    "still produces plausible ocr",
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

# reads that are nothing but a control label, compared against the whole
# normalised text rather than as a substring
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

# the picker's and the panel's status lines, both of which carry numbers that
# identify them
_SELF_PATTERNS = (
    re.compile(r"captured\s+\d{3,5}\s*[x×]\s*\d{3,5}"),
    re.compile(r"\bconf\s+\d{1,3}\s*[·.\-*]\s*(cached|\d+\s*ms)"),
)

# characters trimmed from both ends before the whole-read comparison, so "Pause."
# and "Pause …" still count as the label
_EDGE = " \t\r\n.,;:!?…·-—*_'\"“”‘’()[]{}|"

# a read needs at least this many letters to be dialogue at all
MIN_LETTERS = 2
# below this many letters+digits a read carries too little context to judge it
# by, so it has to be more confident than a normal line before a model is asked
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
    """Why `text` is not dialogue, or None if it might be."""
    if not text or not text.strip():
        return None  # empty is a different case, handled by the empty path
    letters = sum(1 for char in text if char.isalpha())
    alnum = sum(1 for char in text if char.isalnum())
    if letters < MIN_LETTERS or alnum < MIN_LETTERS:
        return "there is not enough text there to be dialogue"
    if alnum < SHORT_CHARS and confidence < short_confidence:
        return "it is too short to be sure of"
    return None
