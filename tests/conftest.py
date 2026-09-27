"""Shared test setup: redirected XDG dirs, so a test never writes the real config."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_app_dirs(tmp_path, monkeypatch):
    """Point config, data, cache and the socket fallback at a per-test dir."""
    from lintranslator import config as config_mod
    from lintranslator import paths

    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        paths, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    # `config` imported the constant by name, so its namespace needs the same patch
    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    return tmp_path
