"""OCR language names and the tessdata download.

`ocr.langs` is a config value that becomes two things: tesseract's `-l`
argument, and `<name>.traineddata` under the tessdata directory. Both are
checked here, along with the pinned-revision download, which is the only place
this app fetches a binary and hands it to a C++ parser.
"""
from __future__ import annotations

import hashlib

import pytest

from lintranslator.ocr import (
    LANG_NAME_RE,
    TESSDATA_SHA256,
    OcrError,
    bad_langs,
    download_tessdata,
    find_tessdata,
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


def test_language_data_left_under_the_old_name_is_still_found(monkeypatch, tmp_path):
    """The pre-rename XDG data dir is read, not ignored.

    The weights already work this way (`paths.default_ct2_dir`), and the same
    argument applies here: re-downloading the language data because the app was
    renamed is wasted work, and the copy on disk is already checksum-verified.
    """
    from lintranslator import paths

    legacy = tmp_path / "old-data" / "tessdata"
    legacy.mkdir(parents=True)
    (legacy / "eng.traineddata").write_bytes(b"x" * 2048)
    monkeypatch.setattr(paths, "LEGACY_XDG_DATA_DIR", tmp_path / "old-data")
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen",
        lambda *a, **kw: pytest.fail("should not download what is already on disk"),
    )

    assert find_tessdata(["eng"]) == legacy


def test_a_new_install_still_prefers_its_own_data_dir(monkeypatch, tmp_path):
    """The legacy directory is a fallback, not a new home."""
    from lintranslator import paths

    legacy = tmp_path / "old-data" / "tessdata"
    legacy.mkdir(parents=True)
    (legacy / "eng.traineddata").write_bytes(b"old" * 512)
    current = paths.DATA_DIR / "tessdata"
    current.mkdir(parents=True)
    (current / "eng.traineddata").write_bytes(b"new" * 512)
    monkeypatch.setattr(paths, "LEGACY_XDG_DATA_DIR", tmp_path / "old-data")

    assert find_tessdata(["eng"]) == current


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

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


def test_an_existing_file_is_not_downloaded_again(monkeypatch, tmp_path):
    (tmp_path / "eng.traineddata").write_bytes(b"already here")
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen",
        lambda *a, **kw: pytest.fail("should not re-download an existing file"),
    )
    download_tessdata(["eng"], tmp_path)
    assert (tmp_path / "eng.traineddata").read_bytes() == b"already here"


def test_a_truncated_download_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lintranslator.ocr.urllib.request.urlopen", lambda *a, **kw: FakeResponse(b"tiny")
    )
    with pytest.raises(OcrError):
        download_tessdata(["eng"], tmp_path)
