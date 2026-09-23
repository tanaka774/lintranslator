"""Settings dialog behaviour: backend-dependent rows and model memory.

These cover the two ways the dialog used to mislead: it showed every row at once
regardless of backend, and it carried one backend's Model value into another's
(so switching Local -> OpenRouter left an NLLB repo in the model id field, which
only fails later, at request time, as "model not found").

Requires a GTK display. Skips cleanly when there is none, so the suite still
runs in a headless container.
"""
from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
except (ImportError, ValueError):  # pragma: no cover - no GTK typelib
    pytest.skip("GTK 4 typelib unavailable", allow_module_level=True)

from tlkun.config import Config  # noqa: E402
from tlkun.languages import LANGUAGES  # noqa: E402
from tlkun.settings import (  # noqa: E402
    BACKENDS,
    DEFAULT_CT2_DIR,
    MODEL_LABELS,
    MODEL_SUGGESTIONS,
    REASONING_CAUTION,
    SettingsDialog,
)
from tlkun.translate import DEFAULT_NLLB_MODEL  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gtk_init():
    if not Gtk.init_check():
        pytest.skip("no display available for GTK", allow_module_level=True)


@pytest.fixture(autouse=True)
def never_write_the_real_config(tmp_path, monkeypatch):
    """Redirect any default config write into tmp_path.

    `_on_save` calls `config.save()` with no path. A dialog built from a default
    `Config()` therefore used to overwrite the developer's real config.json -
    including wiping their API key - which is exactly what happened once. Any
    test here that saves must not be able to touch the repository file.
    """
    from tlkun import config as config_mod

    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")


@pytest.fixture
def dialog(tmp_path):
    cfg = Config()
    cfg.translate.backend = "openrouter"
    cfg.translate.model = "tencent/hy-mt2-1.8b"
    dlg = SettingsDialog(None, cfg)
    yield dlg
    dlg.destroy()


def select(dlg: SettingsDialog, backend: str) -> None:
    idx = next(i for i, (key, _) in enumerate(BACKENDS) if key == backend)
    dlg.backend_dd.set_selected(idx)


# -- backend-dependent rows ------------------------------------------------- #
def test_only_this_backend_rows_are_visible(dialog):
    # ct2 reads a model dir and a tokenizer repo, and needs no key.
    select(dialog, "ct2")
    assert dialog.model_row.get_visible()
    assert dialog.ct2_row.get_visible()
    assert not dialog.key_row.get_visible()
    assert not dialog.fetch_btn.get_visible()

    # openrouter reads a model id and a key, and has no weights dir.
    select(dialog, "openrouter")
    assert dialog.model_row.get_visible()
    assert not dialog.ct2_row.get_visible()
    assert dialog.key_row.get_visible()
    assert dialog.fetch_btn.get_visible()


def test_model_row_hidden_for_backends_that_ignore_it(dialog):
    for backend in ("deepl", "none"):
        select(dialog, backend)
        assert not dialog.model_row.get_visible(), backend
        assert not dialog.fetch_btn.get_visible(), backend


def test_base_url_row_is_only_for_the_backends_that_dial_a_url(dialog):
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.base_row.get_visible(), backend
    for backend in ("ct2", "local", "deepl", "none"):
        select(dialog, backend)
        assert not dialog.base_row.get_visible(), backend


def test_the_custom_endpoint_row_says_which_protocol_it_wants(dialog):
    select(dialog, "chat")
    assert "localhost" in dialog.base_entry.get_placeholder_text()


def test_a_stored_base_url_is_only_shown_to_the_backend_it_was_set_for(dialog):
    """Ollama's URL left in the field while OpenRouter is selected would be
    saved over the provider default."""
    dialog.config.translate.backend = "chat"
    dialog.config.translate.api_base = "http://localhost:11434/v1"
    select(dialog, "chat")
    assert dialog.base_entry.get_text() == "http://localhost:11434/v1"
    select(dialog, "openrouter")
    assert dialog.base_entry.get_text() == ""


def test_saving_the_custom_endpoint_writes_the_base_url(dialog):
    select(dialog, "chat")
    dialog.base_entry.set_text("http://localhost:8080/v1")
    dialog.model_entry.set_text("qwen2.5-7b")
    dialog._on_save(None)
    assert dialog.config.translate.backend == "chat"
    assert dialog.config.translate.api_base == "http://localhost:8080/v1"


def test_the_custom_endpoint_warns_while_it_has_no_url(dialog):
    select(dialog, "chat")
    dialog.base_entry.set_text("")
    dialog._refresh_language()
    assert "Base URL" in dialog.language_hint.get_text()


def test_the_local_backend_states_the_model_licence(dialog):
    """NLLB is CC-BY-NC-4.0: non-commercial, and worth saying where the backend
    is chosen rather than only in the README."""
    for backend in ("ct2", "local"):
        select(dialog, backend)
        assert dialog.licence_hint.get_visible(), backend
        assert "CC-BY-NC-4.0" in dialog.licence_hint.get_text(), backend
    select(dialog, "openrouter")
    assert not dialog.licence_hint.get_visible()


def test_model_label_matches_backend(dialog):
    for backend, label in MODEL_LABELS.items():
        select(dialog, backend)
        assert dialog.model_label.get_text() == label


# -- model memory --------------------------------------------------------- #
def test_switching_backend_does_not_leak_nllb_repo_into_model_id(dialog):
    select(dialog, "local")
    dialog.model_entry.set_text(DEFAULT_NLLB_MODEL)

    select(dialog, "openrouter")
    # The bug: the NLLB repo stayed in the field and was sent as a model id.
    assert dialog.model_entry.get_text() != DEFAULT_NLLB_MODEL


def test_each_backend_remembers_its_own_model(dialog):
    select(dialog, "openrouter")
    dialog.model_entry.set_text("tencent/hy-mt2-1.8b")

    select(dialog, "openai")
    dialog.model_entry.set_text("gpt-4o-mini")

    select(dialog, "openrouter")
    assert dialog.model_entry.get_text() == "tencent/hy-mt2-1.8b"

    select(dialog, "openai")
    assert dialog.model_entry.get_text() == "gpt-4o-mini"


def test_configured_model_is_shown_for_its_own_backend(dialog):
    # config says openrouter + hy-mt2, and the dialog opens on that backend.
    assert dialog.model_entry.get_text() == "tencent/hy-mt2-1.8b"


# -- warnings -------------------------------------------------------------- #
def test_ct2_warns_when_weights_are_missing(dialog):
    select(dialog, "ct2")
    dialog.ct2_entry.set_text("/nonexistent/ct2/dir")
    dialog._refresh_model_hint()
    assert "no converted weights" in dialog.model_hint.get_text()

    # The real project dir does exist, so the warning must not appear.
    dialog.ct2_entry.set_text(DEFAULT_CT2_DIR)
    dialog._refresh_model_hint()
    assert "no converted weights" not in dialog.model_hint.get_text()


def test_reasoning_model_gets_a_caution(dialog):
    select(dialog, "openrouter")
    cautious = sorted(REASONING_CAUTION)[0]
    dialog.model_entry.set_text(cautious)
    assert "hidden reasoning" in dialog.model_hint.get_text()


def test_deepl_asks_for_a_key_rather_than_denying_one_is_needed(dialog, monkeypatch):
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    dialog.key_entry.set_text("")
    select(dialog, "deepl")
    assert dialog.key_row.get_visible()
    # The bug: the row was shown while the label said no key was needed.
    text = dialog.key_label.get_text()
    assert "No key needed" not in text
    assert "DEEPL_API_KEY" in text


def test_backends_without_keys_say_so(dialog):
    for backend in ("none", "ct2", "local"):
        select(dialog, backend)
        assert dialog.key_label.get_text() == "No key needed for this backend."


def test_the_custom_endpoint_offers_a_key_without_demanding_one(dialog, monkeypatch):
    monkeypatch.delenv("TLKUN_API_KEY", raising=False)
    dialog.key_entry.set_text("")
    select(dialog, "chat")
    # The row has to be there: a hosted gateway needs a key.
    assert dialog.key_row.get_visible()
    text = dialog.key_label.get_text()
    assert "No key needed" not in text
    assert "TLKUN_API_KEY" in text


# -- picker ---------------------------------------------------------------- #
def test_picker_filters_and_reports_counts(dialog):
    select(dialog, "openrouter")
    picker = dialog.model_picker
    picker.set_models(MODEL_SUGGESTIONS["openrouter"] + ["some/other-model"])

    def model_rows() -> int:
        n, child = 0, picker.listbox.get_first_child()
        while child is not None:
            n += 1 if getattr(child, "model_id", None) else 0
            child = child.get_next_sibling()
        return n

    total = model_rows()
    assert total == len(set(MODEL_SUGGESTIONS["openrouter"] + ["some/other-model"]))

    picker.search.set_text("hy-mt2")
    picker.refresh()
    assert 0 < model_rows() < total
    assert "match" in picker.count_label.get_text()

    picker.search.set_text("zzz-nothing-matches")
    picker.refresh()
    assert model_rows() == 0


def test_picker_enter_selects_top_match(dialog):
    select(dialog, "openrouter")
    dialog.model_picker.set_models(MODEL_SUGGESTIONS["openrouter"])
    dialog.model_picker.search.set_text("hy-mt2")
    dialog.model_picker.refresh()
    dialog.model_picker._activate_first()
    assert dialog.model_entry.get_text() == "tencent/hy-mt2-1.8b"


# -- save ------------------------------------------------------------------ #
def test_save_records_recent_models_and_weights_dir(tmp_path):
    # Load from a real (temp) file so the save path is exercised end to end
    # rather than only mutating an in-memory Config.
    cfg_path = tmp_path / "config.json"
    cfg = Config()
    cfg.save(cfg_path)
    cfg = Config.load(cfg_path)
    cfg.translate.backend = "openrouter"
    cfg.translate.recent_models = []

    dlg = SettingsDialog(None, cfg)
    try:
        select(dlg, "openrouter")
        dlg.model_entry.set_text("tencent/hy-mt2-1.8b")
        dlg._on_save(Gtk.Button())
        assert cfg.translate.recent_models[0] == "tencent/hy-mt2-1.8b"

        # A second, different model goes to the front and does not duplicate.
        dlg.model_entry.set_text("google/gemini-2.5-flash-lite")
        dlg._on_save(Gtk.Button())
        assert cfg.translate.recent_models[:2] == [
            "google/gemini-2.5-flash-lite",
            "tencent/hy-mt2-1.8b",
        ]

        select(dlg, "ct2")
        dlg.ct2_entry.set_text("/tmp/some-ct2-dir")
        dlg._on_save(Gtk.Button())
        assert cfg.translate.ct2_model_dir == "/tmp/some-ct2-dir"
        # An NLLB repo must never pollute recent_models.
        assert DEFAULT_NLLB_MODEL not in cfg.translate.recent_models

        # The writes actually reached the file, and `path` is not persisted.
        reloaded = Config.load(cfg_path)
        assert reloaded.translate.recent_models[0] == "google/gemini-2.5-flash-lite"
        assert reloaded.translate.ct2_model_dir == "/tmp/some-ct2-dir"
        assert "path" not in reloaded.__dict__ or reloaded.path == cfg_path
        assert not reloaded.warnings, reloaded.warnings
    finally:
        dlg.destroy()


def test_load_records_its_path_so_save_writes_back_there(tmp_path):
    target = tmp_path / "elsewhere.json"
    cfg = Config.load(target)  # does not exist yet
    cfg.display.width = 700
    written = cfg.save()
    assert written == target
    assert Config.load(target).display.width == 700


# --------------------------------------------------------------------------- #
# The card's height budget
# --------------------------------------------------------------------------- #
def test_the_display_section_exposes_the_height_budget(dialog):
    """Both line budgets must be editable, bounded, and start at the config."""
    cfg = dialog.config
    assert dialog.target_lines.get_value() == cfg.display.target_lines
    assert dialog.source_lines.get_value() == cfg.display.source_lines
    # Bounded below at one line: zero lines is not a card, it is a bug report.
    assert dialog.target_lines.get_adjustment().get_lower() >= 1
    assert dialog.source_lines.get_adjustment().get_lower() >= 1
    assert dialog.target_lines.get_adjustment().get_upper() > cfg.display.target_lines


def test_the_line_budgets_round_trip_through_save_and_reload(dialog, tmp_path):
    """A taller budget must survive a save, or Settings silently undoes itself."""
    dlg = dialog
    try:
        dlg.target_lines.set_value(5)
        dlg.source_lines.set_value(3)
        dlg.show_source.set_active(False)
        dlg._on_save(_save_button(dlg))

        # Wherever the redirected default points (`tests/conftest.py` owns the
        # layout now), the save must have landed somewhere under tmp_path.
        written = list(tmp_path.rglob("*.json"))
        assert written, "save wrote nothing"
        reloaded = Config.load(written[0])
        assert reloaded.display.target_lines == 5
        assert reloaded.display.source_lines == 3
        assert reloaded.display.show_source is False
    finally:
        dlg.destroy()


def _save_button(dialog):
    """The dialog's Save button, found by label rather than by attribute."""
    stack = [dialog]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and "Save" in (widget.get_label() or ""):
            return widget
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError("no Save button found in the settings dialog")


def test_the_preview_says_text_scrolls_rather_than_resizing(dialog):
    """The one thing a fixed-height card must explain, or text looks cut off."""
    dlg = dialog
    dlg.target_lines.set_value(4)
    dlg.source_lines.set_value(1)
    dlg.show_source.set_active(True)
    dlg._preview_display()
    text = dlg.display_preview.get_text()
    assert "4 lines" in text
    assert "1 line" in text and "1 lines" not in text
    assert "scrolls" in text

    dlg.show_source.set_active(False)
    dlg._preview_display()
    assert "original" not in dlg.display_preview.get_text()


def test_hiding_the_source_says_below_not_above(dialog):
    """The original text moved below the translation; the checkbox must agree."""
    assert "below the translation" in dialog.show_source.get_label()


def test_changing_a_line_budget_releases_a_dragged_height(dialog):
    """Otherwise the two budget sliders look broken once an edge was dragged.

    Dragging an edge pins the card's height; the line budgets are the rule it
    was pinned *against*. Re-stating that rule has to win, or the sliders appear
    to do nothing after the first drag.
    """
    dlg = dialog
    dlg.config.display.height = 400  # as if the card had been dragged
    dlg.target_lines.set_value(dlg.config.display.target_lines + 1)
    dlg._on_save(_save_button(dlg))
    assert dlg.config.display.height == 0

    # Re-saving the same budgets must not discard a drag for no reason.
    dlg.config.display.height = 400
    dlg.target_lines.set_value(dlg.config.display.target_lines)
    dlg.source_lines.set_value(dlg.config.display.source_lines)
    dlg._on_save(_save_button(dlg))
    assert dlg.config.display.height == 400


def test_the_preview_explains_a_dragged_height(dialog):
    """A pinned height has to be visible, and so does what undoes it."""
    dlg = dialog
    dlg.config.display.height = 0
    dlg._preview_display()
    assert "as dragged" not in dlg.display_preview.get_text()

    dlg.config.display.height = 380
    dlg._preview_display()
    text = dlg.display_preview.get_text()
    assert "380 px tall, as dragged" in text
    assert "line budget" in text, "the way back to budget sizing has to be named"


# --------------------------------------------------------------------------- #
# The language pair
# --------------------------------------------------------------------------- #
def _picker_codes(picker) -> list[str]:
    codes = []
    row = picker.listbox.get_row_at_index(0)
    while row is not None:
        code = getattr(row, "lang_code", None)
        if code:
            codes.append(code)
        row = row.get_next_sibling()
    return codes


def _row_texts(row) -> list[str]:
    texts, stack = [], [row]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label):
            texts.append(widget.get_text())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return texts


def test_the_language_rows_start_at_the_configured_pair(dialog):
    """Opening Settings must show the config, not a default."""
    assert dialog.source_picker.get_code() == dialog.config.translate.source_lang
    assert dialog.target_picker.get_code() == dialog.config.translate.target_lang


def test_the_picker_offers_only_codes_the_model_can_score(dialog):
    """Free text is the whole problem: NLLB scores a typo as `<unk>`."""
    picker = dialog.target_picker
    picker.search.set_text("jpn")
    picker.refresh()
    assert _picker_codes(picker) == ["jpn_Jpan"]

    picker.search.set_text("korean")
    picker.refresh()
    assert _picker_codes(picker) == ["kor_Hang"]

    picker.search.set_text("klingon")
    picker.refresh()
    assert _picker_codes(picker) == []


def test_the_picker_row_says_what_the_backend_would_be_sent(dialog):
    select(dialog, "deepl")
    row = dialog.target_picker._language_row(LANGUAGES["jpn_Jpan"])
    assert "JA" in _row_texts(row)

    # A language DeepL does not have says so while it is being chosen, rather
    # than as an HTTP 400 after Save.
    row = dialog.target_picker._language_row(LANGUAGES["ceb_Latn"])
    assert "not supported" in _row_texts(row)


def test_choosing_a_language_updates_the_pair_and_the_hint(dialog):
    picker = dialog.source_picker
    picker.search.set_text("korean")
    picker.refresh()
    picker._activate_first()
    assert picker.get_code() == "kor_Hang"
    # The hint follows the picker, in the form the selected backend reads.
    select(dialog, "ct2")
    assert "kor_Hang" in dialog.language_hint.get_text()


def test_the_hint_names_what_each_backend_receives(dialog):
    """The same pair is a code, a name and an ISO code depending on backend."""
    select(dialog, "ct2")
    assert "eng_Latn → jpn_Jpan" in dialog.language_hint.get_text()

    select(dialog, "openrouter")
    hint = dialog.language_hint.get_text()
    assert '"English"' in hint and '"Japanese"' in hint

    select(dialog, "deepl")
    assert "EN → JA" in dialog.language_hint.get_text()

    select(dialog, "chat")
    dialog.base_entry.set_text("http://localhost:11434/v1")
    dialog._refresh_language()
    hint = dialog.language_hint.get_text()
    assert '"English"' in hint and '"Japanese"' in hint
    assert "localhost:11434" in hint


def test_the_hint_says_when_the_pair_is_unused(dialog):
    select(dialog, "none")
    assert "unused" in dialog.language_hint.get_text()


def test_deepl_warns_when_it_cannot_translate_the_target(dialog):
    dialog.target_picker.set_code("ceb_Latn")
    select(dialog, "deepl")
    hint = dialog.language_hint.get_text()
    assert "cannot translate into Cebuano" in hint
    assert "another backend" in hint


def test_an_unknown_code_is_shown_and_warned_about_never_replaced(dialog):
    """A hand-edited config must survive Settings being opened.

    Silently rewriting the value would make the dialog the thing that changed the
    language, and the failure it hides - garbage output from an `<unk>` target -
    would look like a bad model.
    """
    dialog.target_picker.set_code("Japanese")
    dialog._refresh_language()
    assert dialog.target_picker.get_code() == "Japanese"
    assert "not a FLORES-200 code" in dialog.language_hint.get_text()

    dialog._on_save(_save_button(dialog))
    assert dialog.config.translate.target_lang == "Japanese"


def test_the_pair_round_trips_through_save_and_reload(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = Config()
    cfg.save(cfg_path)
    cfg = Config.load(cfg_path)

    dlg = SettingsDialog(None, cfg)
    try:
        dlg.source_picker.set_code("jpn_Jpan")
        dlg.target_picker.set_code("eng_Latn")
        dlg._on_save(_save_button(dlg))
    finally:
        dlg.destroy()

    reloaded = Config.load(cfg_path)
    assert reloaded.translate.source_lang == "jpn_Jpan"
    assert reloaded.translate.target_lang == "eng_Latn"


def test_swap_exchanges_the_pair(dialog):
    dialog.source_picker.set_code("eng_Latn")
    dialog.target_picker.set_code("kor_Hang")
    dialog._on_swap_languages()
    assert dialog.source_picker.get_code() == "kor_Hang"
    assert dialog.target_picker.get_code() == "eng_Latn"


# --------------------------------------------------------------------------- #
# The OCR language under the pair
# --------------------------------------------------------------------------- #
def test_ocr_offers_the_source_language_and_is_additive(dialog):
    """`eng+jpn` reads a Japanese line with Latin names in it; `jpn` does not.

    The OCR language is a separate setting from the translation language, so the
    two drift apart silently - and reading Japanese with `eng` produces confident
    nonsense rather than an error.
    """
    dialog._ocr_langs = "eng"
    dialog.source_picker.set_code("jpn_Jpan")
    dialog._refresh_language()

    assert dialog.ocr_row.get_visible()
    assert dialog.ocr_sync_btn.get_label() == "Use eng+jpn"
    assert "tesseract needs jpn" in dialog.ocr_hint.get_text()

    dialog._on_ocr_sync()
    assert dialog._ocr_langs == "eng+jpn"
    # Nothing left to offer once it is there.
    assert not dialog.ocr_row.get_visible()

    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "eng+jpn"


def test_ocr_stays_quiet_when_it_already_reads_the_source(dialog):
    dialog._ocr_langs = "eng+jpn"
    dialog.source_picker.set_code("jpn_Jpan")
    dialog._refresh_language()
    assert not dialog.ocr_row.get_visible()


def test_ocr_is_silent_for_a_language_tesseract_cannot_read(dialog):
    """No button, because there is no file to add."""
    dialog._ocr_langs = "eng"
    dialog.source_picker.set_code("zul_Latn")
    dialog._refresh_language()
    assert not dialog.ocr_row.get_visible()


def test_saving_without_touching_ocr_leaves_it_alone(dialog):
    """A tuned `ocr.langs` must not be rewritten by an unrelated Save."""
    dialog.config.ocr.langs = "eng+chi_sim"
    dialog._ocr_langs = "eng+chi_sim"
    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "eng+chi_sim"


# --------------------------------------------------------------------------- #
# The wheel must not set these values
# --------------------------------------------------------------------------- #
DISPLAY_SLIDERS = ("font_scale", "width_scale", "target_lines", "source_lines")


def _scroll_controllers(widget):
    return [
        controller
        for controller in widget.observe_controllers()
        if isinstance(controller, Gtk.EventControllerScroll)
    ]


@pytest.mark.parametrize("name", DISPLAY_SLIDERS)
def test_a_display_slider_has_a_capture_phase_wheel_guard(dialog, name):
    """The dialog is a scrolling column of sliders, so the wheel is a hazard.

    Measured before this: one wheel notch over the font-size slider took it from
    1.2 to 0.7 - half the range, from a gesture the user aimed at the *dialog*.
    The guard has to be in the capture phase, which runs on the way down the
    widget tree, ahead of the scale's own bubble-phase handling.
    """
    slider = getattr(dialog, name)
    phases = [c.get_propagation_phase() for c in _scroll_controllers(slider)]
    assert Gtk.PropagationPhase.CAPTURE in phases, f"{name} has no wheel guard"
    # The scale's own handler must still be the bubble-phase one, or the guard
    # would be racing it rather than running ahead of it.
    assert Gtk.PropagationPhase.BUBBLE in phases, f"{name} lost its own handling"
    assert phases.count(Gtk.PropagationPhase.CAPTURE) == 1, "guarded twice"


def test_the_wheel_over_a_slider_scrolls_the_dialog_instead(dialog):
    """Swallowing the event outright would freeze the dialog under the pointer."""
    dlg = dialog
    adjustment = dlg.outer_scroller.get_vadjustment()
    adjustment.set_upper(max(adjustment.get_upper(), 2000.0))
    adjustment.set_value(0.0)

    guard = next(
        c
        for c in _scroll_controllers(dlg.width_scale)
        if c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
    )
    value_before = dlg.width_scale.get_value()

    assert dlg._scroll_the_dialog_instead(guard, 0.0, 1.0) is True
    assert adjustment.get_value() > 0.0, "the dialog did not scroll"
    assert dlg.width_scale.get_value() == value_before, "the slider moved"


def test_the_guard_does_not_scroll_past_the_top(dialog):
    """A wheel-up at the top must clamp, not drive the adjustment negative."""
    dlg = dialog
    adjustment = dlg.outer_scroller.get_vadjustment()
    adjustment.set_value(0.0)

    guard = next(
        c
        for c in _scroll_controllers(dlg.font_scale)
        if c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
    )
    dlg._scroll_the_dialog_instead(guard, 0.0, -1.0)
    assert adjustment.get_value() == 0.0


def test_things_that_are_meant_to_scroll_still_do(dialog):
    """The guard is for value widgets, not for panes the user reads."""
    prompt = dialog.prompt_view
    phases = [c.get_propagation_phase() for c in _scroll_controllers(prompt)]
    assert Gtk.PropagationPhase.CAPTURE not in phases, (
        "the prompt editor was guarded; it is meant to scroll"
    )
