"""Shared test setup.

The app's state lives in the XDG directories now (see `tlkun.paths`), so an
unqualified `Config().save()` in a test would write the developer's real
`~/.config/tl-kun/config.json` - possibly wiping their API key, which is exactly
the accident the old repo-relative default caused once. Every test therefore
gets redirected dirs whether it asks for them or not.

The socket benefits too: the control server can bind under `tmp_path`, so the
picker tests exercise a *running* control channel instead of one that silently
failed to start.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_app_dirs(tmp_path, monkeypatch):
    """Point config, data, cache and the socket fallback at a per-test dir."""
    from tlkun import config as config_mod
    from tlkun import paths

    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", tmp_path / "legacy-data")
    monkeypatch.setattr(
        paths, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    monkeypatch.setattr(paths, "LEGACY_CONFIG_PATH", tmp_path / "legacy" / "config.json")
    # `config` imported the constant by name, so its namespace needs the same
    # treatment: `Config.load`/`save` read that module global.
    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    return tmp_path
