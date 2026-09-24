"""Prompt templating shared by the settings dialog and the translator.

Kept out of the widget code so it can be tested without a display.
"""
from __future__ import annotations

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
