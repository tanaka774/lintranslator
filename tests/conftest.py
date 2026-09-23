"""Shared test setup.

The app's state lives in the XDG directories now (see `lintranslator.paths`), so an
unqualified `Config().save()` in a test would write the developer's real
`~/.config/lintranslator/config.json` - possibly wiping their API key, which is exactly
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
    from lintranslator import config as config_mod
    from lintranslator import paths

    # The pre-rename key variable is still honoured (`resolve_api_key` falls back
    # to it), so a developer who exported `TLKUN_API_KEY` before the rename would
    # leak it into every test that asserts no key is configured.
    monkeypatch.delenv("TLKUN_API_KEY", raising=False)

    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", tmp_path / "legacy-data")
    monkeypatch.setattr(
        paths, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    monkeypatch.setattr(paths, "LEGACY_CONFIG_PATH", tmp_path / "legacy" / "config.json")
    # The pre-rename XDG locations resolve against `$HOME`, which is the
    # developer's real one. Without this, a test asking "is there anything to
    # migrate?" would find the developer's own `~/.config/tl-kun/config.json` and
    # answer yes - and would then read, though never delete, their API key.
    monkeypatch.setattr(paths, "LEGACY_XDG_CONFIG_DIR", tmp_path / "old-config")
    monkeypatch.setattr(
        paths, "LEGACY_XDG_CONFIG_PATH", tmp_path / "old-config" / "config.json"
    )
    monkeypatch.setattr(paths, "LEGACY_XDG_DATA_DIR", tmp_path / "old-data")
    monkeypatch.setattr(paths, "LEGACY_XDG_CACHE_DIR", tmp_path / "old-cache")
    # `config` imported the constant by name, so its namespace needs the same
    # treatment: `Config.load`/`save` read that module global.
    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    return tmp_path
