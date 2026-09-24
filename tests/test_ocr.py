"""OCR language names and the tessdata download.

`ocr.langs` is a config value that becomes two things: tesseract's `-l`
argument, and `<name>.traineddata` under the tessdata directory. Both are
checked here, along with the pinned-revision download, which is the only place
this app fetches a binary and hands it to a C++ parser.
"""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from lintranslator.ocr import (
    LANG_NAME_RE,
    TESSDATA_SHA256,
    OcrError,
    TesseractOcr,
    bad_langs,
    download_tessdata,
    find_tessdata,
    model_state,
    parse_langs,
    split_langs,
)


# --------------------------------------------------------------------------- #
# Language names
# --------------------------------------------------------------------------- #
def test_languages_split_on_plus_and_whitespace():
    assert parse_langs("eng") == ["eng"]
    assert parse_langs("eng+jpn") == ["eng", "jpn"]
    assert parse_langs("eng jpn") == ["eng", "jpn"]
    assert parse_langs("  eng + jpn  ") == ["eng", "jpn"]
    assert parse_langs("") == []


@pytest.mark.parametrize(
    "value",
    ["../evil", "eng/../../x", "..", "/etc/passwd", "eng;rm -rf /", "e" * 40, "eng!", "ENG"],
)
def test_a_name_that_is_not_a_language_name_is_refused(value):
    """Path traversal and shell-shaped noise both stop here."""
    with pytest.raises(OcrError) as exc:
        parse_langs(value)
    assert "language name" in str(exc.value)


def test_bad_langs_reports_without_raising_for_the_ui():
    assert bad_langs("eng") == []
    assert bad_langs("eng+../x") == ["../x"]
    assert split_langs("eng+jpn") == ["eng", "jpn"]


def test_the_validator_agrees_with_the_pattern_it_documents():
    assert LANG_NAME_RE.match("chi_sim")
    assert not LANG_NAME_RE.match("chi-sim")
    assert not LANG_NAME_RE.match("")


def test_a_bad_name_is_refused_before_any_download(monkeypatch, tmp_path):
    """No network call: the check happens first."""
    def explode(*_a, **_kw):  # pragma: no cover - it must not be reached
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr("lintranslator.ocr.urllib.request.urlopen", explode)
    with pytest.raises(OcrError):
        download_tessdata(["../evil"], tmp_path)


def test_find_tessdata_refuses_a_bad_name(tmp_path):
    with pytest.raises(OcrError):
        find_tessdata(["../evil"], str(tmp_path))


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> bytes:
        # The real HTTPResponse takes a byte count, and the download path passes
        # one so that a hostile or broken server cannot stream forever.
        if size is None or size < 0:
            return self._payload
        return self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_a_language_without_a_pinned_digest_is_not_downloaded(monkeypatch, tmp_path):
    """Fail closed: otherwise the app installs whatever the URL serves."""
    called = False

    def urlopen(*_a, **_kw):  # pragma: no cover - it must not be reached
        nonlocal called
        called = True
        return FakeResponse(b"x" * 4096)

    monkeypatch.setattr("lintranslator.ocr.urllib.request.urlopen", urlopen)
    with pytest.raises(OcrError) as exc:
        download_tessdata(["xyz_not_pinned"], tmp_path)
    assert not called
    assert "checksum" in str(exc.value)
    assert not (tmp_path / "xyz_not_pinned.traineddata").exists()


def test_the_opt_in_allows_an_unpinned_language(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen",
        lambda *a, **kw: FakeResponse(b"y" * 4096),
    )
    dest = download_tessdata(["xyz_not_pinned"], tmp_path, allow_unverified=True)
    assert (dest / "xyz_not_pinned.traineddata").read_bytes() == b"y" * 4096


def test_a_payload_that_does_not_match_the_pinned_digest_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen",
        lambda *a, **kw: FakeResponse(b"not the real eng model" * 200),
    )
    with pytest.raises(OcrError) as exc:
        download_tessdata(["eng"], tmp_path)
    assert "checksum" in str(exc.value)
    # Nothing half-written is left behind for the next run to trust.
    assert list(tmp_path.iterdir()) == []


def test_a_payload_matching_the_pinned_digest_is_installed(monkeypatch, tmp_path):
    payload = b"pretend eng model" * 200
    monkeypatch.setitem(
        TESSDATA_SHA256, "fakelang", hashlib.sha256(payload).hexdigest()
    )
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen", lambda *a, **kw: FakeResponse(payload)
    )
    dest = download_tessdata(["fakelang"], tmp_path)
    assert (dest / "fakelang.traineddata").read_bytes() == payload
    assert not (dest / "fakelang.traineddata.part").exists()


def test_the_download_url_is_pinned_to_a_revision():
    from lintranslator.ocr import TESSDATA_REVISION, TESSDATA_URL

    assert "/main/" not in TESSDATA_URL
    assert TESSDATA_REVISION in TESSDATA_URL


def test_an_existing_file_matching_the_pin_is_not_downloaded_again(monkeypatch, tmp_path):
    payload = b"pretend eng model" * 200
    monkeypatch.setitem(TESSDATA_SHA256, "eng", hashlib.sha256(payload).hexdigest())
    (tmp_path / "eng.traineddata").write_bytes(payload)
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen",
        lambda *a, **kw: pytest.fail("should not re-download a file that matches"),
    )
    download_tessdata(["eng"], tmp_path)
    assert (tmp_path / "eng.traineddata").read_bytes() == payload


def test_an_existing_file_that_does_not_match_the_pin_is_replaced(monkeypatch, tmp_path):
    """A truncated or tampered file is not trusted just because it is there."""
    payload = b"the real eng model" * 200
    monkeypatch.setitem(TESSDATA_SHA256, "eng", hashlib.sha256(payload).hexdigest())
    (tmp_path / "eng.traineddata").write_bytes(b"truncated garbage")
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen", lambda *a, **kw: FakeResponse(payload)
    )
    download_tessdata(["eng"], tmp_path)
    assert (tmp_path / "eng.traineddata").read_bytes() == payload


# --------------------------------------------------------------------------- #
# What is on this machine, for the Settings chooser
# --------------------------------------------------------------------------- #
def test_installed_models_reports_what_the_directories_hold(monkeypatch, tmp_path):
    """The chooser says `ready` or `not installed`, and only the disk knows.

    `osd` is a file like any other and is reported too: this answers "what is
    here", and the caller decides which of them it recognises.
    """
    from lintranslator import ocr as ocr_mod

    app_data = tmp_path / "data"
    (app_data / "tessdata").mkdir(parents=True)
    (app_data / "tessdata" / "kor.traineddata").write_bytes(b"x")
    (app_data / "tessdata" / "osd.traineddata").write_bytes(b"x")
    (app_data / "tessdata" / "notes.txt").write_text("not a model")

    configured = tmp_path / "configured"
    configured.mkdir()
    (configured / "afr.traineddata").write_bytes(b"x")

    monkeypatch.setattr(ocr_mod.paths, "DATA_DIR", app_data)
    monkeypatch.setattr(ocr_mod, "SYSTEM_TESSDATA_CANDIDATES", ())

    assert ocr_mod.installed_models() == {"kor", "osd"}
    # A configured tessdata dir is searched as well, because `find_tessdata`
    # will take a file from there.
    assert ocr_mod.installed_models(str(configured)) == {"afr", "kor", "osd"}


def test_installed_models_finds_a_system_tessdata_dir(monkeypatch, tmp_path):
    from lintranslator import ocr as ocr_mod

    system = tmp_path / "usr-share-tessdata"
    system.mkdir()
    (system / "jpn.traineddata").write_bytes(b"x")
    monkeypatch.setattr(ocr_mod.paths, "DATA_DIR", tmp_path / "empty")
    monkeypatch.setattr(ocr_mod, "SYSTEM_TESSDATA_CANDIDATES", (str(system),))

    assert ocr_mod.installed_models() == {"jpn"}


def test_installed_models_on_a_machine_with_nothing_installed(monkeypatch, tmp_path):
    from lintranslator import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod.paths, "DATA_DIR", tmp_path / "empty")
    monkeypatch.setattr(ocr_mod, "SYSTEM_TESSDATA_CANDIDATES", ())
    assert ocr_mod.installed_models() == set()


def test_model_state_separates_here_from_fetchable_from_unavailable():
    """`grc` ships with tesseract but has no pinned digest, so the app will not
    download it - a distinction that is invisible in a config file."""
    installed = {"eng"}
    assert model_state("eng", installed) == "installed"
    assert model_state("rus", installed) == "download"
    assert model_state("grc", installed) == "missing"
    assert "rus" in TESSDATA_SHA256 and "grc" not in TESSDATA_SHA256


def test_a_truncated_download_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen", lambda *a, **kw: FakeResponse(b"tiny")
    )
    with pytest.raises(OcrError):
        download_tessdata(["eng"], tmp_path)


# --------------------------------------------------------------------------- #
# What actually reaches `-l`
# --------------------------------------------------------------------------- #
def test_a_space_separated_list_reaches_tesseract_with_pluses(monkeypatch, tmp_path):
    """`-l "kor eng"` is not two languages, it is one model named "kor eng".

    Measured, tesseract answers that with "Failed loading language 'kor eng'" and
    "Tesseract couldn't load any languages!", so the read fails outright for a
    value `split_langs` and the Settings field both accept. The config and
    `--langs` are hand-edited, so the argument is normalised rather than trusted.
    """
    import pytesseract

    seen: dict = {}

    def fake(image, **kwargs):
        seen.update(kwargs)
        return {
            "text": ["오래간만이에요"],
            "conf": ["92"],
            "block_num": [1],
            "par_num": [1],
            "line_num": [1],
            "left": [0],
            "top": [0],
            "width": [10],
            "height": [10],
        }

    monkeypatch.setattr(pytesseract, "image_to_data", fake)
    ocr = TesseractOcr(langs="kor eng", upscale=1.0, autocontrast=False)
    monkeypatch.setattr(ocr, "ensure_ready", lambda: tmp_path)

    result = ocr.read(Image.new("L", (40, 12), 255))

    assert seen["lang"] == "kor+eng", "the list reached -l in a form tesseract refuses"
    assert result.text == "오래간만이에요"
