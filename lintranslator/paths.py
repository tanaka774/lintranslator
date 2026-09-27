"""Where lintranslator keeps its state, and how it writes it."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def _override() -> Path | None:
    """`$LINTRANSLATOR_HOME`, which puts every directory below in one place."""
    value = os.environ.get("LINTRANSLATOR_HOME")
    return Path(value).expanduser() if value else None


def _xdg(env_var: str, fallback: str) -> Path:
    """`$XDG_<X>_HOME/lintranslator`, or the spec's fallback under `$HOME`."""
    base = os.environ.get(env_var)
    root = Path(base) if base else Path.home() / fallback
    return root / "lintranslator"


_OVERRIDE = _override()

#: Config, weights and cache. Overridden wholesale by `LINTRANSLATOR_HOME`.
CONFIG_DIR = _OVERRIDE or _xdg("XDG_CONFIG_HOME", ".config")
DATA_DIR = _OVERRIDE or _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _OVERRIDE or _xdg("XDG_CACHE_HOME", ".cache")

# config.json holds an API key in plain text, so it is written 0600
# (`write_private`) and tightened on sight (`tighten`).
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    """Write `text` to `path`, readable only by its owner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # the mode is set at creation, not afterwards: a file that exists for even a
    # moment as 0644 has already leaked its contents
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except BaseException:
        # never leave a partial .tmp behind for a later run to trip over
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    # rename, so an interrupted save leaves the previous file intact and an older,
    # wider-mode file is replaced rather than written through
    os.replace(tmp, path)


def tighten(path: Path, mode: int = 0o600) -> bool:
    """Narrow an existing file's permissions. True when it changed."""
    try:
        current = path.stat().st_mode & 0o777
    except OSError:
        return False
    if current & ~mode == 0:
        return False
    try:
        path.chmod(mode)
    except OSError:
        return False
    return True
