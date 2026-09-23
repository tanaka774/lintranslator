"""Tests for region arithmetic and prompt templating.

A region that silently grows past the screen edge, or shrinks to nothing, shows
up only as bad OCR much later, so the arithmetic is pinned here.
"""
from __future__ import annotations

import pytest

from lintranslator.geometry import (
    DEFAULT_PROMPT,
    MIN_SIZE,
    PROMPT_PRESETS,
    fill_prompt,
    nudge_region,
    region_to_fraction,
    scaled_region,
)

SCREEN = (2560, 1440)
REGION = (1000, 700, 400, 100)


# --------------------------------------------------------------------------- #
# Scaling
# --------------------------------------------------------------------------- #
def test_scaling_grows_about_the_centre():
    x, y, w, h = scaled_region(REGION, SCREEN, 1.2)
    assert w > 400 and h > 100
    # Centre should be roughly preserved.
    assert abs((x + w / 2) - (1000 + 200)) <= 8
    assert abs((y + h / 2) - (700 + 50)) <= 8


def test_scaling_shrinks_about_the_centre():
    x, y, w, h = scaled_region(REGION, SCREEN, 0.8)
    assert w < 400 and h < 100
    assert abs((x + w / 2) - 1200) <= 8


def test_scaling_never_goes_below_the_minimum():
    """A zero-height region would crop to an empty image and break OCR."""
    _, _, w, h = scaled_region((100, 100, 30, 30), SCREEN, 0.1)
    assert w >= MIN_SIZE and h >= MIN_SIZE


def test_scaling_never_exceeds_the_screen():
    x, y, w, h = scaled_region(REGION, SCREEN, 10.0)
    assert x >= 0 and y >= 0
    assert x + w <= SCREEN[0] and y + h <= SCREEN[1]


def test_scaling_a_region_at_the_edge_stays_on_screen():
    """A region hugging the bottom-right must not overflow when grown."""
    edge = (SCREEN[0] - 200, SCREEN[1] - 80, 200, 80)
    x, y, w, h = scaled_region(edge, SCREEN, 1.5)
    assert x + w <= SCREEN[0], "grew past the right edge"
    assert y + h <= SCREEN[1], "grew past the bottom edge"


def test_scaling_is_idempotent_for_factor_one():
    assert scaled_region(REGION, SCREEN, 1.0) == REGION


# --------------------------------------------------------------------------- #
# Nudging
# --------------------------------------------------------------------------- #
def test_nudge_moves_without_resizing():
    got = nudge_region(REGION, SCREEN, 8, -8)
    assert got == (1008, 692, 400, 100)


def test_nudge_clamps_at_the_edges():
    assert nudge_region((0, 0, 400, 100), SCREEN, -50, -50)[:2] == (0, 0)
    x, y, _, _ = nudge_region(REGION, SCREEN, 99999, 99999)
    assert x == SCREEN[0] - 400
    assert y == SCREEN[1] - 100


# --------------------------------------------------------------------------- #
# Fractions
# --------------------------------------------------------------------------- #
def test_region_to_fraction_round_trips():
    region = region_to_fraction(REGION, SCREEN)
    assert region.mode == "fraction"
    assert region.to_pixels(*SCREEN) == REGION


def test_region_to_fraction_is_resolution_independent():
    """The same fraction must land proportionally at a different resolution."""
    region = region_to_fraction((640, 360, 1280, 360), (2560, 1440))
    assert region.to_pixels(1280, 720) == (320, 180, 640, 180)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def test_fill_prompt_substitutes_both_placeholders():
    got = fill_prompt("translate {source} into {target}", "English", "Japanese")
    assert got == "translate English into Japanese"


def test_fill_prompt_replaces_every_occurrence():
    got = fill_prompt("{source} to {target}: {source}", "English", "Japanese")
    assert got == "English to Japanese: English"


def test_fill_prompt_tolerates_stray_braces():
    """str.format would raise on a user prompt containing JSON or code."""
    got = fill_prompt('return {"a": 1} for {source} to {target}', "English", "Japanese")
    assert '"a": 1' in got and "English" in got


def test_fill_prompt_leaves_unknown_placeholders_alone():
    assert "{unknown}" in fill_prompt("keep {unknown}, {source}", "English", "Japanese")


def test_every_preset_uses_the_language_placeholders():
    for name, template in PROMPT_PRESETS.items():
        assert "{source}" in template, name
        assert "{target}" in template, name


def test_limbus_preset_names_the_game_and_its_terms():
    """The whole point of the preset is telling the model which game this is."""
    preset = PROMPT_PRESETS["Limbus Company"]
    assert "Limbus Company" in preset
    # The terms that the local model actually gets wrong.
    assert "Manager" in preset or "マネージャー" in preset
    assert "Sinner" in preset or "罪人" in preset


def test_default_prompt_asks_for_output_only():
    lowered = DEFAULT_PROMPT.lower()
    assert "only the translation" in lowered
    assert "bracket" in lowered
