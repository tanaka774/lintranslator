"""Shared test setup.

The app's state lives in the XDG directories (see `lintranslator.paths`), so an
unqualified `Config().save()` in a test would write the developer's real
`~/.config/lintranslator/config.json` - possibly wiping their API key, which is
exactly the accident the old repo-relative default caused once. Every test
therefore gets redirected dirs whether it asks for them or not.

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

    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        paths, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    # `config` imported the constant by name, so its namespace needs the same
    # treatment: `Config.load`/`save` read that module global.
    monkeypatch.setattr(
        config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "config" / "config.json"
    )
    # The HuggingFace cache is not the app's, but `lintranslator remove` deletes
    # inside it - and it resolves from `$HF_HOME`/`$HF_HUB_CACHE`, which point at
    # the developer's real cache. Without this, a test of `remove all` deletes
    # their downloaded checkpoint: it only survived the first run because the
    # sandbox refused the write. `hf_cache_root` reads the environment per call,
    # so setting the variables is enough.
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf-cache"))
    monkeypatch.delenv("HF_HOME", raising=False)
    return tmp_path
