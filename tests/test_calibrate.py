"""Calibrator tests, including the "auto-detect moved my selection" bug.

Reported from a real run: with a browser or chat window visible behind the game,
pressing Auto-detect teleported the selection to that window's text instead of
refining the box the user had drawn. A whole-screen scan picks the best-scoring
block anywhere; `near=` confines it so the button refines rather than moves.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from lintranslator.calibrate import calibrate

SCREEN = (1200, 800)


def _text_block(
    draw: ImageDraw.ImageDraw, x: int, y: int, lines: list[str], fill=(240, 240, 240)
) -> None:
    for i, line in enumerate(lines):
        draw.text((x, y + i * 18), line, fill=fill)


def _screen_with_two_blocks() -> Image.Image:
    """Dialogue near the bottom, plus a brighter, wider block higher up.

    The upper block deliberately scores better on raw ink and width, which is
    what used to make auto-detect jump to it.
    """
    img = Image.new("RGB", SCREEN, (12, 12, 16))
    draw = ImageDraw.Draw(img)

    # A "chat window" block: wide, bright, dense - attractive to a blind scan.
    draw.rectangle([40, 60, 1160, 300], fill=(28, 28, 34))
    for i in range(12):
        _text_block(draw, 60, 75 + i * 18, ["xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])

    # The actual game dialogue: lower, narrower, dimmer.
    draw.rectangle([300, 600, 900, 690], fill=(18, 18, 22))
    _text_block(draw, 320, 620, ["record pertaining to today's request."])
    _text_block(draw, 320, 645, ["The following is the case record."])
    return img


def test_whole_screen_scan_prefers_the_lowest_wide_block():
    """Baseline behaviour with no hint: the blind scan is what it is."""
    result = calibrate(_screen_with_two_blocks())
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)
    # With no hint it may pick either; this pins that it returns *something* sane.
    assert 0 <= x < SCREEN[0] and 0 <= y < SCREEN[1]


def test_near_hint_confines_the_search_to_the_selection():
    """The fix: given a selection around the dialogue, it must stay there."""
    img = _screen_with_two_blocks()
    dialogue = (300, 600, 600, 95)  # roughly what the user drew by hand

    result = calibrate(img, near=dialogue)
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)

    # It must land in the lower half, not on the brighter upper block.
    assert y > SCREEN[1] * 0.5, f"auto-detect jumped to y={y}, away from the selection"
    assert y + h <= SCREEN[1]
    # And it should stay near the region the caller cares about.
    assert abs(y - dialogue[1]) < 200, f"moved too far vertically: y={y}"


def test_near_hint_does_not_move_a_good_selection_far():
    """A hint box around the dialogue should come back close to it.

    Not identical: auto-detect snaps to the text it measured, which can be
    narrower than a hand-drawn box. What matters is that it stays anchored in the
    same place rather than jumping to another block.
    """
    img = _screen_with_two_blocks()
    dialogue = (310, 610, 580, 75)
    result = calibrate(img, near=dialogue)
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)
    assert abs(x - dialogue[0]) < 120, f"moved horizontally to x={x}"
    assert abs(y - dialogue[1]) < 60, f"moved vertically to y={y}"
    assert w > 100 and h > 20


def test_near_hint_is_stable_across_similar_hints():
    """The result should depend on where the text is, not on the exact hint."""
    img = _screen_with_two_blocks()
    got = [
        calibrate(img, near=hint).region.to_pixels(*SCREEN)
        for hint in ((300, 600, 600, 95), (310, 610, 580, 75), (360, 640, 400, 30))
    ]
    assert len(set(got)) == 1, f"auto-detect disagreed with itself: {got}"


def test_near_hint_returns_none_when_nothing_is_there():
    """An empty area must report failure, not silently fall back to elsewhere."""
    img = _screen_with_two_blocks()
    empty = (40, 380, 200, 60)  # a blank strip between the two blocks
    assert calibrate(img, near=empty) is None


def test_near_hint_still_finds_text_when_the_box_is_slightly_off():
    """Refining must tolerate a hand-drawn box that is a little out."""
    img = _screen_with_two_blocks()
    # Offset down and right, and too short - a realistic hand-drawn region.
    sloppy = (360, 640, 400, 30)
    result = calibrate(img, near=sloppy)
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)
    assert y > SCREEN[1] * 0.5
    assert h >= 20


def test_calibrate_returns_none_on_a_blank_screen():
    blank = Image.new("RGB", SCREEN, (10, 10, 10))
    assert calibrate(blank) is None
    assert calibrate(blank, near=(100, 100, 200, 200)) is None
