"""Tests for prompt templating.

A user-written prompt with a stray brace in it must not raise at translate time,
and a preset that forgets a placeholder would silently ask the model to translate
"{source}" - both are pinned here.
"""
from __future__ import annotations

from lintranslator.geometry import (
    DEFAULT_PROMPT,
    PROMPT_PRESETS,
    fill_prompt,
)


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


def test_the_default_preset_is_the_builtin_default_and_is_named_for_it():
    """It is what an empty `translate.prompt` falls back to, not one choice among
    three: "Generic game dialogue" read as a genre option while actually being
    the prompt in force, and the Settings dialog pre-fills the box with it."""
    assert PROMPT_PRESETS["Default prompt"] == DEFAULT_PROMPT


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
