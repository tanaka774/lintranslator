"""Where lintranslator keeps its state, and how it writes it.

The config can hold an API key in plain text, so two things are checked here
that are easy to get wrong and invisible when they are: the mode the file is
*created* with (a file that exists for a moment as 0644 has already leaked), and
the one-time move out of the source tree, which must never destroy the original.
"""
from __future__ import annotations

import json
import os
import stat

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
# The move out of the source tree
# --------------------------------------------------------------------------- #
@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """A fake old install: config plus data beside the source."""
    legacy_app = tmp_path / "checkout"
    legacy_app.mkdir()
    (legacy_app / "data" / "ct2" / "nllb-600m-int8").mkdir(parents=True)
    (legacy_app / "data" / "tessdata").mkdir(parents=True)
    (legacy_app / "data" / "cache.json").write_text("{}")
    monkeypatch.setattr(paths, "APP_DIR", legacy_app)
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy_app / "data")
    monkeypatch.setattr(paths, "LEGACY_CONFIG_PATH", legacy_app / "config.json")
    monkeypatch.setattr(
        paths, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    return legacy_app


def _write_legacy_config(legacy_app, **extra):
    raw = {
        "translate": {
            "backend": "openrouter",
            "api_key": "sk-or-v1-secret",
            "ct2_model_dir": str(legacy_app / "data" / "ct2" / "nllb-600m-int8"),
        },
        "cache_path": str(legacy_app / "data" / "cache.json"),
        **extra,
    }
    (legacy_app / "config.json").write_text(json.dumps(raw))
    return raw


def test_a_config_beside_the_source_is_copied_out_with_its_paths_repointed(legacy):
    _write_legacy_config(legacy)
    migration = paths.migrate_legacy_config()
    assert migration is not None

    moved = json.loads(migration.new_path.read_text())
    # The key came along - it is the user's config - but the state paths did
    # not stay in the checkout, or nothing has really moved.
    assert moved["translate"]["api_key"] == "sk-or-v1-secret"
    assert moved["translate"]["ct2_model_dir"] == str(paths.DATA_DIR / paths.CT2_DIR_NAME)
    assert moved["cache_path"] == str(paths.CACHE_DIR / "cache.json")
    assert set(migration.remapped) == {"translate.ct2_model_dir", "cache_path"}


def test_the_original_config_is_never_deleted(legacy):
    original = _write_legacy_config(legacy)
    paths.migrate_legacy_config()
    assert json.loads((legacy / "config.json").read_text()) == original


def test_the_copy_is_private_and_says_what_is_left_behind(legacy):
    _write_legacy_config(legacy)
    migration = paths.migrate_legacy_config()
    assert stat.S_IMODE(migration.new_path.stat().st_mode) == 0o600
    warning = paths.migration_warning(migration)
    assert "API key" in warning
    assert str(migration.old_path) in warning


def test_an_existing_xdg_config_is_never_overwritten(legacy, tmp_path):
    _write_legacy_config(legacy)
    target = tmp_path / "config" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"translate": {"api_key": "the one in use"}}')

    assert paths.migrate_legacy_config() is None
    assert json.loads(target.read_text())["translate"]["api_key"] == "the one in use"


def test_nothing_to_migrate_when_there_is_no_legacy_config(legacy):
    assert paths.migrate_legacy_config() is None


def test_a_path_outside_the_app_dir_is_left_alone(legacy):
    """Guessing at the user's own paths is how a migration eats someone's data."""
    raw = _write_legacy_config(legacy)
    raw["cache_path"] = "/var/tmp/my-own-cache.json"
    (legacy / "config.json").write_text(json.dumps(raw))
    migration = paths.migrate_legacy_config()
    assert migration is not None
    moved = json.loads(migration.new_path.read_text())
    assert moved["cache_path"] == "/var/tmp/my-own-cache.json"
    assert "cache_path" not in migration.remapped


def test_a_corrupt_legacy_config_is_not_migrated(legacy):
    (legacy / "config.json").write_text("{not json")
    assert paths.migrate_legacy_config() is None


def test_loading_without_a_path_migrates_and_says_so(legacy):
    _write_legacy_config(legacy)
    cfg = Config.load()
    assert cfg.path == paths.DEFAULT_CONFIG_PATH
    assert cfg.translate.api_key == "sk-or-v1-secret"
    assert any("moved your config" in w for w in cfg.warnings), cfg.warnings


def test_loading_an_explicit_path_never_migrates(legacy, tmp_path):
    _write_legacy_config(legacy)
    other = tmp_path / "explicit.json"
    other.write_text('{"translate": {"backend": "none"}}')
    cfg = Config.load(other)
    assert cfg.path == other
    assert cfg.warnings == []
    assert not paths.DEFAULT_CONFIG_PATH.exists()


def test_a_legacy_config_is_read_in_place_when_it_is_all_there_is(legacy):
    _write_legacy_config(legacy)
    assert paths.config_search_path() == legacy / "config.json"


# --------------------------------------------------------------------------- #
# The rename from tl-kun: state under the old XDG directories
# --------------------------------------------------------------------------- #
def _write_old_xdg_config(paths_mod, **extra):
    """A config where the previous release kept it: `~/.config/tl-kun`."""
    raw = {
        "translate": {"backend": "openrouter", "api_key": "sk-or-v1-old", **extra},
        "cache_path": "",
    }
    paths_mod.LEGACY_XDG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths_mod.LEGACY_XDG_CONFIG_PATH.write_text(json.dumps(raw))
    return raw


def test_a_config_from_before_the_rename_is_found_and_migrated():
    """The whole point: renaming the app must not look like a first run.

    Losing the config means losing the API key and every setting, and a user who
    does not notice simply re-enters it - so this is checked rather than assumed.
    """
    old = _write_old_xdg_config(paths)
    assert paths.config_search_path() == paths.LEGACY_XDG_CONFIG_PATH

    migration = paths.migrate_legacy_config()
    assert migration is not None
    assert migration.old_path == paths.LEGACY_XDG_CONFIG_PATH
    moved = json.loads(migration.new_path.read_text())
    assert moved["translate"]["api_key"] == "sk-or-v1-old"
    # Never deleted: if the copy is wrong, the user still has their settings.
    assert json.loads(paths.LEGACY_XDG_CONFIG_PATH.read_text()) == old


def test_the_old_xdg_config_wins_over_one_beside_the_source(legacy):
    """Both layouts existed; the XDG one is what the last release wrote."""
    _write_legacy_config(legacy)
    _write_old_xdg_config(paths)

    migration = paths.migrate_legacy_config()
    assert migration.old_path == paths.LEGACY_XDG_CONFIG_PATH
    moved = json.loads(migration.new_path.read_text())
    assert moved["translate"]["api_key"] == "sk-or-v1-old"


def test_an_existing_config_is_never_overwritten_by_the_old_one():
    _write_old_xdg_config(paths)
    paths.DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.DEFAULT_CONFIG_PATH.write_text('{"translate": {"api_key": "current"}}')

    assert paths.migrate_legacy_config() is None
    kept = json.loads(paths.DEFAULT_CONFIG_PATH.read_text())
    assert kept["translate"]["api_key"] == "current"


def test_the_warning_does_not_call_the_old_xdg_dir_the_source_tree():
    """The old location is `~/.config/tl-kun`, and saying otherwise misleads."""
    _write_old_xdg_config(paths)
    warning = paths.migration_warning(paths.migrate_legacy_config())
    assert "old location" in warning
    assert "source tree" not in warning
    assert "API key" in warning


def test_a_config_beside_the_source_still_says_source_tree(legacy):
    """The original wording has to survive: that one really is in the checkout."""
    _write_legacy_config(legacy)
    warning = paths.migration_warning(paths.migrate_legacy_config())
    assert "source tree" in warning


def test_nothing_to_migrate_when_the_old_xdg_dir_is_empty():
    assert not paths.LEGACY_XDG_CONFIG_PATH.exists()
    assert paths.migrate_legacy_config() is None
    assert paths.config_search_path() == paths.DEFAULT_CONFIG_PATH


def test_a_config_that_cannot_be_copied_is_used_where_it_is(monkeypatch):
    """A read-only or full `$HOME` must not turn into a failure to start.

    Found the honest way: the first run of the renamed app in a sandbox that
    permits writes only in the workspace raised `OSError: [Errno 30] Read-only
    file system` out of `Config.load` and the CLI died on a traceback - for a
    downgrade whose fallback was sitting right there.
    """
    _write_old_xdg_config(paths)

    def refuse(path, text, mode=0o600):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(paths, "write_private", refuse)

    migration = paths.migrate_legacy_config()
    assert migration is not None
    assert not migration.succeeded
    assert "Read-only" in migration.error
    # The message must not claim a move that did not happen.
    warning = paths.migration_warning(migration)
    assert "could not copy" in warning
    assert "moved your config" not in warning

    # And the settings are still there to be read.
    assert paths.config_search_path() == paths.LEGACY_XDG_CONFIG_PATH


def test_loading_still_works_when_the_copy_fails(monkeypatch):
    """The same failure, through the path the app actually takes."""
    _write_old_xdg_config(paths)

    def refuse(path, text, mode=0o600):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(paths, "write_private", refuse)

    cfg = Config.load()
    assert cfg.translate.api_key == "sk-or-v1-old"
    assert cfg.path == paths.LEGACY_XDG_CONFIG_PATH
    assert any("could not copy" in w for w in cfg.warnings), cfg.warnings


# --------------------------------------------------------------------------- #
# Directories
# --------------------------------------------------------------------------- #
def test_the_socket_fallback_lives_with_the_rest_of_the_state(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    from lintranslator.control import candidate_paths

    resolved = candidate_paths()
    assert resolved[-1] == paths.CACHE_DIR / "lintranslator.sock"


def test_weights_are_not_re_converted_just_because_a_path_moved(legacy):
    """An older in-tree install keeps being used until it is moved."""
    assert paths.default_ct2_dir() == legacy / "data" / "ct2" / "nllb-600m-int8"


def test_weights_go_to_the_data_dir_once_the_new_one_exists(legacy):
    target = paths.DATA_DIR / paths.CT2_DIR_NAME
    target.mkdir(parents=True)
    assert paths.default_ct2_dir() == target


def test_weights_from_before_the_rename_are_used_where_they_are():
    """600 MB is not copied to rename a directory.

    A copy would double the disk cost, and a move that fails part way would lose
    the one artefact here that is expensive to rebuild.
    """
    old = paths.LEGACY_XDG_DATA_DIR / paths.CT2_DIR_NAME
    old.mkdir(parents=True)
    assert paths.default_ct2_dir() == old


def test_the_new_data_dir_wins_once_weights_are_there_too():
    old = paths.LEGACY_XDG_DATA_DIR / paths.CT2_DIR_NAME
    old.mkdir(parents=True)
    new = paths.DATA_DIR / paths.CT2_DIR_NAME
    new.mkdir(parents=True)
    assert paths.default_ct2_dir() == new


def test_the_old_home_variable_still_moves_the_state(monkeypatch, tmp_path):
    """`TLKUN_HOME` was the documented portable-install switch; keep it working."""
    monkeypatch.delenv("LINTRANSLATOR_HOME", raising=False)
    monkeypatch.setenv("TLKUN_HOME", str(tmp_path / "portable"))
    assert paths._override() == tmp_path / "portable"


def test_the_new_home_variable_wins_over_the_old(monkeypatch, tmp_path):
    monkeypatch.setenv("LINTRANSLATOR_HOME", str(tmp_path / "new"))
    monkeypatch.setenv("TLKUN_HOME", str(tmp_path / "old"))
    assert paths._override() == tmp_path / "new"
