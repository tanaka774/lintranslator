"""What the app has downloaded, and how `remove` gets the space back.

Two things are checked hard here. The sizes must be right, so a user deciding
whether to delete something is not deciding on a made-up number. And the deletion
must stay inside the app's own directories, because `cache_path` is a path the
user can point anywhere.

The list of targets is short on purpose: the app ships no translation model, so
the only things it downloads are OCR language data and the optional translation
cache.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lintranslator import cleanup, paths
from lintranslator.cli import main
from lintranslator.config import Config


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #
def test_a_linked_file_is_measured_once(tmp_path):
    """A symlink is counted where it points, not twice."""
    real = tmp_path / "blobs" / "abcdef"
    real.parent.mkdir()
    real.write_bytes(b"x" * 5000)
    link_dir = tmp_path / "snapshots" / "main"
    link_dir.mkdir(parents=True)
    os.symlink(real, link_dir / "model.bin")

    assert cleanup.tree_size(link_dir) == 0
    assert cleanup.tree_size(tmp_path) == 5000


def test_a_single_file_is_measured(tmp_path):
    target = tmp_path / "cache.json"
    target.write_bytes(b"y" * 1234)
    assert cleanup.tree_size(target) == 1234


def test_a_missing_path_is_zero(tmp_path):
    assert cleanup.tree_size(tmp_path / "nope") == 0


@pytest.mark.parametrize(
    "size,expected",
    [(0, "0 B"), (999, "999 B"), (1500, "2 KB"), (7_000_000, "7 MB"), (2_500_000_000, "2.50 GB")],
)
def test_sizes_read_the_way_people_say_them(size, expected):
    assert cleanup.format_size(size) == expected


# --------------------------------------------------------------------------- #
# What counts as a target
# --------------------------------------------------------------------------- #
def test_the_only_thing_the_app_downloads_is_language_data():
    """There is no checkpoint target, because the app has no checkpoint.

    A model the user serves is theirs, and this command has no business offering
    to delete it.
    """
    cfg = Config()
    assert [t.name for t in cleanup.targets(cfg)] == ["tessdata"]


def test_the_cache_is_only_a_target_when_one_is_configured():
    cfg = Config()
    assert not any(t.name == "cache" for t in cleanup.targets(cfg))
    cfg.cache_path = "/tmp/somewhere/cache.json"
    assert any(t.name == "cache" for t in cleanup.targets(cfg))


def test_targets_come_back_biggest_first(tmp_path):
    cfg = Config()
    cfg.cache_path = str(paths.CACHE_DIR / "cache.json")
    tessdata = paths.DATA_DIR / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"b" * 100)
    Path(cfg.cache_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.cache_path).write_bytes(b"a" * 100_000)

    sizes = [t.size for t in cleanup.targets(cfg)]
    assert sizes == sorted(sizes, reverse=True)


# --------------------------------------------------------------------------- #
# Staying inside the app's own directories
# --------------------------------------------------------------------------- #
def test_a_path_the_user_chose_themselves_is_refused(tmp_path):
    outside = tmp_path / "my-own-models"
    outside.mkdir()
    why = cleanup.refusal(outside)
    assert why is not None and "not inside" in why


def test_a_path_inside_the_app_directories_is_allowed():
    assert cleanup.refusal(paths.DATA_DIR / "tessdata") is None
    assert cleanup.refusal(paths.CACHE_DIR / "cache.json") is None


def test_removing_reports_the_bytes_freed(tmp_path):
    target = cleanup.Target(name="tessdata", path=tmp_path / "w", title="", detail="")
    target.path.mkdir()
    (target.path / "eng.traineddata").write_bytes(b"c" * 2048)
    assert cleanup.remove(target) == 2048
    assert not target.path.exists()
    assert cleanup.remove(target) == 0  # already gone, not an error


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def _tessdata() -> Path:
    """Where a real install keeps it: inside the app's own data directory.

    The conftest redirects `paths.DATA_DIR` per test, and `remove` only deletes
    inside directories the app owns - a path of the test's own choosing is refused
    by design, which is a different test.
    """
    return paths.DATA_DIR / "tessdata"


def _config_file(tmp_path, **translate) -> str:
    cfg = Config()
    for key, value in translate.items():
        setattr(cfg.translate, key, value)
    path = tmp_path / "config.json"
    cfg.save(path)
    return str(path)


def test_the_report_changes_nothing(tmp_path, capsys):
    tessdata = _tessdata()
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"d" * 4096)

    assert main(["--config", _config_file(tmp_path), "remove"]) == 0
    out = capsys.readouterr().out
    assert "tessdata" in out and "4 KB" in out
    assert "nothing was removed" in out
    assert (tessdata / "eng.traineddata").exists(), "the report must not delete anything"


def test_removing_a_target_with_yes_frees_it(tmp_path, capsys):
    tessdata = _tessdata()
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"e" * 4096)

    assert main(["--config", _config_file(tmp_path), "remove", "tessdata", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "removed tessdata" in out and "freed 4 KB" in out
    assert not tessdata.exists()


def test_removing_without_a_terminal_and_without_yes_deletes_nothing(
    tmp_path, capsys, monkeypatch
):
    tessdata = _tessdata()
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"f" * 4096)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["--config", _config_file(tmp_path), "remove", "tessdata"]) == 1
    assert "without --yes" in capsys.readouterr().err
    assert (tessdata / "eng.traineddata").exists()


def test_answering_no_deletes_nothing(tmp_path, capsys, monkeypatch):
    tessdata = _tessdata()
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"g" * 4096)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main(["--config", _config_file(tmp_path), "remove", "tessdata"]) == 0
    assert "nothing removed" in capsys.readouterr().out
    assert (tessdata / "eng.traineddata").exists()


def test_a_configured_path_outside_the_app_is_refused_even_with_yes(
    tmp_path, capsys, monkeypatch
):
    """`--yes` consents to deleting the app's files, not the user's own."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "cache.json").write_bytes(b"h" * 4096)
    cfg = Config()
    cfg.cache_path = str(elsewhere / "cache.json")
    config = tmp_path / "config.json"
    cfg.save(config)

    assert main(["--config", str(config), "remove", "cache", "--yes"]) == 1
    assert "refusing to remove" in capsys.readouterr().err
    assert (elsewhere / "cache.json").exists()


def test_removing_something_that_is_not_there_is_not_an_error(tmp_path, capsys):
    assert main(["--config", _config_file(tmp_path), "remove", "tessdata", "--yes"]) == 0
    assert "nothing to remove" in capsys.readouterr().out


def test_all_skips_what_is_absent(tmp_path, capsys):
    tessdata = _tessdata()
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"i" * 2048)

    assert main(["--config", _config_file(tmp_path), "remove", "all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "removed tessdata" in out
    assert "removed cache" not in out, "an absent target must not be reported as removed"
    assert not tessdata.exists()


def test_the_report_names_the_command_for_each_target(tmp_path, capsys):
    assert main(["--config", _config_file(tmp_path), "remove"]) == 0
    out = capsys.readouterr().out
    assert "lintranslator remove tessdata" in out
