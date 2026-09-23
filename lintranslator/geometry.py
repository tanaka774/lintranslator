"""Region arithmetic and prompt templating shared by the GUI controls.

Kept out of the widget code so it can be tested without a display: a region that
silently grows past the screen edge, or shrinks to nothing, is a bug that only
shows up as bad OCR much later.
"""
from __future__ import annotations

from .config import Region

# A game-agnostic starting point. Short on purpose: long prompts get truncated by
# small models and add latency to every line.
DEFAULT_PROMPT = (
    "You are translating in-game dialogue from {source} into {target}. "
    "This is a video game script, not a document, so keep it natural and concise.\n"
    "Output only the translation - no notes, no romaji, no quotes, no explanations. "
    "Keep the speaker's tone, and preserve leading or trailing brackets and symbols."
)

# Offered in the GUI so nobody has to write a prompt from scratch. The first
# entry *is* the built-in default, and is named for that rather than for a genre:
# it is what an empty `translate.prompt` falls back to, not one choice among
# three. The Limbus one exists because the game's terminology is unusual enough
# that a generic prompt produces wrong names and a wrong register.
PROMPT_PRESETS: dict[str, str] = {
    "Default prompt": DEFAULT_PROMPT,
    "Limbus Company": (
        "You are translating dialogue from the video game Limbus Company "
        "({source} into {target}).\n"
        "Context: it is a dark, bleak dystopian RPG. The player character is "
        "addressed as the Manager (マネージャー) and manages a group of twelve "
        "'Sinners' (罪人). Recurring terms: Distortion, E.G.O, Identity, "
        "Mirror Dungeon, Golden Bough, the City, Wing, Fixer, Backstreets.\n"
        "Style: terse and grim. Characters speak bluntly; Faust is clinical and "
        "precise, Dante is silent, Ryoshu is crude and abbreviated, Outis is "
        "militaristic, Don Quixote is theatrical.\n"
        "Rules: output only the translation. Keep names in their official Latin "
        "script rather than transliterating them. Preserve brackets like [ ] and "
        "any UI symbols. Do not add information that is not in the source."
    ),
    "Literal / faithful": (
        "Translate this {source} text into {target} as literally as possible while "
        "staying grammatical. Do not embellish or localise. Output only the "
        "translation."
    ),
}


def fill_prompt(template: str, source: str, target: str) -> str:
    """Substitute the language names into a prompt template.

    Uses explicit replacement rather than str.format so a stray brace in a
    user-written prompt cannot raise.
    """
    return template.replace("{source}", source).replace("{target}", target)


# --------------------------------------------------------------------------- #
# Region arithmetic (all in screen pixels)
# --------------------------------------------------------------------------- #
MIN_SIZE = 24


def scaled_region(
    region: tuple[int, int, int, int],
    screen: tuple[int, int],
    factor: float,
    step: int = 8,
) -> tuple[int, int, int, int]:
    """Grow or shrink a region about its own centre, clamped to the screen."""
    x, y, w, h = region
    screen_w, screen_h = screen

    def snap(value: int, original: int) -> int:
        # Snap to a grid so repeated clicks produce tidy numbers, but never
        # disturb a dimension the caller did not change (factor 1.0 on one axis
        # is a common call, and re-snapping it would be a surprise edit).
        snapped = max(MIN_SIZE, int(round(value / step)) * step)
        return original if value == original else snapped

    width = max(MIN_SIZE, min(screen_w, snap(int(round(w * factor)), w)))
    height = max(MIN_SIZE, min(screen_h, snap(int(round(h * factor)), h)))

    # Keep the centre fixed, then push back inside the screen if that overflows.
    cx = x + w / 2
    cy = y + h / 2
    new_x = int(round((cx - width / 2) / step) * step)
    new_y = int(round((cy - height / 2) / step) * step)
    new_x = max(0, min(new_x, screen_w - width))
    new_y = max(0, min(new_y, screen_h - height))
    # An unchanged axis keeps its exact position: recomputing it from the centre
    # would nudge a region the caller never asked to move.
    if width == w:
        new_x = x
    if height == h:
        new_y = y
    return (new_x, new_y, width, height)


def nudge_region(
    region: tuple[int, int, int, int],
    screen: tuple[int, int],
    dx: int = 0,
    dy: int = 0,
) -> tuple[int, int, int, int]:
    """Move a region without resizing it, clamped to the screen."""
    x, y, w, h = region
    screen_w, screen_h = screen
    return (
        max(0, min(x + dx, screen_w - w)),
        max(0, min(y + dy, screen_h - h)),
        w,
        h,
    )


def region_to_fraction(
    region: tuple[int, int, int, int], screen: tuple[int, int]
) -> Region:
    """Screen pixels -> a resolution-independent Region."""
    x, y, w, h = region
    screen_w, screen_h = screen
    return Region(
        x=x / screen_w, y=y / screen_h, w=w / screen_w, h=h / screen_h, mode="fraction"
    )
