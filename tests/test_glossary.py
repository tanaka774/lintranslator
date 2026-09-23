"""Tests for the Phase 3 additions: glossary and display line handling.

The glossary has two failure modes that are easy to ship without noticing:

* boundaries: Python's `\\b` treats CJK as word characters, so a `\\b`-anchored
  entry for 管理者 never matches inside 管理者異常 - the exact case it exists for.
* caching: if the post-glossary text were cached, editing a glossary entry would
  appear to do nothing for lines already seen.
"""
from __future__ import annotations

from lintranslator.glossary import LIMBUS_GLOSSARY, Glossary
from lintranslator.ocr import OcrLine, OcrResult
from lintranslator.translate import CachedTranslator, Translation, TranslationCache


# --------------------------------------------------------------------------- #
# Glossary: matching
# --------------------------------------------------------------------------- #
def test_matches_english_terms_case_insensitively():
    g = Glossary(post={"Manager": "マネージャー"})
    assert g.apply_post("Manager left") == "マネージャー left"
    assert g.apply_post("manager left") == "マネージャー left"
    assert g.apply_post("MANAGER left") == "マネージャー left"


def test_does_not_match_inside_a_word():
    g = Glossary(post={"Manager": "マネージャー"})
    assert g.apply_post("Managers and management") == "Managers and management"


def test_matches_cjk_term_followed_by_more_cjk():
    """Regression: a \\b-anchored pattern fails here, because Python counts CJK
    as word characters, so the boundary between 者 and 異 is not a boundary."""
    g = Glossary(post={"管理者": "マネージャー"})
    assert g.apply_post("管理者異常が近づいてる") == "マネージャー異常が近づいてる"
    assert g.apply_post("管理者、結果") == "マネージャー、結果"


def test_longer_keys_win_over_their_own_substrings():
    g = Glossary(post={"Manager": "A", "Manager Assistant": "B"})
    assert g.apply_post("Manager Assistant") == "B"
    assert g.apply_post("Manager") == "A"


def test_replacements_do_not_cascade():
    """A->B while B->C must produce B, not C: one pass, no rescanning."""
    g = Glossary(post={"A": "B", "B": "C"})
    assert g.apply_post("A") == "B"


def test_case_sensitive_mode_is_opt_in():
    g = Glossary(post={"Manager": "マネージャー"}, case_sensitive=True)
    assert g.apply_post("Manager") == "マネージャー"
    assert g.apply_post("manager") == "manager"


def test_pre_and_post_are_independent():
    g = Glossary(pre={"Manager": "Executive Manager"}, post={"管理者": "マネージャー"})
    assert g.apply_pre("Manager!") == "Executive Manager!"
    assert g.apply_pre("Manager!") != g.apply_post("Manager!")
    assert g.apply_post("管理者") == "マネージャー"


def test_empty_glossary_is_a_no_op():
    g = Glossary()
    assert not g
    assert g.apply_pre("anything") == "anything"
    assert g.apply_post("anything") == "anything"


def test_from_config_accepts_flat_and_structured_forms():
    flat = Glossary.from_config({"Manager": "マネージャー"})
    assert flat.post == {"Manager": "マネージャー"}
    assert flat.pre == {}

    structured = Glossary.from_config(
        {"pre": {"a": "b"}, "post": {"c": "d"}, "case_sensitive": True}
    )
    assert structured.pre == {"a": "b"}
    assert structured.post == {"c": "d"}
    assert structured.case_sensitive is True


def test_from_config_handles_empty_values():
    assert not Glossary.from_config(None)
    assert not Glossary.from_config({})


def test_merge_layers_later_entries_on_top():
    base = Glossary(post={"管理者": "マネージャー", "A": "base"})
    user = Glossary(post={"A": "user"})
    merged = Glossary.merge(base, user)
    assert merged.apply_post("管理者") == "マネージャー"  # kept from base
    assert merged.apply_post("A") == "user"  # overridden by user


def test_builtin_limbus_glossary_fixes_the_manager_title():
    assert LIMBUS_GLOSSARY.apply_post("管理者異常が近づいてる") == "マネージャー異常が近づいてる"
    # It deliberately has no pre-map: rewriting the source measured worse.
    assert not LIMBUS_GLOSSARY.pre


# --------------------------------------------------------------------------- #
# Glossary + cache interaction
# --------------------------------------------------------------------------- #
class _EchoTranslator:
    """Records calls and returns a fixed string, standing in for a real model."""

    name = "echo"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return "管理者です"

    def close(self) -> None:
        pass


def test_glossary_is_applied_even_on_a_cache_hit(tmp_path):
    """The cache stores raw output, so editing the glossary must still take
    effect. Caching post-glossary text would silently ignore config changes."""
    backend = _EchoTranslator()
    cache = TranslationCache(tmp_path / "c.json")
    glossary = Glossary(post={"管理者": "マネージャー"})
    t = CachedTranslator(backend, cache, glossary)

    first = t.translate("Manager is here")
    assert first.target == "マネージャーです"
    assert not t.last_was_cached

    second = t.translate("Manager is here")
    assert second.target == "マネージャーです"
    assert t.last_was_cached
    assert len(backend.calls) == 1  # the model was not called twice


def test_swapping_the_glossary_changes_output_without_re_translating(tmp_path):
    backend = _EchoTranslator()
    cache = TranslationCache(tmp_path / "c.json")

    t1 = CachedTranslator(backend, cache, Glossary(post={"管理者": "マネージャー"}))
    assert t1.translate("hi").target == "マネージャーです"

    # Same cache, different glossary: the cached raw output is re-mapped.
    t2 = CachedTranslator(backend, cache, Glossary(post={"管理者": "支配人"}))
    assert t2.translate("hi").target == "支配人です"
    assert len(backend.calls) == 1


def test_pre_glossary_changes_the_cache_key(tmp_path):
    """A pre rewrite must produce a fresh translation, not a stale hit, because
    the model is being asked a different question."""
    backend = _EchoTranslator()
    cache = TranslationCache(tmp_path / "c.json")
    t = CachedTranslator(backend, cache, Glossary(pre={"Manager": "Executive Manager"}))

    t.translate("Manager left")
    assert backend.calls == ["Executive Manager left"]
    t.translate("Manager left")
    assert len(backend.calls) == 1  # cached under the rewritten key


# --------------------------------------------------------------------------- #
# Multi-line dialogue display
# --------------------------------------------------------------------------- #
def _result(*texts: str) -> OcrResult:
    return OcrResult(
        lines=[OcrLine(text=t, confidence=90.0, box=(0, 0, 10, 10)) for t in texts],
        elapsed=0.01,
        engine="test",
        lang="eng",
    )


def test_text_joins_wrapped_lines_for_translation():
    r = _result("The material below is the file", "concerning today's submission.")
    assert r.text == "The material below is the file concerning today's submission."


def test_display_text_preserves_the_game_line_breaks():
    """The panel should not show one long run where the game showed two lines."""
    r = _result("The material below is the file", "concerning today's submission.")
    assert r.display_text == "The material below is the file\nconcerning today's submission."


def test_visual_rows_counts_wrapped_lines():
    assert _result("one").visual_rows == 1
    assert _result("one", "two", "three").visual_rows == 3


def test_empty_result_has_empty_texts():
    r = _result()
    assert r.text == ""
    assert r.display_text == ""
    assert not r


def test_cache_is_namespaced_per_backend(tmp_path):
    """Switching backend must not serve the old backend's cached translation.

    Without namespacing, a source line translated by the local model would be
    returned after switching to a remote model - which looks exactly like the new
    model being no better.
    """
    cache = TranslationCache(tmp_path / "c.json")

    class ModelA(_EchoTranslator):
        name = "modelA"
        model = "a"

    class ModelB(_EchoTranslator):
        name = "modelB"
        model = "b"

    a = CachedTranslator(ModelA(), cache, Glossary())
    b = CachedTranslator(ModelB(), cache, Glossary())
    assert a.translate("hello").target == "管理者です"
    first = b.translate("hello").target
    assert first == "管理者です"
    # Model B must have actually run, not read model A's entry.
    assert len(b.backend.calls) == 1
    assert a.cache_namespace != b.cache_namespace


def test_cache_is_namespaced_per_language_pair(tmp_path):
    """Changing the target language must not serve the old language's lines.

    The cache is keyed on the source text, so without the pair in the namespace
    every line already seen comes back in the *previous* target language. That
    reads as the language setting being ignored, and it is the failure mode that
    makes the setting look broken rather than merely stale.
    """
    cache = TranslationCache(tmp_path / "c.json")

    class Japanese(_EchoTranslator):
        name = "ct2"
        model = "nllb"
        source_lang = "eng_Latn"
        target_lang = "jpn_Jpan"

    class Korean(Japanese):
        target_lang = "kor_Hang"

    ja = CachedTranslator(Japanese(), cache, Glossary())
    ko = CachedTranslator(Korean(), cache, Glossary())
    assert ja.translate("hello").target == "管理者です"
    ko.translate("hello")
    # Korean must have run its own translation rather than reusing Japanese.
    assert len(ko.backend.calls) == 1
    assert ja.cache_namespace != ko.cache_namespace
    assert "eng_Latn>jpn_Jpan" in ja.cache_namespace
    assert "eng_Latn>kor_Hang" in ko.cache_namespace
