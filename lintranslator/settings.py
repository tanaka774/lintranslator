"""Settings dialog: backend, model, language pair, prompt, and the display area.

The capture area is not here. The region belongs to the region picker, which is
the only place it can be drawn and the only place the crop it produces is visible:
nudging a box by pixels in a dialog that shows neither the screen nor the text it
reads is a correction made blind. The display area - the card's font size, width
and line budgets - stays, because nothing else sets those.

The language pair is here rather than in `config.json` only because the codes are
not interchangeable strings: each backend is given a different form of the same
language, and a code NLLB does not know is scored as `<unk>` instead of raising.
So it is a picker over the 202 codes the model was trained on, with the backend's
own form of the pair spelled out underneath it.
"""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import paths  # noqa: E402
from .config import Config  # noqa: E402
from .geometry import DEFAULT_PROMPT, PROMPT_PRESETS  # noqa: E402
from .languages import (  # noqa: E402
    CODES,
    LANGUAGES,
    deepl_code,
    language_name,
    search,
    tesseract_lang,
)
from .occlusion import GUARD  # noqa: E402
from .ocr import bad_langs, split_langs  # noqa: E402
from .translate import DEFAULT_NLLB_MODEL  # noqa: E402

# Where the converted weights are expected: the user data dir, or an
# older in-tree install that has not been moved yet (lintranslator.paths).
DEFAULT_CT2_DIR = str(paths.default_ct2_dir())

BACKENDS = [
    ("ct2", "Local · int8 CTranslate2 (fast, offline)"),
    ("local", "Local · transformers (slow, offline)"),
    ("openrouter", "OpenRouter (many models, needs key)"),
    ("openai", "OpenAI (needs key)"),
    ("deepl", "DeepL (needs key)"),
    ("chat", "Custom · any OpenAI-compatible endpoint"),
    ("none", "None (pass-through, for testing)"),
]

# Which backends actually read `translate.model`, and what the field means for
# them. This is the whole reason the Model row cannot be one fixed widget: for
# ct2/local it is a HuggingFace repo, for the chat backends it is an id like
# "tencent/hy-mt2-1.8b", and for deepl/none it is unused entirely.
MODEL_LABELS = {
    "ct2": "HF tokenizer",
    "local": "HF model",
    "openrouter": "Model id",
    "openai": "Model id",
    "chat": "Model id",
}
MODEL_PLACEHOLDERS = {
    # ct2/local: an empty field means the default repo, and on a config whose
    # backend is a chat one the field opens empty - so this one is the only
    # place the default is named. The chat backends get an example because the
    # format is theirs to choose; openrouter gets nothing, because "pick a model
    # below" described the two buttons that sit beside the field.
    "ct2": f"{DEFAULT_NLLB_MODEL} (default)",
    "local": f"{DEFAULT_NLLB_MODEL} (default)",
    "openai": "e.g. gpt-4o-mini",
    "chat": "e.g. llama3.1:8b",
}
MODEL_TOOLTIPS = {
    "ct2": (
        "The HF repo the tokenizer is read from. The weights come from the "
        "converted int8 directory, not from here."
    ),
    "local": "The HuggingFace repo to load with transformers.",
    "openrouter": "Any id from openrouter.ai/models. Use the list button to search.",
    "openai": "Any OpenAI model id.",
    "chat": "Whatever your server calls the model, e.g. llama3.1:8b for Ollama.",
}
MODEL_LINKS = {
    "openrouter": ("https://openrouter.ai/models", "Browse all models"),
    "openai": ("https://platform.openai.com/docs/models", "Model docs"),
}

# Which backends read `translate.api_base` (the endpoint to talk to).
BACKENDS_WITH_BASE_URL = ("openrouter", "openai", "chat")
BASE_URL_PLACEHOLDERS = {
    "openrouter": "https://openrouter.ai/api/v1 (default)",
    "openai": "https://api.openai.com/v1 (default)",
    "chat": "http://localhost:11434/v1",
}

# Which backends read `translate.prompt`. The Model and Base URL rows already
# hide themselves for the backends that ignore them; the prompt did not, so the
# dialog used to sell it as "the biggest quality lever" over a field the default
# backend (ct2) never reads - NLLB is given a language code, not an instruction,
# and DeepL has no prompt parameter at all.
BACKENDS_WITH_PROMPT = ("openrouter", "openai", "chat")

# Curated shortlist, ordered cheapest-first inside each group. Not exhaustive on
# purpose: `Fetch list` loads every model the key can reach, and free-text entry
# is always allowed because model ids churn faster than this list.
MODEL_SUGGESTIONS = {
    "openrouter": [
        "tencent/hy-mt2-1.8b",
        "tencent/hy-mt2-7b",
        "google/gemini-2.5-flash-lite",
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.1-flash-lite",
        "qwen/qwen3-30b-a3b-instruct-2507",
        "deepseek/deepseek-v4-flash",
        "mistralai/mistral-small-24b-instruct-2501",
    ],
    "openai": ["gpt-4o-mini", "gpt-5-nano", "gpt-4.1-nano", "gpt-5-mini"],
}

# Models that answer with hidden reasoning tokens instead of a translation when
# max_tokens is small. They are worth naming in the UI: the failure looks like a
# broken app (empty response) rather than a model that needs a bigger budget.
REASONING_CAUTION = {
    "openai/gpt-5-nano",
    "qwen/qwen3.7-flash",
    "bytedance-seed/seed-2.0-mini",
    "z-ai/glm-5.3-flash",
    "stepfun/step-3.5-flash",
}

# Ordered so the first entry a backend supports is the recommended default.
BACKENDS_NEEDING_KEY = ("openrouter", "openai", "deepl")


class ModelPicker(Gtk.MenuButton):
    """A searchable model chooser that never fights the text box.

    The previous design put a plain `DropDown` next to the entry, so the list
    showed nine stale ids and the entry was squeezed to a few characters. Here
    the list lives in a popover with its own filter box, which is the only shape
    that stays usable when the real list is several hundred ids long.
    """

    def __init__(self, on_pick, suggestions: list[str] | None = None) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._all: list[str] = list(suggestions or [])
        self._history: list[str] = []

        self.set_icon_name("pan-down-symbolic")
        self.add_css_class("flat")
        self.set_tooltip_text("Search the model list")
        self.set_valign(Gtk.Align.CENTER)

        self.popover = Gtk.Popover()
        self.popover.set_size_request(430, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        self.search = Gtk.SearchEntry()
        # No placeholder: it is a search entry, sitting above the list it filters.
        # Filter as you type: the default 150 ms delay makes the list feel laggy
        # when the whole point is narrowing 400+ ids down quickly.
        self.search.set_search_delay(0)
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.search.connect("activate", lambda *_: self._activate_first())
        box.append(self.search)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._on_row_activated)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_height(240)
        self.scroll.set_max_content_height(380)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_child(self.listbox)
        box.append(self.scroll)

        self.count_label = Gtk.Label(label="", xalign=0)
        self.count_label.add_css_class("lintranslator-hint")
        box.append(self.count_label)

        self.content = box

        self.popover.set_child(box)
        self.set_popover(self.popover)
        self.popover.connect("show", self._on_popover_shown)
        self.refresh()

    # -- data -------------------------------------------------------------- #
    def set_models(self, models: list[str], note: str | None = None) -> None:
        """Replace the list. Called after `Fetch list` gets the real one."""
        # Largest first so a filtered view is not dominated by tiny fine-tunes,
        # but keep the curated shortlist at the very top: it encodes the models
        # that were actually measured to work for this pipeline.
        curated = [m for m in self._all if m in set(models)] if self._all else []
        rest = sorted(set(models) - set(curated))
        self._all = curated + rest
        self.refresh()
        if note:
            self.count_label.set_text(note)

    def set_history(self, models: list[str]) -> None:
        """Recently used models, shown first so switching back is one click."""
        keep = [m for m in models if m and m not in self._history]
        self._history = keep[:6]
        self.refresh()

    # -- rendering --------------------------------------------------------- #
    def refresh(self) -> None:
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)

        query = self.search.get_text().strip().lower()
        pool: list[str] = []
        if not query:
            pool.extend(m for m in self._history if m not in pool)
            pool.extend(m for m in self._all if m not in pool)
        else:
            pool = [m for m in self._all if query in m.lower()]

        section = None
        shown = 0
        limit = 400
        for model in pool[:limit]:
            if not query:
                wanted = "Recent" if model in self._history else "Suggested / fetched"
                if wanted != section:
                    section = wanted
                    self.listbox.append(self._section_header(wanted))
            self.listbox.append(self._model_row(model))
            shown += 1

        if not shown:
            empty = Gtk.Label(
                label=(
                    f"nothing matches “{self.search.get_text().strip()}”\n"
                    "use Fetch list, or type the id and press Enter"
                ),
                justify=Gtk.Justification.CENTER,
            )
            empty.add_css_class("lintranslator-hint")
            empty.set_margin_top(18)
            empty.set_margin_bottom(18)
            self.listbox.append(empty)

        if not query:
            self.count_label.set_text(
                f"{len(self._all)} models — type to filter"
                if self._all
                else "press Fetch list to load the models your key can reach"
            )
        else:
            self.count_label.set_text(f"{shown} of {len(self._all)} match")

    def _section_header(self, text: str) -> Gtk.ListBoxRow:
        label = Gtk.Label(label=text.upper(), xalign=0)
        label.add_css_class("lintranslator-hint")
        label.set_margin_top(8)
        label.set_margin_start(6)
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.set_child(label)
        return row

    def _model_row(self, model: str) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=model, xalign=0)
        label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        label.set_hexpand(True)
        box.append(label)
        if model.endswith(":free"):
            tag = Gtk.Label(label="free")
            tag.add_css_class("lintranslator-hint")
            box.append(tag)
        elif model in REASONING_CAUTION:
            tag = Gtk.Label(label="reasoning ⚠")
            tag.add_css_class("lintranslator-hint")
            tag.set_tooltip_text(
                "This model can spend the whole token budget thinking and return "
                "an empty translation. Raise max_tokens if you use it."
            )
            box.append(tag)

        row = Gtk.ListBoxRow()
        row.set_child(box)
        setattr(row, "model_id", model)
        row.set_tooltip_text(model)
        return row

    # -- events ------------------------------------------------------------ #
    def _on_popover_shown(self, *_args) -> None:
        self.search.set_text("")
        self.refresh()
        self.search.grab_focus()

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        model = getattr(row, "model_id", None)
        if not model:
            return
        self._on_pick(model)
        self.popdown()

    def _activate_first(self) -> None:
        """Enter in the filter box picks the top match."""
        row = self.listbox.get_first_child()
        while row is not None:
            model = getattr(row, "model_id", None)
            if model:
                self._on_pick(model)
                self.popdown()
                return
            row = row.get_next_sibling()



class LanguagePicker(Gtk.MenuButton):
    """A searchable FLORES-200 chooser - the only way to set either language.

    Same shape as `ModelPicker` because it has the same problem: 202 entries is
    past what a `Gtk.DropDown` can show usefully. The difference is that free
    text is not offered at all here. NLLB scores an unknown code as `<unk>` and
    returns plausible-looking garbage with no error, and a chat prompt that says
    "translate into jpn_Jpan" is simply ignored, so the picker offers exactly
    the codes the model has.

    A code that is already in the config but is *not* one of them is still shown
    on the button. Opening the settings window must never edit the config by
    itself, and quietly replacing a language the user typed is worse than
    showing it with a warning.
    """

    def __init__(self, on_pick=None, what: str = "language") -> None:
        super().__init__()
        self._on_pick = on_pick
        self._code = ""
        self._backend = ""
        self.add_css_class("flat")
        self.set_valign(Gtk.Align.CENTER)
        # A label and an arrow rather than a bare label: this opens a list, and
        # a button that looks like a text field while hiding 202 entries reads
        # as a field you are supposed to type in.
        self._label = Gtk.Label(xalign=0)
        self._label.set_hexpand(True)
        self._label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        _content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        _content.append(self._label)
        _content.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        self.set_child(_content)
        self.set_hexpand(True)

        self.popover = Gtk.Popover()
        self.popover.set_size_request(430, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(f"filter the {what}, e.g. japanese or jpn")
        self.search.set_search_delay(0)
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.search.connect("activate", lambda *_: self._activate_first())
        box.append(self.search)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._on_row_activated)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_height(240)
        self.scroll.set_max_content_height(380)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_child(self.listbox)
        box.append(self.scroll)

        self.count_label = Gtk.Label(label="", xalign=0)
        self.count_label.add_css_class("lintranslator-hint")
        box.append(self.count_label)

        self.popover.set_child(box)
        self.set_popover(self.popover)
        self.popover.connect("show", self._on_popover_shown)
        self.refresh()

    # -- value ------------------------------------------------------------- #
    def get_code(self) -> str:
        return self._code

    def set_code(self, code: str | None) -> None:
        """Set the value. Accepts anything: the config is the source of truth."""
        self._code = (code or "").strip()
        self._update_label()

    def set_backend(self, backend: str) -> None:
        """Tell the picker which backend the pair is for.

        The list then shows what *that* backend would be sent for each language
        - DeepL's `JA`, Google's `ja` - so a language the backend cannot express
        is visible while choosing rather than after pressing Save.
        """
        self._backend = backend
        self.refresh()

    # -- rendering --------------------------------------------------------- #
    def refresh(self) -> None:
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)

        query = self.search.get_text().strip()
        matches = search(query)
        for language in matches[:400]:
            self.listbox.append(self._language_row(language))

        if not matches:
            empty = Gtk.Label(
                label=(
                    f"nothing matches “{query}”\n"
                    "the 202 languages are the ones this model was trained on"
                ),
                justify=Gtk.Justification.CENTER,
            )
            empty.add_css_class("lintranslator-hint")
            empty.set_margin_top(18)
            empty.set_margin_bottom(18)
            self.listbox.append(empty)

        total = len(CODES)
        self.count_label.set_text(
            f"{len(matches)} of {total} languages — type to filter"
            if query
            else f"{total} languages — type to filter"
        )

    def _backend_tag(self, language) -> str:
        """What the current backend receives, when it is not the code itself."""
        if self._backend == "deepl":
            return language.deepl or "not supported"
        return ""

    def _language_row(self, language) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=language.name, xalign=0)
        label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        label.set_hexpand(True)
        box.append(label)

        code = Gtk.Label(label=language.code)
        code.add_css_class("lintranslator-hint")
        box.append(code)

        tag_text = self._backend_tag(language)
        if tag_text:
            tag = Gtk.Label(label=tag_text)
            tag.add_css_class("lintranslator-hint")
            box.append(tag)

        row = Gtk.ListBoxRow()
        row.set_child(box)
        setattr(row, "lang_code", language.code)
        row.set_tooltip_text(self._detail(language))
        return row

    def _detail(self, language) -> str:
        """What each backend would be sent for this language."""
        parts = [f"{language.name} ({language.code})"]
        if language.iso:
            parts.append(f"ISO {language.iso}")
        parts.append(f"DeepL {language.deepl}" if language.deepl else "DeepL: not supported")
        parts.append(
            f"OCR reads it with {language.tesseract}"
            if language.tesseract
            else "tesseract has no model for it"
        )
        return "\n".join(parts)

    def _update_label(self) -> None:
        language = LANGUAGES.get(self._code)
        if language is None:
            self._label.set_text(self._code or "pick a language")
            self.set_tooltip_text(
                f"{self._code!r} is not one of the 202 codes this model was trained "
                "on, so it is scored as <unk> - pick one from the list"
                if self._code
                else "No language set"
            )
            return
        self._label.set_text(language.label)
        self.set_tooltip_text(self._detail(language))

    # -- events ------------------------------------------------------------ #
    def _on_popover_shown(self, *_args) -> None:
        self.search.set_text("")
        self.refresh()
        self.search.grab_focus()

    def _on_row_activated(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        code = getattr(row, "lang_code", None)
        if not code:
            return
        self.set_code(code)
        if self._on_pick:
            self._on_pick(code)
        self.popdown()

    def _activate_first(self) -> None:
        """Enter in the filter box picks the top match."""
        row = self.listbox.get_first_child()
        while row is not None:
            code = getattr(row, "lang_code", None)
            if code:
                self.set_code(code)
                if self._on_pick:
                    self._on_pick(code)
                self.popdown()
                return
            row = row.get_next_sibling()


class SettingsDialog(Gtk.Window):
    """Modal-ish settings window. Writes to config on close."""

    def __init__(self, parent: Gtk.Window, config: Config, on_apply=None) -> None:
        super().__init__(transient_for=parent, title="LinTranslator settings", modal=False)
        self.add_css_class("lintranslator-app")
        self.config = config
        self.on_apply = on_apply
        # Per-backend memory for the shared Model field, so switching backend
        # does not drag one backend's value (an NLLB repo, say) into another's.
        self._model_memory: dict[str, str] = {}
        self._last_backend: str | None = None
        # The OCR languages as edited here, which is not `config.ocr.langs`
        # until Save: `ocr.langs` is a tesseract setting with its own life
        # (system tessdata, `--langs` on the command line), so the field edits
        # this copy and it is written once, with everything else.
        self._ocr_langs: str = config.ocr.langs

        # This window is large and lands wherever the compositor likes, so while
        # it is open the pipeline must not read the screen: it would capture the
        # settings UI instead of the game. Same rule as the region picker - see
        # lintranslator.occlusion - and it lifts by itself when the window closes.
        self._guard_key = f"settings-{id(self)}"
        self.connect(
            "map",
            lambda *_: GUARD.set_mapped(
                self._guard_key, True, "the settings window is on screen"
            ),
        )
        self.connect("unmap", lambda *_: GUARD.clear(self._guard_key))
        self.connect("close-request", lambda *_: GUARD.clear(self._guard_key))

        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Kept so the sliders can hand the wheel to it - see `_ignore_wheel`.
        self.outer_scroller = outer
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        outer.set_child(box)
        self.set_child(outer)

        box.append(self._build_translation_section())
        box.append(Gtk.Separator())
        box.append(self._build_display_section())

        # A toolbar rather than a bare right-aligned pair: the primary action
        # is the one that applies, and it should not look like its neighbour.
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.add_css_class("lintranslator-toolbar")
        self.status = Gtk.Label(label="", xalign=0, wrap=True)
        self.status.add_css_class("lintranslator-hint")
        self.status.set_hexpand(True)
        actions.append(self.status)
        save = Gtk.Button(label="Save and apply")
        save.add_css_class("lintranslator-tool")
        save.add_css_class("lintranslator-primary")
        save.connect("clicked", self._on_save)
        actions.append(save)
        close = Gtk.Button(label="Close")
        close.add_css_class("lintranslator-tool")
        close.connect("clicked", lambda *_: self.close())
        actions.append(close)
        box.append(actions)

        for slider in (
            self.font_scale,
            self.width_scale,
            self.target_lines,
            self.source_lines,
        ):
            self._ignore_wheel(slider)

        # Tall enough that Save stays visible without scrolling on a 1080p+
        # screen; the scroller covers smaller displays and the taller backends.
        # Measured rather than a constant, because the column's height depends on
        # which backend is configured - the model row, the API key and the prompt
        # box come and go with it - so one number is dead space under the toolbar
        # for some backends and a scrollbar for others. It also went stale the
        # moment a section was dropped from the column.
        monitor = self.get_display().get_monitors().get_item(0)
        available = monitor.get_geometry().height if monitor else 1080
        _, natural = box.get_preferred_size()
        self.set_default_size(740, max(560, min(natural.height, available - 120)))

    # -- the wheel over a slider ------------------------------------------- #
    def _ignore_wheel(self, widget: Gtk.Widget) -> None:
        """Keep the wheel over `widget` from changing its value.

        A `Gtk.Scale` changes value on every scroll it is given, and this dialog
        is a tall scrolling column of them: wheeling down to reach the buttons at
        the bottom drags four style values along with it, one per slider passed
        over. That is the whole of "too responsive, unexpected changes" - the
        values were not being set by anything the user did *to them*.

        GTK has no switch to turn this off, so the event is taken in the capture
        phase, which runs before the scale's own handler and before the internal
        widgets the event is actually aimed at.

        Returning True here would be fewer lines and worse: the event would stop
        dead, and the dialog would refuse to scroll wherever the pointer happened
        to be resting on a slider. So the wheel is forwarded to the dialog's own
        scroller instead, and the gesture does what the user meant.
        """
        controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("scroll", self._scroll_the_dialog_instead)
        widget.add_controller(controller)

    def _scroll_the_dialog_instead(self, controller, _dx: float, dy: float) -> bool:
        """Scroll the dialog by a wheel event that a slider would have eaten."""
        # A wheel notch arrives as +-1; a touchpad arrives already in surface
        # units, where one unit is a pixel. Without this the notch would move the
        # dialog a single pixel per click.
        step = 1.0 if controller.get_unit() == Gdk.ScrollUnit.SURFACE else 60.0
        adjustment = self.outer_scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + dy * step)
        return True

    # -- translation ------------------------------------------------------- #
    def _build_translation_section(self) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Label(label="Translation", xalign=0)
        head.add_css_class("lintranslator-section")
        frame.append(head)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        frame.append(grid)

        grid.attach(Gtk.Label(label="Backend", xalign=0), 0, 0, 1, 1)
        self.backend_dd = Gtk.DropDown.new_from_strings([label for _, label in BACKENDS])
        current = self.config.translate.backend
        self.backend_dd.set_selected(
            next((i for i, (key, _) in enumerate(BACKENDS) if key == current), 0)
        )
        self.backend_dd.set_hexpand(True)
        self.backend_dd.connect("notify::selected", lambda *_: self._on_backend_changed())
        grid.attach(self.backend_dd, 1, 0, 1, 1)

        # The language pair. One setting for every backend: NLLB is given the
        # FLORES-200 code verbatim, a chat model is told the name in the prompt,
        # DeepL and Google get an ISO code - all derived in `lintranslator.languages`.
        # It is a picker and not an entry because a typo here is invisible:
        # NLLB scores an unknown code as <unk> and returns fluent nonsense.
        grid.attach(Gtk.Label(label="From", xalign=0), 0, 1, 1, 1)
        self.source_picker = LanguagePicker(self._on_language_picked, "source language")
        self.source_picker.set_code(self.config.translate.source_lang)
        self.source_picker.set_hexpand(True)
        grid.attach(self.source_picker, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="To", xalign=0), 0, 2, 1, 1)
        target_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.target_picker = LanguagePicker(self._on_language_picked, "target language")
        self.target_picker.set_code(self.config.translate.target_lang)
        self.target_picker.set_hexpand(True)
        target_row.append(self.target_picker)
        self.swap_btn = Gtk.Button(label="⇅")
        self.swap_btn.set_tooltip_text("Swap the source and target languages")
        self.swap_btn.connect("clicked", lambda *_: self._on_swap_languages())
        target_row.append(self.swap_btn)
        grid.attach(target_row, 1, 2, 1, 1)

        # Under the pair: what this backend is actually sent, and the OCR
        # language the source needs. Both are consequences of the pair that are
        # invisible in the config, and both are wrong in ways that look like bad
        # translation quality rather than like a setting.
        language_notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.language_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self.language_hint.add_css_class("lintranslator-hint")
        self.language_hint.set_max_width_chars(70)
        language_notes.append(self.language_hint)

        # The OCR languages are a setting, not only advice. This used to be a hint
        # that appeared when something was wrong, behind a button that could only
        # ever *add* - so a list that had grown could not be shortened, and the
        # list growing is the direction that costs: every model in it competes
        # for every word, which is slower and lets a wrong script win a soft
        # glyph. The field takes exactly what tesseract's `-l` takes, so it can
        # be set, replaced and trimmed by hand; the button is a shortcut for the
        # one case worth a click.
        self.ocr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ocr_row.append(Gtk.Label(label="OCR languages", xalign=0))
        self.ocr_entry = Gtk.Entry()
        self.ocr_entry.set_text(self._ocr_langs)
        self.ocr_entry.set_hexpand(True)
        self.ocr_entry.set_tooltip_text(
            "What tesseract reads the box with — the value `-l` takes: `kor`, or "
            "`kor+eng` for a box that also has Latin names. Every language added "
            "is another model competing for every word."
        )
        self.ocr_entry.connect("changed", self._on_ocr_langs_changed)
        self.ocr_row.append(self.ocr_entry)
        self.ocr_sync_btn = Gtk.Button(label="")
        self.ocr_sync_btn.add_css_class("lintranslator-tool")
        self.ocr_sync_btn.connect("clicked", lambda *_: self._on_ocr_sync())
        self.ocr_row.append(self.ocr_sync_btn)
        language_notes.append(self.ocr_row)

        self.ocr_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self.ocr_hint.add_css_class("lintranslator-hint")
        self.ocr_hint.set_max_width_chars(70)
        language_notes.append(self.ocr_hint)
        grid.attach(language_notes, 1, 3, 1, 1)

        # The Model row is rebuilt on every backend change: the label, the
        # meaning of the field and the available picker all differ per backend,
        # and showing them all at once is what made this dialog unusable.
        self.model_label = Gtk.Label(label="Model", xalign=0)
        grid.attach(self.model_label, 0, 4, 1, 1)

        self.model_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.model_entry = Gtk.Entry()
        self.model_entry.set_text(self.config.translate.model or "")
        self.model_entry.set_hexpand(True)
        self.model_entry.set_placeholder_text("e.g. google/gemini-2.0-flash-001")
        self.model_entry.connect("changed", lambda *_: self._refresh_model_hint())
        self.model_row.append(self.model_entry)

        self.model_picker = ModelPicker(self._on_model_picked)
        self.model_row.append(self.model_picker)

        self.fetch_btn = Gtk.Button(label="Fetch list")
        self.fetch_btn.set_tooltip_text(
            "Load every model your key can reach, then search the list"
        )
        self.fetch_btn.connect("clicked", self._on_fetch_models)
        self.model_row.append(self.fetch_btn)
        grid.attach(self.model_row, 1, 4, 1, 1)

        # Only ct2 has a second, separate location (the converted weights).
        self.ct2_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ct2_entry = Gtk.Entry()
        self.ct2_entry.set_text(self.config.translate.ct2_model_dir or DEFAULT_CT2_DIR)
        self.ct2_entry.set_hexpand(True)
        self.ct2_entry.set_placeholder_text(DEFAULT_CT2_DIR)
        self.ct2_entry.set_tooltip_text(
            "Directory holding the CTranslate2 int8 weights. Build it with:\n"
            f"  python -m lintranslator.convert --model {DEFAULT_NLLB_MODEL} "
            f"--out {DEFAULT_CT2_DIR}"
        )
        self.ct2_row.append(self.ct2_entry)
        self.ct2_label = Gtk.Label(label="Weights dir", xalign=0)
        grid.attach(self.ct2_label, 0, 6, 1, 1)
        grid.attach(self.ct2_row, 1, 6, 1, 1)

        # API key entry. Editable here so nothing requires a terminal: exporting
        # an environment variable before launch is not a GUI workflow.
        self.key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_hexpand(True)
        self.key_entry.set_tooltip_text(
            "Stored in config.json next to this app. Leave empty to use the "
            "OPENROUTER_API_KEY / OPENAI_API_KEY environment variables instead."
        )
        if self.config.translate.api_key:
            self.key_entry.set_text(self.config.translate.api_key)
        self.key_row.append(self.key_entry)

        self.reveal_btn = Gtk.ToggleButton(label="Show")
        self.reveal_btn.set_tooltip_text("Reveal the key")
        self.reveal_btn.connect("toggled", self._on_reveal_key)
        self.key_row.append(self.reveal_btn)

        self.clear_btn = Gtk.Button(label="Clear")
        self.clear_btn.set_tooltip_text(
            "Remove the key from config.json and fall back to the environment"
        )
        self.clear_btn.connect("clicked", self._on_clear_key)
        self.key_row.append(self.clear_btn)

        self.key_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.key_label.add_css_class("lintranslator-hint")
        self.key_label.set_max_width_chars(70)

        key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        key_box.append(self.key_row)
        key_box.append(self.key_label)
        grid.attach(key_box, 1, 5, 1, 1)
        # The row gets a label like every other one. It used to be labelled by
        # the entry's placeholder, which disappears the moment a key is typed -
        # leaving a masked box with Show and Clear next to it and nothing saying
        # what it is.
        self.api_key_label = Gtk.Label(label="API key", xalign=0)
        grid.attach(self.api_key_label, 0, 5, 1, 1)

        # Contextual help under the model row: model meaning, a browse link, and
        # a warning when the chosen model is known to fail this pipeline.
        self.model_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self.model_hint.add_css_class("lintranslator-hint")
        self.model_hint.set_max_width_chars(70)
        self.model_hint.set_visible(False)
        model_hint_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        model_hint_row.append(self.model_hint)
        self.model_link = Gtk.LinkButton.new_with_label("", "")
        self.model_link.set_visible(False)
        model_hint_row.append(self.model_link)
        grid.attach(model_hint_row, 1, 7, 1, 1)

        # Where the request goes. This is what turns this app into "point it at
        # any OpenAI-compatible server" without editing config.json: llama.cpp,
        # Ollama, vLLM, Groq, Together, or a gateway of your own. Empty means the
        # provider's own URL for openrouter/openai.
        self.base_entry = Gtk.Entry()
        self.base_entry.set_text(self.config.translate.api_base or "")
        self.base_entry.set_hexpand(True)
        self.base_entry.set_tooltip_text(
            "The OpenAI-compatible base URL, without /chat/completions.\n"
            "https is required unless the server is on this machine."
        )
        self.base_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.base_row.append(self.base_entry)
        self.base_label = Gtk.Label(label="Base URL", xalign=0)
        grid.attach(self.base_label, 0, 8, 1, 1)
        grid.attach(self.base_row, 1, 8, 1, 1)

        # The licence of the weights the local backends download. It is not a
        # detail of this app - NLLB is non-commercial - so it is stated where the
        # backend is chosen, not only in the README.
        self.licence_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self.licence_hint.add_css_class("lintranslator-hint")
        self.licence_hint.set_max_width_chars(70)
        self.licence_hint.set_visible(False)
        grid.attach(self.licence_hint, 1, 9, 1, 1)

        # Prompt. Wrapped in a box of its own so the whole control - text, hint
        # and preset list - can be hidden for the backends that never send one.
        self.prompt_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prompt_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prompt_head.append(Gtk.Label(label="Prompt", xalign=0))
        self.preset_dd = Gtk.DropDown.new_from_strings(list(PROMPT_PRESETS))
        self.preset_dd.set_tooltip_text("Load a starting prompt")
        self.preset_dd.connect("notify::selected", self._on_preset_chosen)
        prompt_head.append(self.preset_dd)
        self.prompt_section.append(prompt_head)

        hint = Gtk.Label(
            label=(
                "Sent to the model before every line. "
                "{source} and {target} are filled in automatically."
            ),
            xalign=0,
            wrap=True,
            max_width_chars=70,
        )
        hint.add_css_class("lintranslator-hint")
        self.prompt_section.append(hint)

        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_monospace(False)
        buffer = self.prompt_view.get_buffer()
        # Show the built-in default rather than an empty box: the field being
        # empty means "use this", so an empty box would hide the very prompt that
        # is in force. Read from the constant, not from the preset list, so the
        # two cannot drift apart.
        buffer.set_text(self.config.translate.prompt or DEFAULT_PROMPT)
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        prompt_scroll.set_min_content_height(170)
        prompt_scroll.set_child(self.prompt_view)
        self.prompt_section.append(prompt_scroll)
        frame.append(self.prompt_section)

        # Paint the backend-dependent bits (placeholder, key row) on open, not
        # only after the dropdown is touched.
        self._on_backend_changed()
        return frame

    def _on_backend_changed(self) -> None:
        """Show only the rows this backend actually reads."""
        previous = getattr(self, "_last_backend", None)
        key = BACKENDS[self.backend_dd.get_selected()][0]

        # Remember what was typed for the backend we are leaving. Without this,
        # switching Local -> OpenRouter carries "facebook/nllb-200-distilled-600M"
        # into the model id field, which then fails at request time with a
        # confusing "model not found".
        if previous and previous != key and previous in MODEL_LABELS:
            self._model_memory[previous] = self.model_entry.get_text().strip()
        self._last_backend = key

        needs_key = key in BACKENDS_NEEDING_KEY
        # `chat` may or may not want one: a local server ignores auth, a hosted
        # gateway needs it, so the row is shown and the label explains.
        key_row_wanted = needs_key or key == "chat"
        uses_model = key in MODEL_LABELS

        self.fetch_btn.set_sensitive(key in ("openrouter", "openai"))
        self.fetch_btn.set_visible(key in ("openrouter", "openai"))
        self.key_entry.set_sensitive(key_row_wanted)
        self.key_row.set_visible(key_row_wanted)
        self.api_key_label.set_visible(key_row_wanted)
        # `key_label` is not set here: `_refresh_key_label`, called below, shows it
        # only when it has something the field cannot show.

        # Rebuild the Model row for this backend.
        self.model_label.set_visible(uses_model)
        self.model_row.set_visible(uses_model)
        self.model_picker.set_visible(key in MODEL_SUGGESTIONS)
        self.model_picker.set_models(MODEL_SUGGESTIONS.get(key, []))
        self.model_picker.set_history(self.config.translate.recent_models or [])
        if uses_model:
            self.model_label.set_text(MODEL_LABELS[key])
            self.model_entry.set_placeholder_text(MODEL_PLACEHOLDERS.get(key, ""))
            self.model_entry.set_tooltip_text(MODEL_TOOLTIPS[key])
            remembered = self._model_memory.get(key)
            if remembered is None:
                remembered = (
                    self.config.translate.model if self.config.translate.backend == key else ""
                )
                # Never treat another backend's NLLB repo as a chat model id.
                if key in ("openrouter", "openai") and remembered == DEFAULT_NLLB_MODEL:
                    remembered = ""
            self.model_entry.set_text(remembered or "")
        self.ct2_label.set_visible(key == "ct2")
        self.ct2_row.set_visible(key == "ct2")

        # The endpoint row: only the backends that talk HTTP to a URL.
        uses_base = key in BACKENDS_WITH_BASE_URL
        self.base_label.set_visible(uses_base)
        self.base_row.set_visible(uses_base)
        if uses_base:
            self.base_entry.set_placeholder_text(BASE_URL_PLACEHOLDERS[key])
            # Only show a stored value to the backend it was stored for: an
            # Ollama URL left in the field while OpenRouter is selected would be
            # saved over the provider default.
            self.base_entry.set_text(
                (self.config.translate.api_base or "")
                if self.config.translate.backend == key
                else ""
            )

        # The prompt is an instruction to a chat model. Nothing else reads it, and
        # a field that looks like it matters while doing nothing is worse than no
        # field at all.
        self.prompt_section.set_visible(key in BACKENDS_WITH_PROMPT)

        # The local weights are non-commercial (CC-BY-NC-4.0). Saying so here is
        # cheaper than a user finding out after shipping something with them.
        self.licence_hint.set_visible(key in ("ct2", "local"))
        if key in ("ct2", "local"):
            self.licence_hint.set_text(
                f"{DEFAULT_NLLB_MODEL} is licensed CC-BY-NC-4.0: non-commercial "
                "use only. See NOTICE for the terms and for the alternatives."
            )
        self._refresh_model_hint(key)
        self._refresh_key_label(key)
        # The languages do not change with the backend, but what they *become*
        # does: DeepL is sent "JA" where a chat model is told "Japanese".
        self.source_picker.set_backend(key)
        self.target_picker.set_backend(key)
        self._refresh_language()

    # -- languages --------------------------------------------------------- #
    def _on_language_picked(self, _code: str) -> None:
        self._refresh_language()

    def _on_swap_languages(self) -> None:
        """Swap the pair. Cheaper than two searches, and it cannot typo."""
        source, target = self.source_picker.get_code(), self.target_picker.get_code()
        self.source_picker.set_code(target)
        self.target_picker.set_code(source)
        self._refresh_language()

    def _refresh_language(self) -> None:
        """Spell out what this pair becomes for the selected backend.

        A pair is not the same string everywhere: NLLB decodes with `jpn_Jpan`,
        DeepL wants `JA`, and tesseract needs `jpn.traineddata` to read the
        source at all. Each of those is invisible in config.json and each fails
        as "the translation is bad" rather than as an error, so the dialog says
        which one is in play. The chat backends get the pair as words inside the
        prompt itself, where it is already visible.
        """
        source = self.source_picker.get_code()
        target = self.target_picker.get_code()
        backend = BACKENDS[self.backend_dd.get_selected()][0]
        target_name = language_name(target)

        parts: list[str] = []
        unknown = [code for code in (source, target) if code and code not in CODES]
        if unknown:
            listed = ", ".join(repr(code) for code in unknown)
            verb = "are" if len(unknown) > 1 else "is"
            parts.append(
                f"⚠ {listed} {verb} not a FLORES-200 code this model knows, so "
                f"{'they are' if len(unknown) > 1 else 'it is'} scored as <unk>. "
                "Pick one from the list."
            )

        if backend in ("ct2", "local"):
            parts.append(f"NLLB decodes with {source} → {target}.")
        elif backend == "deepl":
            if deepl_code(target) is None:
                parts.append(
                    f"⚠ DeepL cannot translate into {target_name or target}. "
                    "Pick another target, or use another backend for this pair."
                )
            else:
                source_part = (
                    "DeepL detects the source"
                    if deepl_code(source) is None
                    else f"DeepL gets {deepl_code(source)}"
                )
                parts.append(f"{source_part} → {deepl_code(target)}.")
        elif backend not in ("openrouter", "openai", "chat"):
            # That leaves `none`, which passes the text through, so the pair
            # changes nothing. The chat backends are not named here because
            # there is nothing left to say about the pair for them: it reaches
            # the model as words inside the prompt, and the endpoint is named
            # under the model row.
            parts.append("This backend passes text through, so the pair is unused.")

        self.language_hint.set_text("  ".join(parts))
        self._refresh_ocr_hint(source)

    def _ocr_lang_set(self) -> list[str]:
        return split_langs(self._ocr_langs)

    def _ocr_lang_problem(self) -> str | None:
        """A complaint about `ocr.langs`, or None when it is usable.

        The value becomes both tesseract's `-l` argument and a filename under
        the tessdata directory, so a name that is not a plain language name is
        refused rather than passed on - see `lintranslator.ocr.parse_langs`.
        """
        bad = bad_langs(self._ocr_langs)
        if not bad:
            return None
        listed = ", ".join(repr(name) for name in bad)
        return (
            f"⚠ {listed} is not a valid tesseract language name, so OCR will "
            "refuse to run. Use letters, digits and `_`, e.g. eng or chi_sim."
        )

    def _refresh_ocr_hint(self, source: str) -> None:
        """Say what is wrong with the OCR languages, and offer the one-click fix.

        Reading Japanese with `eng.traineddata` produces confident nonsense, and
        the OCR language is a separate setting from the translation language, so
        the two drift apart silently. The button is a button and not an automatic
        write because `ocr.langs` may have been tuned on purpose (a game with
        Latin names over Japanese text wants `eng+jpn`, not `jpn`) - and because
        the other direction matters just as much: every model in the list
        competes for every word, so it is slower and a wrong script can win on a
        soft glyph.

        So there are two offers and one rule for staying quiet. The list that
        reads the box is the source's own model plus `eng`, which is what carries
        the Latin names and UI labels any box can contain - the same reason the
        add button offers `eng+jpn` rather than `jpn`. Anything beyond that is
        competing without being asked for, which is worth one click to drop, and
        a list that already *is* that is left alone: `eng+kor` is a choice, not a
        mistake, and a hint that fired on it would be nagging.
        """
        problem = self._ocr_lang_problem()
        if problem:
            self.ocr_hint.set_text(problem)
            self.ocr_hint.set_visible(True)
            self.ocr_sync_btn.set_visible(False)
            return

        wanted = tesseract_lang(source)
        current = self._ocr_lang_set()
        if wanted is None:
            # tesseract has no model for the source. There is no file to add and
            # no minimal list to name, so the field is the only help - and it
            # stays visible, because it is the setting.
            self.ocr_hint.set_text("")
            self.ocr_hint.set_visible(False)
            self.ocr_sync_btn.set_visible(False)
            return

        minimal = self._ocr_minimal_set(wanted)
        extra = [name for name in current if name not in minimal]

        if wanted not in current:
            offered = [*current, wanted]
            self.ocr_hint.set_text(
                f"OCR reads “{self._ocr_langs.strip() or 'nothing'}”, but the source is "
                f"{language_name(source)} — tesseract needs {wanted} for it."
            )
            self.ocr_sync_btn.set_label(f"Use {'+'.join(offered)}")
            self.ocr_sync_btn.set_tooltip_text(
                f"Add {wanted} to the OCR languages for the capture, giving "
                f"“{'+'.join(offered)}”. Applied when you press Save."
            )
        elif extra:
            listed = " and ".join(extra) if len(extra) == 2 else ", ".join(extra)
            # "eng" is the whole minimal list when the source is English, so the
            # usual "plus eng for Latin names" would explain it as itself.
            if wanted == "eng":
                why = "the source's own model"
            else:
                why = "the source's model plus eng for Latin names"
            self.ocr_hint.set_text(
                f"OCR reads “{self._ocr_langs.strip()}”, so {listed} "
                f"{'competes' if len(extra) == 1 else 'compete'} for every word as "
                "well — slower, and a wrong script can win on a soft glyph. "
                f"“{'+'.join(minimal)}” is {why}."
            )
            self.ocr_sync_btn.set_label(f"Use {'+'.join(minimal)}")
            self.ocr_sync_btn.set_tooltip_text(
                f"Set the OCR languages for the capture to “{'+'.join(minimal)}”, "
                f"dropping {listed}. Applied when you press Save."
            )
        else:
            self.ocr_hint.set_text("")
            self.ocr_hint.set_visible(False)
            self.ocr_sync_btn.set_visible(False)
            return

        self.ocr_hint.set_visible(True)
        self.ocr_sync_btn.set_visible(True)

    def _on_ocr_langs_changed(self, entry: Gtk.Entry) -> None:
        """Follow the field: it is the control, `_ocr_langs` is what Save reads."""
        self._ocr_langs = entry.get_text()
        self._refresh_ocr_hint(self.source_picker.get_code())

    def _set_ocr_langs(self, value: str) -> None:
        """Write the list into the field, which the state then follows.

        Through the field rather than around it, so the two cannot disagree: a
        button that changed only `_ocr_langs` would leave the visible box showing
        the list it had just replaced.
        """
        if self.ocr_entry.get_text() != value:
            self.ocr_entry.set_text(value)  # emits "changed", which syncs state
        else:
            self._ocr_langs = value
            self._refresh_ocr_hint(self.source_picker.get_code())

    @staticmethod
    def _ocr_minimal_set(wanted: str) -> list[str]:
        """The list that reads a box written in `wanted`, and nothing more.

        `eng` comes along because it is what carries the Latin names and UI
        labels any box can contain - the reason the add offer is `eng+jpn` and
        not `jpn`. Both the hint and the button use this, so what is described
        and what is applied cannot differ.
        """
        return [wanted] if wanted == "eng" else [wanted, "eng"]

    def _on_ocr_sync(self) -> None:
        """Apply the move the hint is offering: add the source's model, or trim."""
        source = self.source_picker.get_code()
        wanted = tesseract_lang(source)
        if wanted is None:
            return
        current = self._ocr_lang_set()
        minimal = self._ocr_minimal_set(wanted)
        # One button, two directions. Adding is for "the source is unreadable";
        # trimming is for "the list has grown past what reads this box", which
        # only the user can judge - so the hint names what is extra and the button
        # offers the list without it.
        if wanted not in current:
            updated = [*current, wanted]
        elif [name for name in current if name not in minimal]:
            updated = minimal
        else:
            return  # nothing was being offered; the button is hidden for this case
        self._set_ocr_langs("+".join(updated))
        self.status.set_text(f"OCR languages set to {self._ocr_langs} — press Save")

    def _refresh_model_hint(self, backend: str | None = None) -> None:
        backend = backend or BACKENDS[self.backend_dd.get_selected()][0]
        model = self.model_entry.get_text().strip()

        link = MODEL_LINKS.get(backend)
        if link:
            self.model_link.set_label(link[1])
            self.model_link.set_uri(link[0])
            self.model_link.set_visible(True)
        else:
            self.model_link.set_visible(False)

        parts: list[str] = []
        if backend == "chat":
            base = self.base_entry.get_text().strip()
            if base:
                parts.append(f"Requests go to {base}/chat/completions.")
            else:
                parts.append(
                    "⚠ set the Base URL above — llama.cpp, Ollama and vLLM all "
                    "serve this protocol, e.g. http://localhost:11434/v1"
                )
        if backend == "ct2":
            path = Path(self.ct2_entry.get_text().strip() or DEFAULT_CT2_DIR)
            if not path.exists():
                parts.append(
                    f"⚠ no converted weights at {path} — build them with "
                    f"`python -m lintranslator.convert --out {DEFAULT_CT2_DIR}`"
                )
        if model in REASONING_CAUTION:
            parts.append(
                "⚠ this model may spend the whole token budget on hidden reasoning "
                "and return an empty translation. Raise translate.max_tokens."
            )
        self.model_hint.set_text("  ".join(parts))
        self.model_hint.set_visible(bool(parts))

    def _refresh_key_label(self, backend: str | None = None) -> None:
        """Say which key will be used, and where it came from.

        Silent while a key is typed into the field above: the field is the
        indicator, and the buttons beside it are the controls. The one thing the
        line used to add there - that the key is written into config.json in
        plain text - is on the field's own tooltip. What is left is the case the
        field cannot show: a key coming from the environment, or none at all.
        """
        import os

        def note(text: str, visible: bool = True) -> None:
            self.key_label.set_text(text)
            self.key_label.set_visible(visible)

        backend = backend or BACKENDS[self.backend_dd.get_selected()][0]
        if backend == "chat":
            # No key is required here, so the label explains both cases instead
            # of claiming one is needed or that none ever is.
            typed = self.key_entry.get_text().strip()
            # Through `resolve_api_key` rather than `os.environ` directly, so the
            # label cannot disagree with what the translator will actually use.
            from .translate import resolve_api_key

            env = resolve_api_key(None, "LINTRANSLATOR_API_KEY")
            if typed:
                note("", visible=False)
            elif env:
                note(
                    f"No key entered here, so LINTRANSLATOR_API_KEY from the environment "
                    f"is used ({env[:6]}…{env[-4:]})."
                )
            else:
                note(
                    "No key set. A server on this machine usually needs none; a "
                    "hosted endpoint needs one (paste it above or set "
                    "LINTRANSLATOR_API_KEY)."
                )
            return
        envs = {
            "openrouter": ("OPENROUTER_API_KEY", "LINTRANSLATOR_API_KEY"),
            "openai": ("OPENAI_API_KEY", "LINTRANSLATOR_API_KEY"),
            # DeepL's own variable, as the translator reads it. Without this the
            # row was visible but the label claimed no key was needed.
            "deepl": ("DEEPL_API_KEY",),
        }.get(backend)
        if not envs:
            # The row is hidden for these backends, so this text is never on
            # screen; it is what `lintranslator check`-style introspection and
            # the tests read.
            note("No key needed for this backend.", visible=False)
            return

        typed = self.key_entry.get_text().strip()
        if typed:
            note("", visible=False)
            return
        for name in envs:
            if os.environ.get(name):
                value = os.environ[name]
                note(
                    f"No key entered here, so {name} from the environment is used "
                    f"({value[:6]}…{value[-4:]})."
                )
                return
        note(f"No key yet. Paste one above, or set {envs[0]} in the environment.")

    def _on_reveal_key(self, button: Gtk.ToggleButton) -> None:
        hidden = not button.get_active()
        self.key_entry.set_visibility(hidden)
        button.set_label("Hide" if not hidden else "Show")

    def _on_clear_key(self, _button: Gtk.Button) -> None:
        self.key_entry.set_text("")
        self.config.translate.api_key = None
        self.config.save()
        self._refresh_key_label()
        self.status.set_text("key cleared from config.json")

    def _on_model_picked(self, model: str) -> None:
        self.model_entry.set_text(model)
        self._refresh_model_hint()

    def _on_preset_chosen(self, *_args) -> None:
        name = list(PROMPT_PRESETS)[self.preset_dd.get_selected()]
        self.prompt_view.get_buffer().set_text(PROMPT_PRESETS[name])

    def _on_fetch_models(self, _button: Gtk.Button) -> None:
        """Load the real model list in the background (network call)."""
        import threading

        from .translate import OpenRouterTranslator, TranslatorError, resolve_api_key

        backend = BACKENDS[self.backend_dd.get_selected()][0]
        envs = (
            ("OPENROUTER_API_KEY", "LINTRANSLATOR_API_KEY")
            if backend == "openrouter"
            else ("OPENAI_API_KEY", "LINTRANSLATOR_API_KEY")
        )
        key = resolve_api_key(self.config.translate, *envs)
        if not key:
            self.status.set_text("cannot fetch models without an API key")
            return

        self.fetch_btn.set_sensitive(False)
        self.status.set_text("fetching model list…")

        def work() -> None:
            try:
                translator = OpenRouterTranslator(key, model="placeholder")
                models = translator.available_models()
                GLib.idle_add(self._apply_models, models)
            except TranslatorError as exc:
                GLib.idle_add(self.status.set_text, f"fetch failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self.status.set_text, f"fetch failed: {type(exc).__name__}: {exc}")
            finally:
                GLib.idle_add(self.fetch_btn.set_sensitive, True)

        threading.Thread(target=work, daemon=True).start()

    def _apply_models(self, models: list[str]) -> bool:
        if models:
            self.model_picker.set_models(
                models, note=f"{len(models)} models — type to filter"
            )
            self.status.set_text(
                f"loaded {len(models)} models — open the list to search, "
                "or type an id directly"
            )
        else:
            self.status.set_text("the provider returned an empty model list")
        return False

    # -- display area ------------------------------------------------------ #
    def _build_display_section(self) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        display_head = Gtk.Label(label="Display area — the card", xalign=0)
        display_head.add_css_class("lintranslator-section")
        frame.append(display_head)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        frame.append(grid)

        grid.attach(Gtk.Label(label="Font size", xalign=0), 0, 0, 1, 1)
        self.font_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.7, 2.2, 0.05)
        self.font_scale.set_value(self.config.display.font_scale)
        self.font_scale.set_draw_value(True)
        self.font_scale.set_hexpand(True)
        grid.attach(self.font_scale, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Card width", xalign=0), 0, 1, 1, 1)
        self.width_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 360, 1400, 20)
        self.width_scale.set_value(self.config.display.width)
        self.width_scale.set_draw_value(True)
        self.width_scale.set_hexpand(True)
        grid.attach(self.width_scale, 1, 1, 1, 1)

        # The card's height budget. Expressed in lines rather than pixels because
        # that is the unit the question is actually asked in ("how much of the
        # dialogue do I want to see?"), and because it survives a font-size
        # change - the line height is measured from the font at layout time.
        grid.attach(Gtk.Label(label="Translation lines", xalign=0), 0, 2, 1, 1)
        self.target_lines = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 8, 1)
        self.target_lines.set_value(self.config.display.target_lines)
        self.target_lines.set_draw_value(True)
        self.target_lines.set_hexpand(True)
        grid.attach(self.target_lines, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Original text lines", xalign=0), 0, 3, 1, 1)
        self.source_lines = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 6, 1)
        self.source_lines.set_value(self.config.display.source_lines)
        self.source_lines.set_draw_value(True)
        self.source_lines.set_hexpand(True)
        grid.attach(self.source_lines, 1, 3, 1, 1)

        self.show_source = Gtk.CheckButton(
            label="Show the original text below the translation"
        )
        self.show_source.set_active(self.config.display.show_source)
        frame.append(self.show_source)
        return frame

    # -- save -------------------------------------------------------------- #
    def _on_save(self, _button: Gtk.Button) -> None:
        buffer = self.prompt_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        backend = BACKENDS[self.backend_dd.get_selected()][0]
        self.config.translate.backend = backend
        model = self.model_entry.get_text().strip()
        if not model and backend in ("ct2", "local"):
            # An empty model means "use the provider's default" for the chat
            # backends, but for the NLLB ones it would be an empty HuggingFace
            # repo id, which fails at load time with a validation error. Store
            # the real default so the panel names the model actually in use.
            model = DEFAULT_NLLB_MODEL
        self.config.translate.model = model

        # Only remember ids for backends where the field is a model id; storing
        # an NLLB repo or a DeepL target here would poison the picker.
        if backend in ("openrouter", "openai") and model:
            recent = [m for m in self.config.translate.recent_models if m != model]
            self.config.translate.recent_models = [model, *recent][:6]

        if backend == "ct2":
            weights = self.ct2_entry.get_text().strip() or DEFAULT_CT2_DIR
            self.config.translate.ct2_model_dir = weights

        if backend in BACKENDS_WITH_BASE_URL:
            self.config.translate.api_base = self.base_entry.get_text().strip() or None

        # An empty field means "use the environment", not "store an empty key".
        self.config.translate.api_key = self.key_entry.get_text().strip() or None
        self.config.translate.prompt = text.strip()

        # Language pair. Written back exactly as the pickers hold it, including a
        # code the config already had that is not a real one: the hint above says
        # so, and quietly rewriting it here would make opening Settings the thing
        # that changed the language.
        self.config.translate.source_lang = self.source_picker.get_code()
        self.config.translate.target_lang = self.target_picker.get_code()
        # An emptied field is not a setting: OCR with no language cannot run, so
        # clearing the box leaves the stored list alone rather than saving a value
        # the engine would refuse. Anything else is written in the one form `-l`
        # accepts - `split_langs` also takes whitespace, and `-l "kor eng"` is not
        # two languages but one model named "kor eng", which loads none of them.
        ocr_warning: str | None = None
        if self._ocr_langs.strip():
            # A name that is not a language name is kept out of the config: it
            # would become a tessdata filename, and `parse_langs` refuses it at
            # read time anyway. Saying so beats a save that looks like it worked.
            ocr_warning = self._ocr_lang_problem()
            if ocr_warning:
                ocr_warning = (
                    f"{ocr_warning} OCR languages left at "
                    f"{self.config.ocr.langs!r}."
                )
            else:
                self.config.ocr.langs = "+".join(split_langs(self._ocr_langs))
                # Show what was actually stored, so a list typed with spaces is
                # not left on screen in a form the engine never sees.
                self._set_ocr_langs(self.config.ocr.langs)
        self.config.display.font_scale = round(self.font_scale.get_value(), 2)
        self.config.display.width = int(self.width_scale.get_value())
        # Changing a line budget re-states the rule the card's height comes from,
        # so it releases a height that was pinned by dragging an edge. Keeping
        # the pinned height here would make these two sliders look broken.
        budget_changed = (
            int(self.target_lines.get_value()) != self.config.display.target_lines
            or int(self.source_lines.get_value()) != self.config.display.source_lines
        )
        self.config.display.target_lines = int(self.target_lines.get_value())
        self.config.display.source_lines = int(self.source_lines.get_value())
        if budget_changed:
            self.config.display.height = 0
        self.config.display.show_source = self.show_source.get_active()

        path = self.config.save()
        pair = (
            f"{language_name(self.config.translate.source_lang)} → "
            f"{language_name(self.config.translate.target_lang)}"
        )
        self.status.set_text(f"saved to {path} — {pair}")
        if ocr_warning:
            self.status.set_text(f"{self.status.get_text()}  {ocr_warning}")
        if self.on_apply:
            self.on_apply()
