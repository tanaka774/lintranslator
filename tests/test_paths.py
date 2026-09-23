"""Where lintranslator keeps its state, and how it writes it.

The config can hold an API key in plain text, so the thing checked here is the
mode the file is *created* with: a file that exists for even a moment as 0644 has
already leaked its contents, and no later `chmod` undoes that.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from lintranslator import paths
from lintranslator.config import Config


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def test_a_private_file_is_created_0600(tmp_path):
    target = tmp_path / "secret.json"
    paths.write_private(target, "{}\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_mode_is_set_at_creation_not_afterwards(tmp_path):
    """`write_private` must not create the file wide and chmod it down.

    Sampled from the mode passed to `os.open`, which is the only way to see the
    difference: the finished file looks the same either way.
    """
    seen: list[int] = []
    real_open = os.open

    def spy(path, flags, mode=0o777):
        seen.append(mode)
        return real_open(path, flags, mode)

    original = paths.os.open
    paths.os.open = spy
    try:
        paths.write_private(tmp_path / "secret.json", "{}\n")
    finally:
        paths.os.open = original
    assert seen == [0o600]


def test_a_wide_mode_file_is_tightened_on_save(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{}")
    os.chmod(target, 0o644)

    paths.write_private(target, '{"a": 1}\n')
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"a": 1}


def test_tighten_reports_whether_it_changed_anything(tmp_path):
    target = tmp_path / "config.json"
    os.chmod(tmp_path, 0o700)
    target.write_text("{}")
    os.chmod(target, 0o644)
    assert paths.tighten(target) is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert paths.tighten(target) is False


def test_a_failed_write_leaves_no_partial_file(tmp_path):
    """A half-written .tmp is worse than nothing: a later run would read it."""
    target = tmp_path / "config.json"
    target.write_text('{"old": true}\n')

    # A non-string payload fails inside write(): the existing file must survive.
    with pytest.raises(TypeError):
        paths.write_private(target, {"not": "text"})  # type: ignore[arg-type]
    assert json.loads(target.read_text()) == {"old": True}
    assert list(tmp_path.iterdir()) == [target]


# --------------------------------------------------------------------------- #
# Config save/load
# --------------------------------------------------------------------------- #
def test_saving_the_config_is_private(tmp_path):
    cfg = Config()
    cfg.translate.api_key = "sk-or-v1-secret"
    written = cfg.save(tmp_path / "config.json")
    assert stat.S_IMODE(written.stat().st_mode) == 0o600


def test_saving_through_an_explicit_path_still_writes_there(tmp_path):
    """`--config` and the tools that pass a path must not be redirected."""
    target = tmp_path / "somewhere" / "other.json"
    cfg = Config()
    assert cfg.save(target) == target
    assert target.exists()


# --------------------------------------------------------------------------- #
# Directories
# --------------------------------------------------------------------------- #
def test_the_socket_fallback_lives_with_the_rest_of_the_state(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    from lintranslator.control import candidate_paths

    resolved = candidate_paths()
    assert resolved[-1] == paths.CACHE_DIR / "lintranslator.sock"


def test_the_weights_live_under_the_data_dir():
    assert paths.default_ct2_dir() == paths.DATA_DIR / paths.CT2_DIR_NAME


def test_the_home_variable_moves_every_directory_at_once(monkeypatch, tmp_path):
    """`LINTRANSLATOR_HOME` is the portable-install switch."""
    monkeypatch.setenv("LINTRANSLATOR_HOME", str(tmp_path / "portable"))
    assert paths._override() == tmp_path / "portable"


def test_the_xdg_directories_follow_their_environment(monkeypatch):
    """Nothing resolves to the real `$HOME` when the spec's variables are set."""
    monkeypatch.delenv("LINTRANSLATOR_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/cfg")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/data")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/cache")
    assert paths._xdg("XDG_CONFIG_HOME", ".config") == Path("/tmp/cfg/lintranslator")
    assert paths._xdg("XDG_DATA_HOME", ".local/share") == Path("/tmp/data/lintranslator")
    assert paths._xdg("XDG_CACHE_HOME", ".cache") == Path("/tmp/cache/lintranslator")
