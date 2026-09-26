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

from lintranslator.config import Config  # noqa: E402
from lintranslator.languages import LANGUAGES  # noqa: E402
from lintranslator import settings as settings_mod  # noqa: E402
from lintranslator.settings import (  # noqa: E402
    BACKENDS,
    DEFAULT_CT2_DIR,
    MODEL_LABELS,
    MODEL_SUGGESTIONS,
    REASONING_CAUTION,
    SettingsDialog,
)
from lintranslator.translate import DEFAULT_NLLB_MODEL  # noqa: E402


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


def test_the_prompt_row_is_only_for_the_backends_that_read_it(dialog):
    """ct2/local decode a language code, and DeepL has no prompt parameter.

    The field is only meaningful for the backends that send it, so showing it
    over a backend that ignores it is a control that silently does nothing -
    which is what the default backend (ct2) did on a fresh install.
    """
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.prompt_section.get_visible(), backend
    for backend in ("ct2", "local", "deepl", "none"):
        select(dialog, backend)
        assert not dialog.prompt_section.get_visible(), backend


def test_hiding_the_prompt_does_not_erase_a_stored_one():
    """Switching to a local backend and saving must keep the chat prompt.

    The field is hidden, not cleared: `translate.prompt` is still in the config,
    and a Save made while ct2 is selected must not drop it.
    """
    cfg = Config()
    cfg.translate.prompt = "keep Faust in Latin script"
    dlg = SettingsDialog(None, cfg)
    try:
        select(dlg, "ct2")
        dlg._on_save(_save_button(dlg))
        assert dlg.config.translate.prompt == "keep Faust in Latin script"
    finally:
        dlg.destroy()


def test_the_prompt_box_shows_the_builtin_default_rather_than_an_empty_box():
    """An empty field means "use the built-in default", so showing nothing would
    hide the prompt that is actually in force."""
    from lintranslator.geometry import DEFAULT_PROMPT

    dlg = SettingsDialog(None, Config())
    try:
        buffer = dlg.prompt_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert text == DEFAULT_PROMPT
    finally:
        dlg.destroy()


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
    dialog._refresh_model_hint()
    assert "Base URL" in dialog.model_hint.get_text()


def test_the_timeout_row_shows_where_a_socket_is_waited_on(dialog):
    """The key existed in config.json and had no row here.

    That is how a local model that was merely slow - 10.3 s for one line, measured
    on qwen3.5:4b through Ollama - got reported as "unreachable", with the one
    setting that would have fixed it unreachable too.
    """
    for backend in ("chat", "openai", "openrouter", "deepl", "google"):
        select(dialog, backend)
        assert dialog.timeout_row.get_visible(), backend
    for backend in ("none", "ct2", "local"):
        select(dialog, backend)
        assert not dialog.timeout_row.get_visible(), backend


def test_the_thinking_row_is_only_for_the_backends_that_send_a_chat_request(dialog):
    for backend in ("openrouter", "openai", "chat"):
        select(dialog, backend)
        assert dialog.thinking_row.get_visible(), backend
    for backend in ("none", "ct2", "local", "deepl", "google"):
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
    """Settings must show what the config holds, including a value it does not
    offer - the rule the language pickers already follow."""
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
    monkeypatch.delenv("LINTRANSLATOR_API_KEY", raising=False)
    dialog.key_entry.set_text("")
    select(dialog, "chat")
    # The row has to be there: a hosted gateway needs a key.
    assert dialog.key_row.get_visible()
    text = dialog.key_label.get_text()
    assert "No key needed" not in text
    assert "LINTRANSLATOR_API_KEY" in text


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


def test_the_hint_names_the_code_each_backend_decodes_with(dialog):
    """NLLB takes the FLORES code verbatim; DeepL wants an ISO code of its own.

    The chat backends are deliberately not named here: the pair reaches them as
    words inside the prompt, and that prompt is on screen for them. A line
    repeating it under the pickers was the same sentence twice.
    """
    select(dialog, "ct2")
    assert "eng_Latn → jpn_Jpan" in dialog.language_hint.get_text()

    select(dialog, "deepl")
    assert "EN → JA" in dialog.language_hint.get_text()

    # Google's code space is ISO 639-1 as well, and lowercase.
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
    """The hint has to name the code Google is actually sent.

    `zho_Hant` is the one language where that is not the table's ISO column: it
    carries "zh", and asking Google for "zh" returns Simplified.
    """
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
# The OCR languages: a set to choose, under the pair
# --------------------------------------------------------------------------- #
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
    """`eng+jpn` reads a Japanese line with Latin names in it; `jpn` does not.

    The OCR language is a separate setting from the translation language, so the
    two drift apart silently - and reading Japanese with `eng` produces confident
    nonsense rather than an error. The hint is text, not a button: the row for
    `jpn` is tagged in the list, so the fix is already one click away.
    """
    dialog.source_picker.set_code("jpn_Jpan")
    dialog._refresh_language()

    assert dialog.ocr_hint.get_visible()
    assert "tick jpn in the list above" in dialog.ocr_hint.get_text()

    _tick(dialog, "jpn")
    # Nothing left to say once it is there.
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


# -- the choice itself ------------------------------------------------------ #
def test_the_chooser_opens_on_what_the_config_says(dialog):
    """Otherwise the list would edit something other than what is in force."""
    dialog.config.ocr.langs = "eng+kor"
    dlg = SettingsDialog(None, dialog.config)
    try:
        assert dlg.ocr_picker.get_langs() == ["eng", "kor"]
        assert dlg.ocr_picker._label.get_text() == "English + Korean"
    finally:
        dlg.destroy()


def test_ticking_and_unticking_are_both_possible(dialog):
    """The whole point: the list could be grown but never shortened.

    Every model in it competes for every word, so an OCR list that has grown is
    slower and can lose a soft glyph to a script that is not on screen.
    """
    assert dialog.ocr_picker.get_langs() == ["eng"]

    _tick(dialog, "kor")
    assert dialog.ocr_picker.get_langs() == ["eng", "kor"]
    assert dialog._ocr_langs == "eng+kor"

    _tick(dialog, "eng", on=False)
    assert dialog._ocr_langs == "kor"

    dialog._on_save(_save_button(dialog))
    assert dialog.config.ocr.langs == "kor"


def test_the_choice_is_stored_in_one_order_whatever_order_it_was_ticked(dialog):
    """`eng+kor` and `kor+eng` give the same text and the same confidence.

    Order carries no meaning, so the value is written in the list's own order -
    otherwise the same selection would look different in the config depending on
    which box was clicked first.
    """
    _tick(dialog, "kor")
    first = dialog._ocr_langs
    _tick(dialog, "kor", on=False)
    _tick(dialog, "kor")
    assert dialog._ocr_langs == first == "eng+kor"


def test_the_source_language_is_marked_in_the_list(dialog):
    """"Which of these 124 do I tick" should have an answer on screen."""
    dialog.source_picker.set_code("kor_Hang")
    dialog._refresh_language()
    assert dialog.ocr_picker.NEEDED_TAG in _tags(dialog, "kor")
    assert dialog.ocr_picker.NEEDED_TAG not in _tags(dialog, "jpn")


def test_each_row_says_how_the_model_would_be_obtained(dialog, monkeypatch):
    """The state that is otherwise found by saving and watching OCR fail.

    `rus` has a pinned checksum so the app fetches it; `grc` ships with tesseract
    but the app will not download it, because it has no digest to check it
    against. Both look identical in a config file.
    """
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
    """A hand-added file, or a name from a newer tessdata revision.

    Dropping it would be the dialog editing the config by opening it, which is
    the one thing it must never do.
    """
    dialog.config.ocr.langs = "eng+zzz_handmade"
    dlg = SettingsDialog(None, dialog.config)
    try:
        assert dlg.ocr_picker.get_langs() == ["eng", "zzz_handmade"]
        assert _tags(dlg, "zzz_handmade") == ["not a known model"]

        # And it can be removed, which is what "control the list" has to mean.
        _tick(dlg, "zzz_handmade", on=False)
        dlg._on_save(_save_button(dlg))
        assert dlg.config.ocr.langs == "eng"
    finally:
        dlg.destroy()


# -- the advice under the choice -------------------------------------------- #
def test_the_hint_names_what_is_only_competing(dialog):
    """Three models over a Korean box: one of them is only competing.

    The source is already readable, so what is left to say is the cost of the
    extra model - the user untick it, which is a click in the list.
    """
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
    """`eng+kor` over Korean is a choice, not a mistake.

    A hint that fired here would be nagging: it is exactly the list the app
    recommends for a box with Latin names in it, and the old dialog hid the row
    for this case rather than warning about it.
    """
    for langs in ("eng+kor", "kor+eng"):
        dialog._set_ocr_langs(langs)
        dialog.source_picker.set_code("kor_Hang")
        dialog._refresh_language()
        assert not dialog.ocr_hint.get_visible(), langs


def test_the_hint_reads_correctly_for_every_shape_of_extra(dialog):
    """The first version said "kor, jpn competes", which is not a sentence."""
    dialog._set_ocr_langs("eng+jpn+kor")
    dialog.source_picker.set_code("kor_Hang")
    dialog._refresh_language()
    assert "jpn competes for every word" in dialog.ocr_hint.get_text()

    dialog.source_picker.set_code("eng_Latn")
    dialog._refresh_language()
    text = dialog.ocr_hint.get_text()
    assert "jpn and kor compete" in text, text


def test_an_empty_selection_is_not_saved_and_the_picker_is_put_back(dialog):
    """OCR with no language cannot run, so an empty set is not a setting.

    The picker is restored rather than left showing a selection that was never
    stored: a window that lies about what is in force is worse than one that
    refuses.
    """
    dialog.config.ocr.langs = "kor"
    dialog._set_ocr_langs("kor")
    _tick(dialog, "kor", on=False)

    dialog._on_save(_save_button(dialog))

    assert dialog.config.ocr.langs == "kor"
    assert dialog.ocr_picker.get_langs() == ["kor"], "the picker kept an empty selection"
    assert "No OCR language is ticked" in dialog.status.get_text()


def test_a_hand_edited_bad_name_is_reported_not_silently_dropped(dialog):
    """The value becomes a filename, so it is checked rather than trusted.

    A picker cannot produce one of these, but `config.json` and `--langs` can, and
    `parse_langs` refuses the name at read time anyway - so a save that accepted it
    would only move the failure somewhere less explicable. It is shown rather than
    dropped, because opening Settings must never edit the config by itself.
    """
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
    """The row is a label and a picker, and must not outgrow the dialog.

    The picker is the control the row exists for; a selection wide enough to push
    it off the edge would be a row nobody can use.
    """
    dialog._set_ocr_langs("eng+jpn+chi_sim+kor")
    dialog._refresh_language()
    label = dialog.ocr_picker._label.get_text()
    # Four names, the longest label the button can carry, in the order the
    # config holds them.
    assert label == "English + Japanese + Chinese (simplified) + Korean", label

    width = dialog.get_default_size().width
    row = dialog.ocr_row.measure(Gtk.Orientation.HORIZONTAL, -1)
    # 160 px is the label column of this grid plus its margins.
    assert row.minimum + 160 <= width, f"the OCR row needs {row.minimum}px of {width}px"


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


# --------------------------------------------------------------------------- #
# What this dialog deliberately does not edit
# --------------------------------------------------------------------------- #
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
    """The region belongs to the picker; this dialog is not a second editor.

    It used to carry a pixel readout with Smaller/Larger and 8 px move buttons -
    a correction made blind, since this window sits on neither the screen it
    reads nor the crop that comes back. The picker shows both, and
    `lintranslator region` sets the box without a GUI at all. Either half coming
    back here would leave two windows disagreeing about what gets OCR'd, so the
    controls and the value Save writes are both checked.
    """
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
