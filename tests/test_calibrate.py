"""Tests for calibrate()'s near= hint, which confines auto-detect to a selection."""
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
    """Dialogue near the bottom, plus a brighter, wider block higher up."""
    img = Image.new("RGB", SCREEN, (12, 12, 16))
    draw = ImageDraw.Draw(img)

    draw.rectangle([40, 60, 1160, 300], fill=(28, 28, 34))
    for i in range(12):
        _text_block(draw, 60, 75 + i * 18, ["xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])

    draw.rectangle([300, 600, 900, 690], fill=(18, 18, 22))
    _text_block(draw, 320, 620, ["record pertaining to today's request."])
    _text_block(draw, 320, 645, ["The following is the case record."])
    return img


def test_whole_screen_scan_prefers_the_lowest_wide_block():
    result = calibrate(_screen_with_two_blocks())
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)
    assert 0 <= x < SCREEN[0] and 0 <= y < SCREEN[1]


def test_near_hint_confines_the_search_to_the_selection():
    img = _screen_with_two_blocks()
    dialogue = (300, 600, 600, 95)

    result = calibrate(img, near=dialogue)
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)

    assert y > SCREEN[1] * 0.5, f"auto-detect jumped to y={y}, away from the selection"
    assert y + h <= SCREEN[1]
    assert abs(y - dialogue[1]) < 200, f"moved too far vertically: y={y}"


def test_near_hint_does_not_move_a_good_selection_far():
    img = _screen_with_two_blocks()
    dialogue = (310, 610, 580, 75)
    result = calibrate(img, near=dialogue)
    assert result is not None
    x, y, w, h = result.region.to_pixels(*SCREEN)
    assert abs(x - dialogue[0]) < 120, f"moved horizontally to x={x}"
    assert abs(y - dialogue[1]) < 60, f"moved vertically to y={y}"
    assert w > 100 and h > 20


def test_near_hint_is_stable_across_similar_hints():
    img = _screen_with_two_blocks()
    got = [
        calibrate(img, near=hint).region.to_pixels(*SCREEN)
        for hint in ((300, 600, 600, 95), (310, 610, 580, 75), (360, 640, 400, 30))
    ]
    assert len(set(got)) == 1, f"auto-detect disagreed with itself: {got}"


def test_near_hint_returns_none_when_nothing_is_there():
    img = _screen_with_two_blocks()
    empty = (40, 380, 200, 60)
    assert calibrate(img, near=empty) is None


def test_near_hint_still_finds_text_when_the_box_is_slightly_off():
    img = _screen_with_two_blocks()
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
