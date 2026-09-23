"""The two guards that keep lintranslator from translating itself.

Both exist because of a measured failure: the frame captured 10 ms after "Watch
live" was pressed contained the picker's own status line, and the dialogue line
the picker covered was dropped by the confidence gate underneath it.

* `OcclusionGuard` is the blunt instrument - while one of our windows is on
  screen, the pipeline does not capture at all.
* `looks_like_own_ui` is the safety net for the one window that cannot be gated
  (the panel is the output and has to stay visible).

The false-positive tests matter as much as the positive ones: flagging real
dialogue would silently cost the user a line, which is the same class of bug in
the other direction.
"""
from __future__ import annotations

from lintranslator.occlusion import OcclusionGuard
from lintranslator.selftext import looks_like_own_ui, noise_reason


# --------------------------------------------------------------------------- #
# OcclusionGuard
# --------------------------------------------------------------------------- #
def test_a_guard_with_nothing_mapped_does_not_block():
    guard = OcclusionGuard()
    assert guard.reason() is None
    assert guard.blocked is False
    assert guard.windows == []


def test_a_mapped_window_blocks_and_says_what_to_do():
    guard = OcclusionGuard()
    guard.set_mapped("picker", True, "the region picker is on screen — minimise it")
    assert guard.blocked is True
    assert guard.reason() == "the region picker is on screen — minimise it"
    assert guard.windows == ["picker"]


def test_unmapping_lifts_the_block():
    guard = OcclusionGuard()
    guard.set_mapped("picker", True, "picker")
    guard.set_mapped("picker", False)
    assert guard.blocked is False
    # Clearing twice must not raise: unmap and close both report.
    guard.clear("picker")
    assert guard.blocked is False


def test_several_windows_stay_blocked_until_the_last_one_goes():
    guard = OcclusionGuard()
    guard.set_mapped("picker", True, "the region picker is on screen")
    guard.set_mapped("settings", True, "the settings window is on screen")
    assert guard.reason() == "the region picker is on screen"
    guard.clear("picker")
    assert guard.reason() == "the settings window is on screen"
    guard.clear("settings")
    assert guard.blocked is False


def test_a_window_without_a_note_still_blocks():
    """The note is for the user, but its absence must not disable the guard."""
    guard = OcclusionGuard()
    guard.set_mapped("panel", True)
    assert guard.blocked is True
    assert guard.reason()


# --------------------------------------------------------------------------- #
# looks_like_own_ui — the reads that were actually captured
# --------------------------------------------------------------------------- #
def test_the_measured_polluted_capture_is_recognised():
    """Verbatim from `data/probe_polluted_first_crop.png` at min_confidence=55."""
    measured = (
        "Watcning... captured 2560x1440 — dra without letting them in on all "
        "available information... Ah, | can practically hear the complaints lodged "
        "in the tick-tocks of your winding clockwork. é."
    )
    assert looks_like_own_ui(measured) is True


def test_the_picker_status_line_is_recognised():
    assert looks_like_own_ui("captured 2560x1440 — drag over the dialogue text")
    assert looks_like_own_ui("captured 1920x1080 - drag over the dialogue text")


def test_panel_and_picker_chrome_is_recognised():
    for text in (
        "12:34:56 · conf 90 · 812 ms · openrouter",
        "Waiting for dialogue…",
        "press Start to begin translating",
        "paused — press Start to resume",
        "Preview: raw",
        "Preview: OCR input",
        "Preview: threshold",
        "(no text found in this region)",
        "Cover the whole text block. A region that is slightly too short…",
        "not always-on-top — on Wayland add a KWin window rule",
        "This window is a still screenshot, so it will not follow the game.",
    ):
        assert looks_like_own_ui(text) is True, text


def test_control_labels_count_only_as_a_whole_read():
    """A lone button label is ours; the same word inside a sentence is not."""
    for label in ("Pause", "Quit", "no selection", "Watch live", "Apply box"):
        assert looks_like_own_ui(label) is True, label
        assert looks_like_own_ui(f"  {label}.  ") is True, label

    for sentence in (
        "Pause for a moment and consider what that means.",
        "Close the door behind you.",
        "Start again, then. From the beginning.",
        "Copy that down before you forget it.",
    ):
        assert looks_like_own_ui(sentence) is False, sentence


def test_real_dialogue_is_never_flagged():
    """The expensive mistake is the false positive: it loses a line silently."""
    for line in (
        "The city was captured by the enemy.",
        "Watching the city burn, he said nothing.",
        "I have the ability to rewind them back to life, after all.",
        "Herr Gregor is the proverbial poster child of Workshop-sponsored Fixers.",
        "See you around.",
        "Manager! The abnormality is approaching. Prepare for combat.",
        "It has been determined that this case merits preservation as a record.",
        "",
        "   ",
    ):
        assert looks_like_own_ui(line) is False, line


# --------------------------------------------------------------------------- #
# noise_reason — "confident nonsense" is not dialogue
# --------------------------------------------------------------------------- #
def test_background_art_that_ocr_is_wrongly_sure_about_is_not_dialogue():
    """Measured, not imagined: a patterned poster inside the box read as 'e¢ ¢' at
    62.5% - above the ordinary confidence gate - and was translated until this
    rail existed."""
    assert noise_reason("e¢", 62.5) is not None


def test_a_read_without_real_words_is_not_dialogue():
    for text in ("12", "= .", "...", "| / \\", "42 7"):
        assert noise_reason(text, 99.0) is not None, text


def test_a_single_letter_is_not_enough_to_translate():
    assert noise_reason("I", 99.0) is not None


def test_short_and_unsure_is_skipped_but_short_and_clean_is_not():
    """The rule has to leave real short lines alone: "Yes." off a clean game font
    reads in the nineties, and losing it would be a missing line."""
    assert noise_reason("Yes.", 70.0) is not None, "short and unsure should wait"
    assert noise_reason("Yes.", 92.0) is None
    assert noise_reason("Hm?", 88.0) is None


def test_long_reads_are_never_judged_on_confidence():
    """Length is context: a long line is judged by the OCR gate, not by this."""
    long_low = "wv f] |<aSte OFjnd =. 4/7 ae and then some more of it"
    assert noise_reason(long_low, 40.0) is None


def test_real_dialogue_is_never_flagged_as_noise():
    for text, confidence in (
        ("The city was captured by the enemy.", 96.0),
        ("If I'm to put it m", 57.4),  # a real mid-reveal read, measured live
        ("Here, however... everyone seems to be mired... in ennui.", 88.0),
        ("Herr Gregor is the proverbial poster child of Workshop-sponsored Fixers.", 93.0),
        ("Yes.", 91.0),
    ):
        assert noise_reason(text, confidence) is None, text


def test_cjk_short_lines_are_not_mistaken_for_noise():
    """Letters are letters in any script; a clean CJK read passes."""
    assert noise_reason("異常", 93.0) is None
    assert noise_reason("異常", 60.0) is not None, "short and unsure still waits"
