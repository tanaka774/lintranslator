"""The chooser's list of OCR models.

`ocr_languages.py` is generated data, so what is checked here is the properties
the Settings chooser depends on rather than the names themselves: that every
entry can be handed to `-l`, that nothing the app can fetch is missing from it,
and that the list can be searched and ordered.
"""
from __future__ import annotations

import pytest

from lintranslator import ocr_languages
from lintranslator.ocr import LANG_NAME_RE, TESSDATA_SHA256


def test_every_stem_is_a_name_tesseract_accepts():
    """The chooser cannot offer something the engine would refuse.

    A stem becomes tesseract's `-l` argument and a filename under the tessdata
    directory, and `parse_langs` refuses anything that is not a plain language
    name - so a table entry outside that pattern would be a tick that fails at
    read time.
    """
    bad = [model.stem for model in ocr_languages.MODELS if not LANG_NAME_RE.match(model.stem)]
    assert not bad, f"not valid tesseract language names: {bad}"


def test_everything_the_app_can_download_is_offered():
    """The other direction: nothing fetchable may be missing from the list.

    The 16 languages with a pinned checksum are the ones a first run installs by
    itself. If one of them were absent from the table, the only way to keep OCR
    reading it would be to hand-edit the config.
    """
    offered = {model.stem for model in ocr_languages.MODELS}
    assert set(TESSDATA_SHA256) <= offered, sorted(set(TESSDATA_SHA256) - offered)


def test_the_table_is_not_empty_and_has_no_duplicates():
    stems = [model.stem for model in ocr_languages.MODELS]
    assert len(stems) > 100, "tessdata_fast ships far more models than this"
    assert len(set(stems)) == len(stems)
    assert all(model.name.strip() for model in ocr_languages.MODELS)
    assert len({model.name for model in ocr_languages.MODELS}) == len(stems)


def test_ordered_is_what_someone_reads_not_what_a_file_is_called():
    """The list is read by name, so it is sorted by name."""
    names = [model.name.casefold() for model in ocr_languages.ordered()]
    assert names == sorted(names)
    assert {model.stem for model in ocr_languages.ordered()} == {
        model.stem for model in ocr_languages.MODELS
    }


def test_search_matches_the_name_and_the_stem():
    assert [m.stem for m in ocr_languages.search("korean")] == ["kor", "kor_vert"]
    assert [m.stem for m in ocr_languages.search("kor")] == ["kor", "kor_vert"]
    assert [m.stem for m in ocr_languages.search("KOR")] == ["kor", "kor_vert"]
    assert ocr_languages.search("") == ocr_languages.ordered()
    assert ocr_languages.search("   ") == ocr_languages.ordered()


def test_search_finds_a_language_by_a_word_in_the_middle_of_its_name():
    assert "grc" in [m.stem for m in ocr_languages.search("ancient greek")]


def test_search_that_matches_nothing_is_empty_not_an_error():
    assert ocr_languages.search("klingon") == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_stem_means_no_model(value):
    assert ocr_languages.get(value) is None


def test_get_is_exact_and_strips():
    assert ocr_languages.get("kor").name == "Korean"
    assert ocr_languages.get("  kor  ").stem == "kor"
    assert ocr_languages.get("ko") is None, "a partial stem is not a model"
    assert ocr_languages.get("zzz") is None


def test_the_model_names_its_own_stem():
    """The label says both, because the stem is what ends up in the config."""
    assert ocr_languages.get("chi_sim").label == "Chinese (simplified) — chi_sim"
