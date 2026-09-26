"""Where lintranslator keeps its state, and how it writes it.

Everything used to live next to the source: `config.json`, `data/tessdata/`,
`data/cache.json`. That is fine for a git checkout and wrong for an installed
app:

* `config.json` holds an API key in plain text, and it was written with the
  process umask - 0644 on a normal desktop, so any local account could read it.
* A system-wide install has no writable directory there at all, so Settings
  could not save.
* Two users of the same install would share one config, one cache and one set
  of weights.

So state lives in the XDG directories, the config is written 0600, and there is
exactly one place each thing can be. `LINTRANSLATOR_HOME` puts all of it in one
directory instead, which is what the tests use and what a portable install
wants.
"""
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

DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    """Write `text` to `path`, readable only by its owner.

    Both halves matter. The mode is set at creation rather than afterwards,
    because a file that exists for even a moment as 0644 has already leaked its
    contents; and the mode is the one passed to `open`, not what a later
    `chmod` would fix if the process died in between.

    Written through a temporary file and renamed, so an interrupted save leaves
    the previous file intact instead of a half-written one. The rename also
    replaces an older, wider-mode file rather than writing through it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except BaseException:
        # Never leave a partial .tmp behind for a later run to trip over.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def tighten(path: Path, mode: int = 0o600) -> bool:
    """Narrow an existing file's permissions. True when it changed.

    For files written before this module existed: the fix has to reach them
    too, and a config that is only private when it is next saved is not private.
    """
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
