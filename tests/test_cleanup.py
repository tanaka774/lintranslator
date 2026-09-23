"""What the app has downloaded, and how `remove` gets the space back.

Two things are checked hard here. The sizes must be right — a HuggingFace cache
stores the weights once in `blobs/` and links to them from `snapshots/`, so a
naive walk reports the checkpoint twice and a user deciding whether to delete
2.5 GB is deciding on a made-up number. And the deletion must stay inside the
app's own directories, because `ct2_model_dir` is a path the user can point
anywhere.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from lintranslator import cleanup, paths
from lintranslator.cli import main
from lintranslator.config import Config


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #
def test_a_directory_is_measured_once_even_when_it_is_linked_to(tmp_path):
    """The shape a HuggingFace cache actually has."""
    blob = tmp_path / "blobs" / "abcdef"
    blob.parent.mkdir()
    blob.write_bytes(b"x" * 5000)
    snapshot = tmp_path / "snapshots" / "main"
    snapshot.mkdir(parents=True)
    os.symlink(blob, snapshot / "pytorch_model.bin")

    assert cleanup.tree_size(tmp_path) == 5000


def test_a_single_file_is_measured(tmp_path):
    target = tmp_path / "model.bin"
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
# Where the caches are
# --------------------------------------------------------------------------- #
def test_the_huggingface_cache_follows_its_own_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    assert cleanup.hf_cache_root() == tmp_path / "hf" / "hub"

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "direct"))
    assert cleanup.hf_cache_root() == tmp_path / "direct"


def test_a_repo_id_becomes_the_directory_name_the_cache_uses():
    assert cleanup.hf_repo_dir("facebook/nllb-200-distilled-600M").name == (
        "models--facebook--nllb-200-distilled-600M"
    )


# --------------------------------------------------------------------------- #
# What counts as a target
# --------------------------------------------------------------------------- #
def test_the_checkpoint_target_follows_the_backend(monkeypatch, tmp_path):
    """`translate.model` is an OpenRouter id for the chat backends.

    Reading it unconditionally produced a target named after a model that is
    never downloaded - and hid the real 2.5 GB one.
    """
    cfg = Config()
    cfg.translate.backend = "openrouter"
    cfg.translate.model = "google/gemini-2.5-flash-lite"
    checkpoint = next(t for t in cleanup.targets(cfg) if t.name == "checkpoint")
    assert "nllb-200" in checkpoint.path.name

    cfg.translate.backend = "ct2"
    cfg.translate.model = "facebook/nllb-200-distilled-1.3B"
    checkpoint = next(t for t in cleanup.targets(cfg) if t.name == "checkpoint")
    assert checkpoint.path.name == "models--facebook--nllb-200-distilled-1.3B"


def test_the_cache_is_only_a_target_when_one_is_configured():
    cfg = Config()
    assert not any(t.name == "cache" for t in cleanup.targets(cfg))
    cfg.cache_path = "/tmp/somewhere/cache.json"
    assert any(t.name == "cache" for t in cleanup.targets(cfg))


def test_a_configured_weights_dir_that_is_not_where_the_weights_are_says_so():
    """Seen for real: a config from before the rename named the old directory,
    so 629 MB sat unread under a path nothing looked at."""
    cfg = Config()
    cfg.translate.ct2_model_dir = "/nonexistent/old/ct2"
    real = paths.default_ct2_dir()
    real.mkdir(parents=True)
    (real / "model.bin").write_bytes(b"z" * 4096)

    model = next(t for t in cleanup.targets(cfg) if t.name == "model")
    assert model.size == 0
    assert "does not exist" in model.note
    assert str(real) in model.note


def test_targets_come_back_biggest_first(tmp_path):
    cfg = Config()
    cfg.translate.ct2_model_dir = str(tmp_path / "model")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "model.bin").write_bytes(b"a" * 100_000)
    tessdata = paths.DATA_DIR / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"b" * 100)

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
    cfg = Config()
    model = next(t for t in cleanup.targets(cfg) if t.name == "model")
    assert cleanup.refusal(model.path) is None


def test_removing_reports_the_bytes_freed(tmp_path):
    target = cleanup.Target(name="model", path=tmp_path / "w", title="", detail="")
    target.path.mkdir()
    (target.path / "model.bin").write_bytes(b"c" * 2048)
    assert cleanup.remove(target) == 2048
    assert not target.path.exists()
    assert cleanup.remove(target) == 0  # already gone, not an error


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #
def _weights_dir() -> Path:
    """Where a real install keeps them: inside the app's own data directory.

    The conftest redirects `paths.DATA_DIR` per test, and `remove` only deletes
    inside directories the app owns - a weights dir of the test's own choosing
    is refused by design, which is a different test.
    """
    return paths.default_ct2_dir()


def _config_file(tmp_path, **translate) -> str:
    cfg = Config()
    cfg.translate.ct2_model_dir = str(_weights_dir())
    for key, value in translate.items():
        setattr(cfg.translate, key, value)
    path = tmp_path / "config.json"
    cfg.save(path)
    return str(path)


def test_the_report_changes_nothing(tmp_path, capsys):
    weights = _weights_dir()
    weights.mkdir(parents=True)
    (weights / "model.bin").write_bytes(b"d" * 4096)

    assert main(["--config", _config_file(tmp_path), "remove"]) == 0
    out = capsys.readouterr().out
    assert "model" in out and "4 KB" in out
    assert "nothing was removed" in out
    assert (weights / "model.bin").exists(), "the report must not delete anything"


def test_removing_a_target_with_yes_frees_it(tmp_path, capsys):
    weights = _weights_dir()
    weights.mkdir(parents=True)
    (weights / "model.bin").write_bytes(b"e" * 4096)

    assert main(["--config", _config_file(tmp_path), "remove", "model", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "removed model" in out and "freed 4 KB" in out
    assert not weights.exists()


def test_removing_without_a_terminal_and_without_yes_deletes_nothing(
    tmp_path, capsys, monkeypatch
):
    weights = _weights_dir()
    weights.mkdir(parents=True)
    (weights / "model.bin").write_bytes(b"f" * 4096)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["--config", _config_file(tmp_path), "remove", "model"]) == 1
    assert "without --yes" in capsys.readouterr().err
    assert (weights / "model.bin").exists()


def test_answering_no_deletes_nothing(tmp_path, capsys, monkeypatch):
    weights = _weights_dir()
    weights.mkdir(parents=True)
    (weights / "model.bin").write_bytes(b"g" * 4096)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main(["--config", _config_file(tmp_path), "remove", "model"]) == 0
    assert "nothing removed" in capsys.readouterr().out
    assert (weights / "model.bin").exists()


def test_a_configured_path_outside_the_app_is_refused_even_with_yes(
    tmp_path, capsys, monkeypatch
):
    """`--yes` consents to deleting the app's files, not the user's own."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "model.bin").write_bytes(b"h" * 4096)
    config = _config_file(tmp_path)
    cfg = Config.load(config)
    cfg.translate.ct2_model_dir = str(elsewhere)
    cfg.save(config)

    assert main(["--config", config, "remove", "model", "--yes"]) == 1
    assert "refusing to remove" in capsys.readouterr().err
    assert (elsewhere / "model.bin").exists()


def test_removing_something_that_is_not_there_is_not_an_error(tmp_path, capsys):
    assert main(["--config", _config_file(tmp_path), "remove", "model", "--yes"]) == 0
    assert "nothing to remove" in capsys.readouterr().out


def test_all_skips_what_is_absent(tmp_path, capsys):
    tessdata = paths.DATA_DIR / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "eng.traineddata").write_bytes(b"i" * 2048)

    assert main(["--config", _config_file(tmp_path), "remove", "all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "removed tessdata" in out
    assert "removed model" not in out, "an absent target must not be reported as removed"
    assert not tessdata.exists()


def test_the_report_says_what_it_will_not_touch(tmp_path, capsys):
    """The app's checkpoint shares a cache with everything else the user runs."""
    other = cleanup.hf_cache_root() / "models--Qwen--Qwen3-TTS-12Hz-0.6B-Base"
    other.mkdir(parents=True)
    (other / "model.safetensors").write_bytes(b"j" * 300_000)

    assert main(["--config", _config_file(tmp_path), "remove"]) == 0
    out = capsys.readouterr().out
    assert "not ours, not touched" in out
    assert "1 other model(s)" in out
    assert other.exists(), "a report must not touch another project's models"


def test_removing_the_checkpoint_leaves_the_other_models_alone(tmp_path, capsys):
    mine = cleanup.hf_repo_dir("facebook/nllb-200-distilled-600M")
    mine.mkdir(parents=True)
    (mine / "tokenizer.json").write_bytes(b"k" * 2048)
    theirs = cleanup.hf_cache_root() / "models--Qwen--something"
    theirs.mkdir(parents=True)
    (theirs / "model.safetensors").write_bytes(b"l" * 4096)

    assert main(["--config", _config_file(tmp_path), "remove", "checkpoint", "--yes"]) == 0
    assert not mine.exists()
    assert theirs.exists()


def test_the_report_names_the_command_for_each_target(tmp_path, capsys):
    assert main(["--config", _config_file(tmp_path), "remove"]) == 0
    out = capsys.readouterr().out
    for name in ("checkpoint", "model", "tessdata"):
        assert f"lintranslator remove {name}" in out
