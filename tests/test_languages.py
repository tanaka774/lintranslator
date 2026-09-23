"""The language table: the codes, and what each backend is told for them.

The table is data generated from primary sources (`probe/gen_languages.py`), and
the failure it exists to prevent is invisible: NLLB maps an unknown or mistyped
language code to `<unk>` and returns plausible-looking garbage with no error at
all. So these tests are about the *shape* of the data - every code is one the
model can score, every backend gets a code it understands, and a language a
backend cannot express says so instead of guessing.
"""
from __future__ import annotations

import re

import pytest

from lintranslator import languages as L

FLORES_PATTERN = re.compile(r"^[a-z]{2,3}_[A-Z][a-z]{3}$")

# The languages DeepL documents, by FLORES code. Spelled out here rather than
# derived from the table so that a regeneration which silently drops one fails
# instead of agreeing with itself.
DEEPL_MUST_HAVE = (
    "arb_Arab", "bul_Cyrl", "ces_Latn", "dan_Latn", "deu_Latn", "ell_Grek",
    "eng_Latn", "spa_Latn", "est_Latn", "fin_Latn", "fra_Latn", "heb_Hebr",
    "hun_Latn", "ind_Latn", "ita_Latn", "jpn_Jpan", "kor_Hang", "lit_Latn",
    "lvs_Latn", "nob_Latn", "nld_Latn", "pol_Latn", "por_Latn", "ron_Latn",
    "rus_Cyrl", "slk_Latn", "slv_Latn", "swe_Latn", "tha_Thai", "tur_Latn",
    "ukr_Cyrl", "vie_Latn", "zho_Hans", "zho_Hant",
)


# --------------------------------------------------------------------------- #
# The table itself
# --------------------------------------------------------------------------- #
def test_the_table_holds_the_codes_nllb_was_trained_on():
    """202 is the count the NLLB tokenizer's vocabulary produces.

    A code that is not in that vocabulary is scored as `<unk>`, which is why the
    picker offers this list and nothing else.
    """
    assert len(L.CODES) == 202
    assert len(L.LANGUAGES) == 202


def test_every_code_looks_like_flores_200():
    for code in L.CODES:
        assert FLORES_PATTERN.match(code), code


def test_every_language_has_a_readable_name():
    for code, language in L.LANGUAGES.items():
        assert language.name.strip(), code
        assert language.name != code, f"{code} has no name, only a code"


def test_the_common_languages_are_all_real():
    """The picker pins these to the top, so a typo would hide a language."""
    for code in L.COMMON_CODES:
        assert code in L.CODES, code
    assert "eng_Latn" in L.COMMON_CODES and "jpn_Jpan" in L.COMMON_CODES


def test_scripts_drive_the_cjk_flag():
    assert L.is_cjk("jpn_Jpan") and L.is_cjk("kor_Hang")
    assert L.is_cjk("zho_Hans") and L.is_cjk("zho_Hant")
    assert not L.is_cjk("eng_Latn") and not L.is_cjk("rus_Cyrl")
    assert not L.is_cjk("not-a-code")


def test_the_iso_column_only_holds_two_letter_codes():
    for code, language in L.LANGUAGES.items():
        if language.iso is not None:
            assert re.fullmatch(r"[a-z]{2}", language.iso), (code, language.iso)


def test_the_deepl_column_only_holds_deepl_codes():
    documented = set(DEEPL_MUST_HAVE)
    for code, language in L.LANGUAGES.items():
        if language.deepl is not None:
            assert re.fullmatch(r"[A-Z]{2}", language.deepl), (code, language.deepl)
    for code in documented:
        assert L.LANGUAGES[code].deepl, f"{code} lost its DeepL code"


def test_languages_deepl_cannot_do_have_no_code():
    """The point of the column: `None` is what stops a wrong code being sent."""
    assert L.deepl_code("ceb_Latn") is None
    assert L.deepl_code("srp_Cyrl") is None
    assert L.deepl_code("kat_Geor") is None


def test_the_tesseract_column_holds_tessdata_stems():
    for code, language in L.LANGUAGES.items():
        if language.tesseract is not None:
            assert re.fullmatch(r"[a-z][a-z_0-9]*", language.tesseract), (
                code,
                language.tesseract,
            )


def test_the_two_chinese_scripts_do_not_share_an_ocr_language():
    """`chi_sim` and `chi_tra` are different files; one cannot stand for both."""
    assert L.tesseract_lang("zho_Hans") == "chi_sim"
    assert L.tesseract_lang("zho_Hant") == "chi_tra"


def test_languages_tesseract_cannot_read_have_no_ocr_language():
    assert L.tesseract_lang("zul_Latn") is None
    assert L.tesseract_lang("sat_Beng") is None


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def test_get_is_exact_and_case_sensitive():
    """FLORES codes are case-sensitive; `eng_latn` is not a language."""
    assert L.get("jpn_Jpan") is not None
    assert L.get("JPN_JPAN") is None
    assert L.get("jpn") is None
    assert L.get(None) is None and L.get("") is None


def test_language_name_prefers_the_exact_language():
    assert L.language_name("jpn_Jpan") == "Japanese"
    assert L.language_name("zho_Hant") == "Chinese (Traditional)"


def test_language_name_accepts_a_bare_code():
    """An older config or a CLI flag can carry "jpn" instead of "jpn_Jpan"."""
    assert L.language_name("jpn") == "Japanese"
    assert L.language_name("eng") == "English"


def test_language_name_passes_unknown_values_through():
    """Better a prompt that says "Japanese" than a silent rewrite to English."""
    assert L.language_name("Japanese") == "Japanese"
    assert L.language_name("xx_YY") == "xx_YY"
    assert L.language_name("") == ""


def test_iso_code_says_none_rather_than_guessing():
    assert L.iso_code("eng_Latn") == "en"
    assert L.iso_code("ceb_Latn") is None
    assert L.iso_code("not-a-code") is None


def test_short_code_is_the_label_form():
    assert L.short_code("eng_Latn") == "eng"
    assert L.short_code("zho_Hant") == "zho"
    # Unknown values are shown as they are, not tidied into something plausible.
    assert L.short_code("Japanese") == "Japanese"
    assert L.short_code("") == ""


# --------------------------------------------------------------------------- #
# Ordering and search (the picker's data)
# --------------------------------------------------------------------------- #
def test_ordered_lists_every_language_once_with_the_common_ones_first():
    ordered = L.ordered()
    assert len(ordered) == len(L.CODES)
    assert len({language.code for language in ordered}) == len(L.CODES)
    assert [language.code for language in ordered[: len(L.COMMON_CODES)]] == list(
        L.COMMON_CODES
    )


def test_search_matches_names_and_codes_case_insensitively():
    assert [language.code for language in L.search("japanese")] == ["jpn_Jpan"]
    assert [language.code for language in L.search("JPN")] == ["jpn_Jpan"]
    assert [language.code for language in L.search("zho")] == ["zho_Hans", "zho_Hant"]


def test_search_with_no_query_is_the_ordered_list():
    assert [language.code for language in L.search("")] == [
        language.code for language in L.ordered()
    ]


def test_search_that_matches_nothing_is_empty():
    assert L.search("klingon") == []


# --------------------------------------------------------------------------- #
# The check that actually decides whether a code means a language
# --------------------------------------------------------------------------- #
def test_the_table_matches_the_real_tokenizer_when_it_is_available(monkeypatch):
    """Ask the model, not the list, which codes exist.

    The published FLORES-200 lists disagree with each other and with the model -
    one carries `sat_Olck` and `arb_Latn`, which this tokenizer scores as
    `<unk>`, and omits `sat_Beng`, which it does not. The vocabulary is what
    decides, so when the tokenizer can be loaded this compares against it and
    skips when it cannot (no transformers, or no cached weights and no network).
    """
    transformers = pytest.importorskip("transformers")
    import os
    from pathlib import Path

    # Point at this checkout's cache, and stay offline: a test must not fetch
    # 600 MB of weights, and must not leave the environment changed either.
    if "HF_HOME" not in os.environ:
        monkeypatch.setenv(
            "HF_HOME", str(Path(L.__file__).resolve().parent.parent / ".hf")
        )
    if "HF_HUB_OFFLINE" not in os.environ:
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            "facebook/nllb-200-distilled-600M"
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"tokenizer unavailable: {type(exc).__name__}: {exc}")

    pattern = re.compile(r"^[a-z]{2,3}_[A-Z][a-z]{3}$")
    unk = tokenizer.unk_token_id
    accepted = {
        token
        for token in tokenizer.get_vocab()
        if pattern.match(token) and tokenizer.convert_tokens_to_ids(token) != unk
    }
    assert accepted == set(L.CODES), (
        f"only in the table: {sorted(set(L.CODES) - accepted)}; "
        f"only in the model: {sorted(accepted - set(L.CODES))}"
    )


# --------------------------------------------------------------------------- #
# The command line surface
# --------------------------------------------------------------------------- #
def test_the_languages_command_prints_the_codes_a_config_needs(capsys):
    """`lintranslator check` sends people here, so it has to name real codes."""
    from lintranslator.cli import main

    assert main(["languages", "korean"]) == 0
    printed = capsys.readouterr().out
    assert "kor_Hang" in printed
    assert "jpn_Jpan" not in printed

    assert main(["languages"]) == 0
    printed = capsys.readouterr().out
    for code in L.CODES:
        assert code in printed, code
