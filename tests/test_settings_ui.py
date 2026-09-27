"""Settings dialog behaviour: backend-dependent rows and model memory."""
from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
except (ImportError, ValueError):  # pragma: no cover - no GTK typelib
    pytest.skip("GTK 4 typelib unavailable", allow_module_level=True)

from lintranslator.config import Config  # noqa: E402
from lintranslator.languages import LANGUAGES  # noqa: E402
from lintranslator import settings as settings_mod  # noqa: E402
from lintranslator.settings import (  # noqa: E402
    BACKENDS,
    MODEL_LABELS,
    MODEL_SUGGESTIONS,
    REASONING_CAUTION,
    SettingsDialog,
)


@pytest.fixture(scope="module", autouse=True)
def gtk_init():
    if not Gtk.init_check():
        pytest.skip("no display available for GTK", allow_module_level=True)


@pytest.fixture(autouse=True)
def never_write_the_real_config(tmp_path, monkeypatch):
    """Redirect any default config write into tmp_path."""
    from lintranslator import config as config_mod

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


def test_only_this_backend_rows_are_visible(dialog):
    select(dialog, "chat")
    assert dialog.model_row.get_visible()
    assert dialog.base_row.get_visible()
    assert dialog.key_row.get_visible()
    assert not dialog.fetch_btn.get_visible()

    select(dialog, "openrouter")
    assert dialog.model_row.get_visible()
    assert dialog.key_row.get_visible()
    assert dialog.fetch_btn.get_visible()


def test_saving_writes_the_ocr_recipe(dialog):
    """The OCR-input rows are what OCR is handed, not display options."""
    dialog.ocr_invert.set_active(True)
    dialog.ocr_autocontrast.set_active(False)
    dialog.ocr_threshold.set_value(144)
    dialog._on_save(None)
    assert dialog.config.ocr.invert is True
    assert dialog.config.ocr.autocontrast is False
    assert dialog.config.ocr.threshold == 144


def test_the_cut_row_says_off_rather_than_zero():
    """0 is not a grey level, it is "no cut", and the field has to say so."""
    dlg = SettingsDialog(None, Config())
    try:
        assert dlg.ocr_threshold.get_text() == "off"
        dlg.ocr_threshold.set_value(160)
        assert dlg.ocr_threshold.get_text() == "160"
        dlg.ocr_threshold.set_value(0)
        assert dlg.ocr_threshold.get_text() == "off"
    finally:
        dlg.destroy()


def test_the_ocr_recipe_rows_are_shown_for_every_backend(dialog):
    """The recipe is about reading the screen, so no backend may hide it."""
    for backend in ("openrouter", "openai", "deepl", "chat", "none"):
        select(dialog, backend)
        assert dialog.ocr_input_row.get_visible(), backend


def test_a_hand_edited_cut_survives_the_dialog(tmp_path):
    """A hand-edited threshold string in config.json must not take the dialog down."""
    path = tmp_path / "config.json"
    path.write_text('{"ocr": {"threshold": "wide"}}')
    dlg = SettingsDialog(None, Config.load(path))
    try:
        assert dlg.ocr_threshold.get_value() == 0
        assert dlg.ocr_threshold.get_text() == "off"
    finally:
        dlg.destroy()


def test_model_row_hidden_for_backends_that_ignore_it(dialog):
    for backend in ("deepl", "none"):
        select(dialog, backend)
        assert not dialog.model_row.get_visible(), backend
        assert not dialog.fetch_btn.get_visible(), backend


def test_base_url_row_is_only_for_the_backends_that_dial_a_url(dialog):
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.base_row.get_visible(), backend
    for backend in ("deepl", "none"):
        select(dialog, backend)
        assert not dialog.base_row.get_visible(), backend


def test_the_custom_endpoint_row_says_which_protocol_it_wants(dialog):
    select(dialog, "chat")
    assert "localhost" in dialog.base_entry.get_placeholder_text()


def test_the_prompt_row_is_only_for_the_backends_that_read_it(dialog):
    """DeepL has no prompt parameter, and `none` sends nothing at all."""
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.prompt_section.get_visible(), backend
    for backend in ("deepl", "none"):
        select(dialog, backend)
        assert not dialog.prompt_section.get_visible(), backend


def test_hiding_the_prompt_does_not_erase_a_stored_one():
    """Switching to a backend that ignores the prompt and saving must keep it."""
    cfg = Config()
    cfg.translate.prompt = "keep Faust in Latin script"
    dlg = SettingsDialog(None, cfg)
    try:
        select(dlg, "deepl")
        dlg._on_save(_save_button(dlg))
        assert dlg.config.translate.prompt == "keep Faust in Latin script"
    finally:
        dlg.destroy()


def test_the_prompt_box_shows_the_builtin_default_rather_than_an_empty_box():
    """An empty field means "use the built-in default", so showing nothing would hide the prompt in force."""
    from lintranslator.geometry import DEFAULT_PROMPT

    dlg = SettingsDialog(None, Config())
    try:
        buffer = dlg.prompt_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert text == DEFAULT_PROMPT
    finally:
        dlg.destroy()


def test_a_stored_base_url_is_only_shown_to_the_backend_it_was_set_for(dialog):
    """Ollama's URL left in the field while OpenRouter is selected would be saved over the provider default."""
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
    dialog._refresh_model_hint()
    assert "Base URL" in dialog.model_hint.get_text()


def test_the_timeout_row_shows_where_a_socket_is_waited_on(dialog):
    """The timeout row is shown for the backends that wait on a socket."""
    for backend in ("chat", "openai", "openrouter", "deepl", "google"):
        select(dialog, backend)
        assert dialog.timeout_row.get_visible(), backend
    for backend in ("none",):
        select(dialog, backend)
        assert not dialog.timeout_row.get_visible(), backend


def test_the_thinking_row_is_only_for_the_backends_that_send_a_chat_request(dialog):
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.thinking_row.get_visible(), backend
    for backend in ("none", "deepl", "google"):
        select(dialog, backend)
        assert not dialog.thinking_row.get_visible(), backend


def test_saving_writes_the_timeout_and_the_thinking_choice(dialog):
    select(dialog, "chat")
    dialog.timeout_spin.set_value(300)
    dialog.thinking_dd.set_selected(dialog._thinking_values.index("none"))
    dialog._on_save(None)
    assert dialog.config.translate.timeout == 300.0
    assert dialog.config.translate.reasoning_effort == "none"


def test_a_hand_edited_value_survives_the_dialog(tmp_path):
    """Settings must show what the config holds, including a value it does not offer."""
    cfg = Config()
    cfg.translate.backend = "chat"
    cfg.translate.timeout = 45.0
    cfg.translate.reasoning_effort = "minimal"
    dlg = SettingsDialog(None, cfg)
    try:
        select(dlg, "chat")
        assert dlg.timeout_spin.get_value() == 45.0
        assert dlg._thinking_values[dlg.thinking_dd.get_selected()] == "minimal"
        dlg._on_save(None)
        assert dlg.config.translate.timeout == 45.0
        assert dlg.config.translate.reasoning_effort == "minimal"
    finally:
        dlg.destroy()


def test_no_row_claims_a_model_licence():
    """The dialog names no model licence, because it installs no model."""
    assert not hasattr(SettingsDialog, "licence_hint")
    with open(settings_mod.__file__, encoding="utf-8") as handle:
        assert "CC-BY-NC" not in handle.read()


def test_model_label_matches_backend(dialog):
    for backend, label in MODEL_LABELS.items():
        select(dialog, backend)
        assert dialog.model_label.get_text() == label


def test_switching_backend_does_not_leak_a_model_id(dialog):
    select(dialog, "chat")
    dialog.model_entry.set_text("hy-mt2:1.8b")

    select(dialog, "openrouter")
    assert dialog.model_entry.get_text() != "hy-mt2:1.8b"


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
    assert dialog.model_entry.get_text() == "tencent/hy-mt2-1.8b"


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
    text = dialog.key_label.get_text()
    assert "No key needed" not in text
    assert "DEEPL_API_KEY" in text


def test_backends_without_keys_say_so(dialog):
    for backend in ("none",):
        select(dialog, backend)
        assert dialog.key_label.get_text() == "No key needed for this backend."


def test_the_custom_endpoint_offers_a_key_without_demanding_one(dialog, monkeypatch):
    monkeypatch.delenv("LINTRANSLATOR_API_KEY", raising=False)
    dialog.key_entry.set_text("")
    select(dialog, "chat")
    assert dialog.key_row.get_visible()
    text = dialog.key_label.get_text()
    assert "No key needed" not in text
    assert "LINTRANSLATOR_API_KEY" in text


def test_the_key_field_belongs_to_one_backend_at_a_time(tmp_path):
    """A key typed for one backend must never be saved as another backend's."""
    cfg = Config()
    cfg.translate.backend = "deepl"
    cfg.translate.api_keys = {"deepl": "deepl-key", "openrouter": "or-key"}
    dlg = SettingsDialog(None, cfg)
    try:
        assert dlg.key_entry.get_text() == "deepl-key"
        select(dlg, "openrouter")
        assert dlg.key_entry.get_text() == "or-key"
        select(dlg, "google")
        assert dlg.key_entry.get_text() == "", "no key stored for this one"
    finally:
        dlg.destroy()


def test_a_key_typed_for_one_backend_is_not_saved_for_another(dialog):
    select(dialog, "deepl")
    dialog.key_entry.set_text("deepl-key")
    select(dialog, "openai")
    dialog.key_entry.set_text("openai-key")
    dialog._on_save(None)
    assert dialog.config.translate.api_keys == {
        "deepl": "deepl-key",
        "openai": "openai-key",
    }


def test_switching_away_and_back_keeps_a_key_that_is_not_saved_yet(dialog):
    select(dialog, "deepl")
    dialog.key_entry.set_text("typed-but-not-saved")
    select(dialog, "openai")
    assert dialog.key_entry.get_text() == ""
    select(dialog, "deepl")
    assert dialog.key_entry.get_text() == "typed-but-not-saved"


def test_a_backend_with_no_key_field_never_gets_one(dialog):
    select(dialog, "deepl")
    dialog.key_entry.set_text("deepl-key")
    # a hidden key field must not be where a key gets stored from
    select(dialog, "none")
    dialog._on_save(None)
    assert dialog.config.translate.api_keys == {"deepl": "deepl-key"}


def test_clearing_the_key_clears_only_this_backend(dialog):
    dialog.config.translate.api_keys = {"deepl": "d", "openrouter": "o"}
    select(dialog, "deepl")
    dialog._on_clear_key(None)
    assert dialog.config.translate.api_keys == {"openrouter": "o"}
    assert "deepl" in dialog.status.get_text()


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


def test_save_records_recent_models_and_weights_dir(tmp_path):
    # load from a real file so the save path is exercised end to end
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

        dlg.model_entry.set_text("google/gemini-2.5-flash-lite")
        dlg._on_save(Gtk.Button())
        assert cfg.translate.recent_models[:2] == [
            "google/gemini-2.5-flash-lite",
            "tencent/hy-mt2-1.8b",
        ]

        select(dlg, "deepl")
        dlg._on_save(Gtk.Button())
        # a backend whose field is not a model id must never pollute recent_models
        assert "DEEPL" not in "".join(cfg.translate.recent_models)

        reloaded = Config.load(cfg_path)
        assert reloaded.translate.recent_models[0] == "google/gemini-2.5-flash-lite"
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


def test_the_display_section_exposes_the_height_budget(dialog):
    """Both line budgets must be editable, bounded, and start at the config."""
    cfg = dialog.config
    assert dialog.target_lines.get_value() == cfg.display.target_lines
    assert dialog.source_lines.get_value() == cfg.display.source_lines
    # bounded below at one line: zero lines is not a card
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

        # the redirected default may point elsewhere, so look for the file under tmp_path
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


def test_hiding_the_source_says_below_not_above(dialog):
    """The checkbox label must say the original text sits below the translation."""
    assert "below the translation" in dialog.show_source.get_label()


def test_changing_a_line_budget_releases_a_dragged_height(dialog):
    """A line-budget change must clear a card height pinned by an edge drag."""
    dlg = dialog
    dlg.config.display.height = 400  # as if the card had been dragged
    dlg.target_lines.set_value(dlg.config.display.target_lines + 1)
    dlg._on_save(_save_button(dlg))
    assert dlg.config.display.height == 0

    # re-saving the same budgets must not discard a drag for no reason
    dlg.config.display.height = 400
    dlg.target_lines.set_value(dlg.config.display.target_lines)
    dlg.source_lines.set_value(dlg.config.display.source_lines)
    dlg._on_save(_save_button(dlg))
    assert dlg.config.display.height == 400


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


def test_the_picker_offers_only_codes_the_app_knows(dialog):
    """Free text is the whole problem: no backend can use a code it does not know."""
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

    row = dialog.target_picker._language_row(LANGUAGES["ceb_Latn"])
    assert "not supported" in _row_texts(row)


def test_choosing_a_language_updates_the_pair_and_the_hint(dialog):
    picker = dialog.source_picker
    picker.search.set_text("korean")
    picker.refresh()
    picker._activate_first()
    assert picker.get_code() == "kor_Hang"
    select(dialog, "google")
    assert "ko" in dialog.language_hint.get_text()


def test_the_hint_names_the_code_each_backend_decodes_with(dialog):
    """DeepL takes an ISO code of its own, and Google a lowercase one."""
    select(dialog, "deepl")
    assert "EN → JA" in dialog.language_hint.get_text()

    select(dialog, "google")
    assert "en → ja" in dialog.language_hint.get_text()

    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.language_hint.get_text() == "", backend


def test_the_hint_says_when_the_pair_is_unused(dialog):
    select(dialog, "none")
    assert "unused" in dialog.language_hint.get_text()


def test_deepl_warns_when_it_cannot_translate_the_target(dialog):
    dialog.target_picker.set_code("ceb_Latn")
    select(dialog, "deepl")
    hint = dialog.language_hint.get_text()
    assert "cannot translate into Cebuano" in hint
    assert "another backend" in hint


def test_google_gets_iso_codes_and_the_traditional_chinese_override(dialog):
    """The hint names the code Google is actually sent, including the zh-TW override for `zho_Hant`."""
    dialog.target_picker.set_code("zho_Hant")
    select(dialog, "google")
    hint = dialog.language_hint.get_text()
    assert "zh-TW" in hint
    assert "en → zh-TW" in hint


def test_google_warns_when_it_cannot_translate_the_target(dialog):
    dialog.target_picker.set_code("ceb_Latn")
    select(dialog, "google")
    hint = dialog.language_hint.get_text()
    assert "cannot translate into Cebuano" in hint
    assert "another backend" in hint


def test_an_unknown_code_is_shown_and_warned_about_never_replaced(dialog):
    """A hand-edited config must survive Settings being opened."""
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


def _row_for(dialog, stem: str) -> Gtk.ListBoxRow:
    """The chooser's row for a stem, or an assertion failure naming it."""
    listbox = dialog.ocr_picker.listbox
    index = 0
    while (row := listbox.get_row_at_index(index)) is not None:
        if getattr(row, "ocr_stem", None) == stem:
            return row
        index += 1
    raise AssertionError(f"no row for {stem!r} in the chooser")


def _tick(dialog, stem: str, on: bool = True) -> None:
    """Tick a language the way a click does: through its checkbox."""
    _row_for(dialog, stem).get_child().set_active(on)


def _tags(dialog, stem: str) -> list[str]:
    """The labels on a row after the name: the tags the chooser adds."""
    box = _row_for(dialog, stem).get_child().get_child()
    texts, child = [], box.get_first_child()
    while child is not None:
        texts.append(child.get_text() if hasattr(child, "get_text") else "")
        child = child.get_next_sibling()
    return texts[1:]


def test_the_hint_names_the_model_the_source_needs(dialog):
    """The hint names the OCR model the source language needs."""
    dialog.source_picker.set_code("jpn_Jpan")
    dialog._refresh_language()

    assert dialog.ocr_hint.get_visible()
    assert "tick jpn in the list above" in dialog.ocr_hint.get_text()

    _tick(dialog, "jpn")
    assert not dialog.ocr_hint.get_visible()

    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "eng+jpn"


def test_ocr_stays_quiet_when_it_already_reads_the_source(dialog):
    dialog._set_ocr_langs("eng+jpn")
    dialog.source_picker.set_code("jpn_Jpan")
    dialog._refresh_language()
    assert not dialog.ocr_hint.get_visible()


def test_ocr_is_silent_for_a_language_tesseract_cannot_read(dialog):
    """Nothing to point at, because there is no model to tick."""
    dialog.source_picker.set_code("zul_Latn")
    dialog._refresh_language()
    assert not dialog.ocr_hint.get_visible()


def test_saving_without_touching_ocr_leaves_it_alone(dialog):
    """A tuned `ocr.langs` must not be rewritten by an unrelated Save."""
    dialog._set_ocr_langs("eng+chi_sim")
    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "eng+chi_sim"


def test_the_chooser_opens_on_what_the_config_says(dialog):
    """The chooser opens on the language list the config holds."""
    dialog.config.ocr.langs = "eng+kor"
    dlg = SettingsDialog(None, dialog.config)
    try:
        assert dlg.ocr_picker.get_langs() == ["eng", "kor"]
        assert dlg.ocr_picker._label.get_text() == "English + Korean"
    finally:
        dlg.destroy()


def test_ticking_and_unticking_are_both_possible(dialog):
    """Both ticking and unticking an OCR language must be possible."""
    assert dialog.ocr_picker.get_langs() == ["eng"]

    _tick(dialog, "kor")
    assert dialog.ocr_picker.get_langs() == ["eng", "kor"]
    assert dialog._ocr_langs == "eng+kor"

    _tick(dialog, "eng", on=False)
    assert dialog._ocr_langs == "kor"

    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "kor"


def test_the_choice_is_stored_in_one_order_whatever_order_it_was_ticked(dialog):
    """The stored value is written in the list's own order, whatever order it was ticked in."""
    _tick(dialog, "kor")
    first = dialog._ocr_langs
    _tick(dialog, "kor", on=False)
    _tick(dialog, "kor")
    assert dialog._ocr_langs == first == "eng+kor"


def test_the_source_language_is_marked_in_the_list(dialog):
    """The row for the source language is marked in the list."""
    dialog.source_picker.set_code("kor_Hang")
    dialog._refresh_language()
    assert dialog.ocr_picker.NEEDED_TAG in _tags(dialog, "kor")
    assert dialog.ocr_picker.NEEDED_TAG not in _tags(dialog, "jpn")


def test_each_row_says_how_the_model_would_be_obtained(dialog, monkeypatch):
    """Each row says how its model would be obtained; only a model with a pinned checksum is downloaded."""
    monkeypatch.setattr(settings_mod, "installed_models", lambda *_: {"eng"})
    dialog._refresh_language()

    assert _tags(dialog, "eng") == ["this box's language", "ready"]
    assert _tags(dialog, "rus") == ["will download"]
    assert _tags(dialog, "grc") == ["not installed"]
    assert "tesseract-data-grc" in _row_for(dialog, "grc").get_child().get_tooltip_text()


def test_search_narrows_the_list(dialog):
    dialog.ocr_picker.search.set_text("korean")
    dialog.ocr_picker.refresh()
    stems = []
    index = 0
    while (row := dialog.ocr_picker.listbox.get_row_at_index(index)) is not None:
        stems.append(getattr(row, "ocr_stem", None))
        index += 1
    assert stems == ["kor", "kor_vert"]
    assert "2 of 124" in dialog.ocr_picker.count_label.get_text()


def test_a_stem_the_table_does_not_know_is_kept_and_shown(dialog):
    """A stem the table does not know is kept and shown, not dropped."""
    dialog.config.ocr.langs = "eng+zzz_handmade"
    dlg = SettingsDialog(None, dialog.config)
    try:
        assert dlg.ocr_picker.get_langs() == ["eng", "zzz_handmade"]
        assert _tags(dlg, "zzz_handmade") == ["not a known model"]

        _tick(dlg, "zzz_handmade", on=False)
        dlg._on_save(_save_button(dlg))
        assert dlg.config.ocr.langs == "eng"
    finally:
        dlg.destroy()


def test_the_hint_names_what_is_only_competing(dialog):
    """The hint names the OCR models that are only competing for the box."""
    dialog._set_ocr_langs("eng+jpn+kor")
    dialog.source_picker.set_code("kor_Hang")
    dialog._refresh_language()

    assert dialog.ocr_hint.get_visible()
    assert "jpn competes for every word" in dialog.ocr_hint.get_text()
    assert "Untick it if nothing in the box is written in it" in dialog.ocr_hint.get_text()

    _tick(dialog, "jpn", on=False)
    assert not dialog.ocr_hint.get_visible()
    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "eng+kor"


def test_a_list_that_already_reads_the_box_is_left_alone(dialog):
    """A list that already reads the box is left alone."""
    for langs in ("eng+kor", "kor+eng"):
        dialog._set_ocr_langs(langs)
        dialog.source_picker.set_code("kor_Hang")
        dialog._refresh_language()
        assert not dialog.ocr_hint.get_visible(), langs


def test_the_hint_reads_correctly_for_every_shape_of_extra(dialog):
    """The hint reads correctly for every shape of extra OCR model."""
    dialog._set_ocr_langs("eng+jpn+kor")
    dialog.source_picker.set_code("kor_Hang")
    dialog._refresh_language()
    assert "jpn competes for every word" in dialog.ocr_hint.get_text()

    dialog.source_picker.set_code("eng_Latn")
    dialog._refresh_language()
    text = dialog.ocr_hint.get_text()
    assert "jpn and kor compete" in text, text


def test_an_empty_selection_is_not_saved_and_the_picker_is_put_back(dialog):
    """OCR with no language cannot run, so an empty set is not a setting."""
    dialog.config.ocr.langs = "kor"
    dialog._set_ocr_langs("kor")
    _tick(dialog, "kor", on=False)

    dialog._on_save(_save_button(dialog))

    assert dialog.config.ocr.langs == "kor"
    assert dialog.ocr_picker.get_langs() == ["kor"], "the picker kept an empty selection"
    assert "No OCR language is ticked" in dialog.status.get_text()


def test_a_hand_edited_bad_name_is_reported_not_silently_dropped(dialog):
    """The value becomes a filename, so it is checked rather than trusted."""
    dialog.config.ocr.langs = "kor+../passwd"
    dlg = SettingsDialog(None, dialog.config)
    try:
        assert dlg.ocr_picker.get_langs() == ["kor", "../passwd"]
        assert "not a valid tesseract language name" in dlg.ocr_hint.get_text()

        dlg._on_save(_save_button(dlg))

        assert dlg.config.ocr.langs == "kor+../passwd"
        assert "not a valid tesseract language name" in dlg.status.get_text()
    finally:
        dlg.destroy()


def test_the_ocr_row_fits_the_dialog(dialog):
    """The row is a label and a picker, and must not outgrow the dialog."""
    dialog._set_ocr_langs("eng+jpn+chi_sim+kor")
    dialog._refresh_language()
    label = dialog.ocr_picker._label.get_text()
    assert label == "English + Japanese + Chinese (simplified) + Korean", label

    width = dialog.get_default_size().width
    row = dialog.ocr_row.measure(Gtk.Orientation.HORIZONTAL, -1)
    # 160 px is the label column of this grid plus its margins.
    assert row.minimum + 160 <= width, f"the OCR row needs {row.minimum}px of {width}px"


DISPLAY_SLIDERS = ("font_scale", "width_scale", "target_lines", "source_lines")


def _scroll_controllers(widget):
    return [
        controller
        for controller in widget.observe_controllers()
        if isinstance(controller, Gtk.EventControllerScroll)
    ]


@pytest.mark.parametrize("name", DISPLAY_SLIDERS)
def test_a_display_slider_has_a_capture_phase_wheel_guard(dialog, name):
    """The wheel guard must be in the capture phase, ahead of the scale's own bubble-phase handling."""
    slider = getattr(dialog, name)
    phases = [c.get_propagation_phase() for c in _scroll_controllers(slider)]
    assert Gtk.PropagationPhase.CAPTURE in phases, f"{name} has no wheel guard"
    # the scale's own handler must stay bubble-phase, or the guard races it
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


def _labels(widget):
    """Every label inside `widget`, in no particular order."""
    stack = [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label):
            yield node.get_label() or ""
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def test_the_dialog_offers_no_way_to_edit_the_capture_region(dialog):
    """The region belongs to the picker; this dialog is not a second editor."""
    labels = list(_labels(dialog))
    assert not [text for text in labels if text.startswith("Capture area")], labels
    assert not [text for text in labels if text.startswith(("Smaller ", "Larger "))], labels

    region = dialog.config.capture.region
    before = (region.x, region.y, region.w, region.h, region.mode)
    dialog._on_save(_save_button(dialog))
    region = dialog.config.capture.region
    assert (region.x, region.y, region.w, region.h, region.mode) == before, (
        "Save moved the capture region"
    )


def test_saving_writes_the_confidence_gate(dialog):
    """Save writes the confidence gate; a read below it is dropped."""
    dialog.ocr_confidence.set_value(70)
    dialog._on_save(None)
    assert dialog.config.ocr.min_confidence == 70.0


def test_saving_leaves_the_layout_alone(tmp_path):
    """`ocr.psm` has no row on purpose, so Save must not invent one."""
    path = tmp_path / "config.json"
    path.write_text('{"ocr": {"psm": 7}}')
    dlg = SettingsDialog(None, Config.load(path))
    try:
        assert not hasattr(dlg, "ocr_psm"), "the layout row came back"
        dlg._on_save(None)
        assert dlg.config.ocr.psm == 7
    finally:
        dlg.destroy()
