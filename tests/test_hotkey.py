"""The compositor-granted global hotkey: the parts that can be tested off-screen.

The portal round trip needs a compositor, but the failure mapping and the request
path prediction do not - and they are the parts that decide what the user is told
when binding does not work.
"""
from __future__ import annotations

from tlkun.hotkey import (
    REREAD_DESCRIPTION,
    REREAD_ID,
    REREAD_TRIGGER,
    GlobalHotkey,
)


def _hotkey() -> tuple[GlobalHotkey, list[tuple[bool, str]]]:
    notes: list[tuple[bool, str]] = []
    hotkey = GlobalHotkey(on_activated=lambda _id: None, on_status=lambda ok, d: notes.append((ok, d)))
    return hotkey, notes


def test_default_shortcut_is_the_reread_key():
    assert REREAD_ID == "reread"
    assert "R" in REREAD_TRIGGER and "CTRL" in REREAD_TRIGGER
    assert "re-read" in REREAD_DESCRIPTION.lower()


def test_an_app_id_refusal_is_explained_in_terms_a_user_can_act_on():
    """KDE answers `An app id is required` for a terminal launch; that string
    alone tells the user nothing about what to do."""
    hotkey, notes = _hotkey()
    hotkey._on_call_failed(
        "CreateSession: Error: GDBus.Error:org.freedesktop.portal.Error.NotAllowed: "
        "An app id is required (36)"
    )
    assert notes and notes[0][0] is False
    assert "application id" in notes[0][1]
    assert "menu" in notes[0][1], "the fix has to be named"


def test_other_failures_are_passed_through_unchanged():
    hotkey, notes = _hotkey()
    hotkey._on_call_failed("BindShortcuts: timeout")
    assert notes == [(False, "BindShortcuts: timeout")]


def test_an_unbound_hotkey_reports_false():
    hotkey, _ = _hotkey()
    assert hotkey.bound is False
    hotkey.close()  # must not raise with no session and no connection


def test_a_session_that_did_not_bind_times_out_with_an_answer():
    hotkey, notes = _hotkey()
    hotkey._on_timeout()
    assert notes == [(False, "no answer to the shortcut dialog")]
