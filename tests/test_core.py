"""Tests for the logic that is easy to get subtly wrong.

The settler/change-detector interaction in particular already shipped one bug:
requiring N identical consecutive OCR reads never fires on a static screen,
because OCR only runs when pixels change. These tests pin that down.

Run:  .venv-gi/bin/python -m pytest tests/ -v
"""
from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from lintranslator.config import Config, Region, TranslateConfig
from lintranslator.detect import (
    ChangeDetector,
    EmptyGuard,
    TextSettler,
    is_same_reading,
    looks_complete,
    mean_abs_delta,
    signature,
)
from lintranslator.ocr import preprocess
from lintranslator.translate import TranslationCache, is_cjk, normalize_cjk


def _frame(text: str = "", size=(320, 60)) -> Image.Image:
    img = Image.new("RGB", size, (12, 12, 14))
    if text:
        ImageDraw.Draw(img).text((8, 20), text, fill=(235, 235, 235))
    return img


# --------------------------------------------------------------------------- #
# Change detection
# --------------------------------------------------------------------------- #
def test_first_frame_always_counts_as_change():
    det = ChangeDetector()
    assert det.update(_frame("hello")) is True


def test_identical_frames_are_not_changes():
    det = ChangeDetector()
    frame = _frame("hello")
    assert det.update(frame) is True
    for _ in range(5):
        assert det.update(frame) is False
    assert det.frames == 6
    assert det.changes == 1


def test_different_text_is_a_change():
    det = ChangeDetector()
    det.update(_frame("hello"))
    assert det.update(_frame("hello world")) is True


def test_incremental_typewriter_change_is_detected():
    """Regression: a mean-difference metric missed this, because appending a few
    characters changes so little of the frame that the average stays flat. The
    typewriter reveal is precisely this kind of localized change."""
    det = ChangeDetector()
    det.update(_frame("The reactor is"))
    # one more word revealed in an otherwise identical frame
    assert det.update(_frame("The reactor is overheat")) is True
    # repeating the same frame is still not a change
    assert det.update(_frame("The reactor is overheat")) is False


def test_single_character_change_is_detected():
    det = ChangeDetector()
    det.update(_frame("100 gold"))
    assert det.update(_frame("100 gole")) is True


def test_changed_fraction_ignores_subthreshold_noise():
    """Tiny sensor/compression noise must not trigger OCR on every poll."""
    a = bytes([100] * 1000)
    b = bytes([100 + 3] * 1000)  # below pixel_delta
    from lintranslator.detect import changed_fraction

    assert changed_fraction(a, b, pixel_delta=12) == 0.0


def test_signature_and_delta_are_sane():
    a = signature(_frame("hello"))
    b = signature(_frame("hello"))
    assert a == b
    assert mean_abs_delta(a, b) == 0.0
    c = signature(_frame("something else entirely"))
    assert mean_abs_delta(a, c) > 0


# --------------------------------------------------------------------------- #
# Settler: the typewriter case
# --------------------------------------------------------------------------- #
def test_partial_typewriter_text_is_held_then_released():
    """Mid-reveal text must not be translated, but the final text must be.

    Release is measured from the last *change*, so the final line needs to hold
    still for `settle_window` - not merely be seen twice.
    """
    settler = TextSettler(settle_frames=2, settle_max_wait=2.0, settle_window=1.2)
    # Typewriter reveal, one OCR sample at a time.
    assert settler.observe("The react", 0.0) is False
    assert settler.observe("The reactor is", 0.5) is False
    assert settler.observe("The reactor is overheating.", 1.0) is False
    # Still inside the stability window: the reveal only just finished.
    assert settler.observe("The reactor is overheating.", 1.5) is False
    # Past the window -> final.
    assert settler.observe("The reactor is overheating.", 2.3) is True


def test_oscillating_text_still_settles():
    """Regression from a live screen: if the OCR result jitters between two
    values, a timer that restarts on every change never expires. Stability
    measured from the last change settles anyway."""
    settler = TextSettler(settle_frames=2, settle_max_wait=2.0, settle_window=1.0)
    # Alternating reads, never the same twice in a row.
    now = 0.0
    released = False
    for i in range(12):
        text = "line" if i % 2 else "line."
        if settler.observe(text, now):
            released = True
            break
        now += 0.2
    assert released, "jittering text never settled"
    assert now >= 1.0


def test_long_line_tolerates_real_ocr_jitter():
    """Regression: jitter on a long line is not "a character or two".

    Measured on the live screen (tesseract, conf 80-91, identical pixels): the
    same 70-character Limbus line came back with 2-10 character edits between
    polls - the leading em-dash run and a trailing cursor glyph are not stable.
    An edit-distance-only rule rejected these, which reset the settle timer on
    every read so the line was never translated at all.
    """
    reads = [
        "——— Ah, let me set it on your table, Master... It's hot—you",
        "° — Ah, etme setit on your table, Master... It's hot—you d",
        "—— Ah, let me setit on your table, Master... It's hot—you ",
        "a He has a point, Sinclair. Perhaps it would be best for your ",
    ]
    assert is_same_reading(reads[0], reads[1]), "wild-but-same read treated as a new line"
    assert is_same_reading(reads[1], reads[2]), "trailing cursor glyph treated as a new line"
    assert is_same_reading(reads[3], reads[3].replace("a He", "—, He")), (
        "3-edit jitter on a 62-char line treated as a new line"
    )


def test_a_genuinely_different_line_is_still_a_new_line():
    """The tolerance above must not swallow real dialogue changes."""
    a = "—— Ah, let me setit on your table, Master... It's hot—you"
    b = "Years have passed since that smoke faded into the grey sky."
    assert not is_same_reading(a, b)
    assert not is_same_reading(b, "Nothing but ash remains here now.")


def test_short_lines_still_decide_by_edit_distance():
    """A ratio is meaningless on short lines, so it must not decide them.

    "Yes." and "No." are 0.67 similar; a bare similarity threshold would call
    them the same line and silently drop a real change.
    """
    assert is_same_reading("Yes.", "Yes,")
    assert not is_same_reading("Yes.", "No.")
    assert not is_same_reading("Wait!", "Wait...")
    assert not is_same_reading("", "anything")


def test_typewriter_reveal_is_not_mistaken_for_jitter():
    """A reveal grows, so it must keep resetting the settle window."""
    assert not is_same_reading("Ah, let me set it on your", "Ah, let me set it on your table, Master")
    assert not is_same_reading("The react", "The reactor is overheating.")


def test_unfinished_text_waits_out_a_reveal_pause():
    """Regression: a game pauses mid-reveal, and "stable for N seconds" cannot
    tell that pause from the end of a line. Releasing on the pause put a fragment
    in the panel ("...the proverbial poster child of company") and then translated
    the line again when the rest arrived."""
    settler = TextSettler(settle_frames=2, settle_max_wait=8.0, settle_window=1.2)
    partial = "The inspector is the proverbial poster child of company"  # no terminator
    assert settler.observe(partial, 0.0) is False
    # Still unfinished 3s later (the pause a live game took), so it must be held.
    assert settler.tick(3.0) is False, "a paused reveal was released as final"
    # The rest of the sentence arrives and the line is now complete.
    full = "The inspector is the proverbial poster child of company-sponsored contractors."
    assert settler.observe(full, 3.5) is False   # reveal continues, window restarts
    assert settler.held == full, "the completed sentence must replace the fragment"
    assert settler.tick(4.6) is False            # settle_window is 1.2s from 3.5s
    assert settler.tick(4.8) is True, "the finished line must still be released"


def test_finished_line_is_released_on_the_normal_window():
    """The unfinished-text grace must not slow down ordinary dialogue."""
    settler = TextSettler(settle_frames=2, settle_max_wait=8.0, settle_window=1.2)
    assert settler.observe("The reactor is overheating.", 0.0) is False
    assert settler.tick(1.1) is False
    assert settler.tick(1.3) is True


def test_unfinished_text_is_eventually_released():
    """A line the game never punctuates must not be held forever."""
    settler = TextSettler(settle_frames=2, settle_max_wait=8.0, settle_window=1.2)
    assert settler.observe("Gotta keep your clientele more distinguished", 0.0) is False
    assert settler.tick(5.6) is False          # settle_window + incomplete_grace
    assert settler.tick(5.8) is True           # released once the grace expires
    assert settler.tick(8.1) is True           # and the cap is a ceiling, not a gate


def test_looks_complete_reads_punctuation():
    """Asymmetric on purpose: a fragment judged finished is worse than a finished
    line judged unfinished, which only costs the grace period."""
    for finished in ["Done.", "Really?", "Stop!", "He said, \"no.\"", "wait...", "そうですね。"]:
        assert looks_complete(finished), finished
    for fragment in [
        "the proverbial poster child of Work",
        "more importantly, big-name Workshops out there don't let anyb",
        "and then he said,",       # a reveal holds exactly here
        "wait—",
        "the following:",
        "",
        "   ",
    ]:
        assert not looks_complete(fragment), fragment


def test_confirm_prevents_re_emitting_the_same_line():
    # A finished line (terminal punctuation), so the release is governed by the
    # settle window alone rather than by the unfinished-text grace.
    settler = TextSettler(settle_frames=2, settle_max_wait=2.0, settle_window=1.0)
    assert settler.observe("The same line, finished.", 0.0) is False
    assert settler.observe("The same line, finished.", 1.5) is True
    settler.confirm(1.5)
    settler.reset()
    # The line is still on screen; it must not be translated again immediately.
    assert settler.observe("The same line, finished.", 1.6) is False
    assert settler.tick(2.0) is False


def test_a_reveal_converges_instead_of_being_discarded():
    """A changing read must keep the newest text, not drop back to the first."""
    settler = TextSettler(settle_frames=2, settle_max_wait=5.0, settle_window=1.0)
    settler.observe("The react", 0.0)
    settler.observe("The reactor is overheating.", 0.3)
    assert settler.held == "The reactor is overheating."


def test_static_screen_still_releases_via_tick():
    """The bug this guards: OCR stops when pixels stop changing, so a line that
    appears and then sits still would never be translated without a timeout."""
    settler = TextSettler(settle_frames=2, settle_max_wait=2.0)
    assert settler.observe("A static line of dialogue.", 0.0) is False
    assert settler.tick(1.0) is False  # not yet
    assert settler.tick(2.5) is True  # released by time, not by a second read


def test_empty_text_resets_the_settler():
    settler = TextSettler(settle_frames=2, settle_max_wait=2.0)
    settler.observe("Some text", 0.0)
    assert settler.observe("", 0.5) is False
    assert settler.held is None
    assert settler.tick(10.0) is False


def test_settle_frames_one_translates_immediately():
    settler = TextSettler(settle_frames=1, settle_max_wait=2.0)
    assert settler.observe("Immediate", 0.0) is True


# --------------------------------------------------------------------------- #
# Empty guard
# --------------------------------------------------------------------------- #
def test_empty_guard_mutes_after_repeated_empty_reads():
    guard = EmptyGuard(limit=3)
    assert guard.observe(True) is True
    assert guard.observe(False) is False
    assert guard.observe(False) is False
    assert guard.observe(False) is False
    assert guard.muted is True
    # Text returning must unmute.
    assert guard.observe(True) is True
    assert guard.muted is False


# --------------------------------------------------------------------------- #
# Text post-processing
# --------------------------------------------------------------------------- #
def test_cjk_detection():
    assert is_cjk("あ")
    assert is_cjk("事")
    assert is_cjk("。")
    assert not is_cjk("A")
    assert not is_cjk(" ")


def test_normalize_cjk_removes_spaces_between_japanese():
    assert normalize_cjk("今日 の 要請 に関する") == "今日の要請に関する"


def test_normalize_cjk_keeps_spaces_around_latin():
    assert normalize_cjk("LRU cache を 使う") == "LRU cache を使う"


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_cache_roundtrip_and_stats(tmp_path):
    cache = TranslationCache(tmp_path / "c.json")
    assert cache.get("hello") is None
    cache.put("hello", "こんにちは")
    assert cache.get("hello") == "こんにちは"
    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1

    cache.save()
    reloaded = TranslationCache(tmp_path / "c.json")
    assert reloaded.get("hello") == "こんにちは"


def test_cache_evicts_beyond_max_entries():
    cache = TranslationCache(None, max_entries=3)
    for i in range(5):
        cache.put(f"k{i}", f"v{i}")
    assert cache.stats["entries"] == 3
    assert cache.get("k0") is None
    assert cache.get("k4") == "v4"


def test_a_memory_only_cache_writes_nothing():
    """What the default config uses.

    The disk cache is a plaintext transcript of everything that passed through
    the capture box - which is whatever was on screen, not only the game - so
    persisting it is opt-in. Within a run it still collapses repeats.
    """
    cache = TranslationCache(None)
    cache.put("hello", "こんにちは")
    assert cache.get("hello") == "こんにちは"
    cache.save()  # no path: a no-op, not an error
    assert cache.stats["entries"] == 1


def test_the_default_config_does_not_persist_translations():
    from lintranslator.pipeline import _cache_for

    cfg = Config()
    assert cfg.cache_path == ""
    # No path at all, so `save()` cannot write one by accident.
    assert _cache_for(cfg).path is None


def test_a_configured_cache_path_still_persists(tmp_path):
    from lintranslator.pipeline import _cache_for

    cfg = Config()
    cfg.cache_path = str(tmp_path / "c.json")
    cache = _cache_for(cfg)
    cache.put("on screen", "画面")
    cache.save()
    assert (tmp_path / "c.json").exists()


# --------------------------------------------------------------------------- #
# Config + preprocessing
# --------------------------------------------------------------------------- #
def test_region_fraction_and_pixel_resolution():
    frac = Region(0.5, 0.5, 0.25, 0.25, "fraction")
    assert frac.to_pixels(1000, 800) == (500, 400, 250, 200)
    px = Region(10, 20, 30, 40, "pixels")
    assert px.to_pixels(1000, 800) == (10, 20, 30, 40)


def test_region_is_clamped_into_the_screen():
    # A region hanging off the edge must not produce a negative/empty box.
    x, y, w, h = Region(900, 700, 500, 500, "pixels").to_pixels(1000, 800)
    assert (x, y, w, h) == (900, 700, 100, 100)
    assert w >= 1 and h >= 1


def test_config_roundtrip_preserves_nested_region(tmp_path):
    cfg = Config()
    cfg.capture.region = Region(0.1, 0.2, 0.3, 0.4, "fraction")
    cfg.ocr.langs = "eng+jpn"
    path = tmp_path / "config.json"
    cfg.save(path)

    loaded = Config.load(path)
    assert loaded.capture.region.mode == "fraction"
    assert loaded.capture.region.w == 0.3
    assert loaded.ocr.langs == "eng+jpn"


def test_an_old_single_api_key_migrates_to_the_configured_backend(tmp_path):
    """The old field recorded no backend, so the configured one is the answer.

    It is also the right answer for every config Settings wrote: the field was
    filled in while that backend was selected. Without this the key would sit
    there meaning "for all of them", which is the bug being fixed.
    """
    path = tmp_path / "config.json"
    path.write_text(
        '{"translate": {"backend": "deepl", "api_key": "deepl-legacy"}}'
    )
    cfg = Config.load(path)
    assert cfg.translate.api_keys == {"deepl": "deepl-legacy"}
    assert cfg.translate.api_key is None, "the shared field must not survive the load"
    assert cfg.warnings == [], "a working config must not warn about anything"


def test_a_key_that_names_its_issuer_is_filed_under_that_backend(tmp_path):
    """The case that prompted this: an OpenRouter key left in the shared field
    while the backend was on `chat`, which sent it to a local Ollama server as a
    bearer token. The configured backend is the only backend the old field
    records, and here it is the wrong one - the key says so itself."""
    path = tmp_path / "config.json"
    path.write_text('{"translate": {"backend": "chat", "api_key": "sk-or-v1-abc"}}')
    cfg = Config.load(path)
    assert cfg.translate.api_keys == {"openrouter": "sk-or-v1-abc"}
    assert any("openrouter" in w for w in cfg.warnings), cfg.warnings


def test_an_unrecognised_key_stays_with_the_configured_backend(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"translate": {"backend": "chat", "api_key": "gateway-token"}}')
    cfg = Config.load(path)
    assert cfg.translate.api_keys == {"chat": "gateway-token"}
    assert cfg.warnings == [], "nothing was moved, so there is nothing to say"


def test_saving_drops_the_old_single_api_key_field(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"translate": {"backend": "openrouter", "api_key": "sk-or-x"}}')
    Config.load(path).save()
    raw = json.loads(path.read_text())
    assert raw["translate"]["api_keys"] == {"openrouter": "sk-or-x"}
    assert "api_key" not in raw["translate"], (
        "a field with no backend attached is what leaked the key in the first place"
    )


def test_a_key_stored_for_another_backend_is_not_a_key_for_this_one():
    """The leak: a DeepL key was offered to whichever backend was selected."""
    from lintranslator.translate import TranslatorError, build_translator

    cfg = TranslateConfig(backend="google", target_lang="jpn_Jpan", api_keys={"deepl": "d"})
    with pytest.raises(TranslatorError) as exc:
        build_translator(cfg)
    assert "needs an api_key" in str(exc.value)


def test_each_backend_resolves_its_own_key(monkeypatch):
    from lintranslator.translate import resolve_api_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = TranslateConfig(
        backend="deepl", api_keys={"deepl": "d-key", "openrouter": "or-key"}
    )
    assert resolve_api_key(cfg, "DEEPL_API_KEY") == "d-key"
    cfg.backend = "openrouter"
    assert resolve_api_key(cfg, "OPENROUTER_API_KEY") == "or-key"
    cfg.backend = "openai"
    assert resolve_api_key(cfg, "OPENAI_API_KEY") is None


def test_preprocess_upscales_and_grayscales():
    img = _frame("test", size=(100, 20))
    out = preprocess(img, upscale=3.0, autocontrast=True)
    assert out.mode == "L"
    assert out.size == (300, 60)


def test_a_shorter_new_line_replaces_the_held_one():
    """Regression: a shorter line could never replace a longer held one.

    `_held_text` was only updated when the new read was at least as long as the
    held one. A shorter line therefore differed from the held text on *every*
    poll: the stability window restarted each time and nothing was translated
    again until a longer line happened to appear. Measured on a live screen with
    our own status line inside the box - dropping that line is exactly what makes
    the game's text shorter than what was held.
    """
    long_line = (
        "For lack of any meaningful help they could provide; given the nature of this trial."
    )
    short_line = "Yes, of course."
    settler = TextSettler(settle_frames=2, settle_window=1.0, settle_max_wait=8.0)

    assert settler.observe(long_line, 0.0) is False
    assert settler.held == long_line

    # A different, shorter line must be adopted straight away...
    assert settler.observe(short_line, 0.5) is False
    assert settler.held == short_line, "the stale longer line is still held"

    # ...and released on the normal window, not held forever.
    assert settler.observe(short_line, 1.2) is False
    assert settler.observe(short_line, 1.6) is True


def test_jitter_of_one_line_is_still_not_a_new_line():
    """The fix above must not undo the reason 'keep the longer read' exists:
    OCR noise on one line must not restart the stability window."""
    settler = TextSettler(settle_frames=2, settle_window=1.0, settle_max_wait=8.0)
    settler.observe("The reactor is overheating, Captain.", 0.0)
    # A jittering read, one character shorter, half a second in. If that counted
    # as a new line the window would restart and the release would move to 1.5s.
    assert settler.observe("The reactor is overheating, Captain", 0.5) is False
    assert settler.observe("The reactor is overheating, Captain.", 1.2) is True


def test_force_re_translates_instead_of_serving_the_cache(tmp_path):
    """Re-read skips the cache: answering a "do that again" press with the same
    cached string would look like the button did nothing."""
    from lintranslator.translate import CachedTranslator, Translation

    calls: list[str] = []

    class Backend:
        name = "stub"
        model = "m"

        def translate(self, text: str) -> str:
            calls.append(text)
            return f"attempt {len(calls)}"

        def close(self) -> None:
            pass

    translator = CachedTranslator(Backend(), cache=TranslationCache(tmp_path / "c.json"))

    assert translator.translate("hello").target == "attempt 1"
    assert translator.translate("hello").target == "attempt 1"  # from the cache
    assert translator.last_was_cached is True
    assert calls == ["hello"]

    forced = translator.translate("hello", force=True)
    assert isinstance(forced, Translation)
    assert forced.target == "attempt 2", "the cached answer was served anyway"
    assert translator.last_was_cached is False
    assert calls == ["hello", "hello"]

    # The fresh result replaces the cached one, so the next repeat is cheap again.
    assert translator.translate("hello").target == "attempt 2"
    assert calls == ["hello", "hello"]
