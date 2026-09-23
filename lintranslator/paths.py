"""Where lintranslator keeps its state, and how it writes it.

Everything used to live next to the source: `config.json`, `data/ct2/`,
`data/tessdata/`, `data/cache.json`. That is fine for a git checkout and wrong
for an installed app:

* `config.json` holds an API key in plain text, and it was written with the
  process umask - 0644 on a normal desktop, so any local account could read it.
* A system-wide install has no writable directory there at all, so Settings
  could not save.
* Two users of the same install would share one config, one cache and one set
  of weights.

So state lives in the XDG directories now, the config is written 0600, and the
old in-tree locations are still *read* so an existing checkout keeps working.
`LINTRANSLATOR_HOME` puts all of it in one directory instead, which is what the tests
use and what a portable install wants.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


#: The app was called tl-kun before it was called lintranslator. The old name is
#: still honoured everywhere the state could be - see `_legacy_xdg_root`.
LEGACY_APP_NAME = "tl-kun"


def _override() -> Path | None:
    """`$LINTRANSLATOR_HOME`, or the `$TLKUN_HOME` an older session exported."""
    for name in ("LINTRANSLATOR_HOME", "TLKUN_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return None


def _xdg(env_var: str, fallback: str, name: str = "lintranslator") -> Path:
    """`$XDG_<X>_HOME/lintranslator`, or the spec's fallback under `$HOME`."""
    base = os.environ.get(env_var)
    root = Path(base) if base else Path.home() / fallback
    return root / name


def _legacy_xdg_root(env_var: str, fallback: str) -> Path:
    """The same directory under the app's old name."""
    return _xdg(env_var, fallback, LEGACY_APP_NAME)


_OVERRIDE = _override()

#: Config, weights and cache. Overridden wholesale by `LINTRANSLATOR_HOME`.
#: These are the *write* locations and always carry the new name, so the rename
#: produces exactly one copy of a 600 MB model rather than two.
CONFIG_DIR = _OVERRIDE or _xdg("XDG_CONFIG_HOME", ".config")
DATA_DIR = _OVERRIDE or _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _OVERRIDE or _xdg("XDG_CACHE_HOME", ".cache")

#: Where an older install kept everything. Read, never written.
LEGACY_DATA_DIR = APP_DIR / "data"
LEGACY_CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"

#: The same files under the pre-rename name, for setups that predate it. Also
#: read, never written: a config found here is copied to `CONFIG_DIR` by
#: `migrate_legacy_config`, and the big weights are used where they are.
LEGACY_XDG_CONFIG_DIR = _legacy_xdg_root("XDG_CONFIG_HOME", ".config")
LEGACY_XDG_CONFIG_PATH = LEGACY_XDG_CONFIG_DIR / "config.json"
LEGACY_XDG_DATA_DIR = _legacy_xdg_root("XDG_DATA_HOME", ".local/share")
LEGACY_XDG_CACHE_DIR = _legacy_xdg_root("XDG_CACHE_HOME", ".cache")

CT2_DIR_NAME = Path("ct2") / "nllb-600m-int8"


def default_ct2_dir() -> Path:
    """Where the converted weights live.

    Normally the user data dir, with two exceptions: weights left inside the
    source tree by an older install, or weights left in the pre-rename
    `~/.local/share/tl-kun` by an older release. Both are used where they are
    while nothing exists in the new place. Silently converting 600 MB again - or
    worse, failing to - just because a path or a name moved is a poor welcome for
    an existing user.

    Deliberately a *read* fallback rather than a copy: moving 600 MB to rename a
    directory is a lot of I/O for no benefit, and a failed move could lose it.
    """
    current = DATA_DIR / CT2_DIR_NAME
    if current.exists():
        return current
    for legacy in (LEGACY_DATA_DIR / CT2_DIR_NAME, LEGACY_XDG_DATA_DIR / CT2_DIR_NAME):
        if legacy.exists():
            return legacy
    return current


def config_search_path() -> Path:
    """The config this process reads when `--config` is not given.

    The XDG location unless it does not exist yet and an older file does - either
    beside the source (the original layout) or under the pre-rename XDG directory
    - in which case the old file is read where it is. `migrate_legacy_config`
    then copies it into place; this is only the read fallback.
    """
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    for legacy in (LEGACY_XDG_CONFIG_PATH, LEGACY_CONFIG_PATH):
        if legacy.exists():
            return legacy
    return DEFAULT_CONFIG_PATH


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


# --------------------------------------------------------------------------- #
# One-time move out of the source tree
# --------------------------------------------------------------------------- #
@dataclass
class Migration:
    """What `migrate_legacy_config` did, for the caller to report."""

    new_path: Path
    old_path: Path
    remapped: list[str] = field(default_factory=list)
    legacy_files: list[Path] = field(default_factory=list)
    #: Set when the copy could not be written - a read-only or full `$HOME`, a
    #: sandbox, a permission problem. The old file is still readable, so this is
    #: a downgrade to "keep using it where it is", never a failure to start.
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


def _remap(value: str) -> str | None:
    """An in-tree state path, pointed at its new home.

    Only paths that are actually inside the old app directory are touched:
    anything the user pointed somewhere else of their own accord is left alone,
    because guessing at those is how a migration eats someone's data.
    """
    if not value:
        return None
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError):
        return None
    try:
        relative = path.resolve().relative_to(APP_DIR.resolve())
    except (OSError, ValueError):
        return None
    if not relative.parts or relative.parts[0] != "data":
        return None
    rest = Path(*relative.parts[1:])
    if not rest.parts:
        return None
    if rest.parts[0] == "cache.json":
        return str(CACHE_DIR / "cache.json")
    if rest.parts[0] == "tessdata":
        return str(DATA_DIR / rest)
    if rest.parts[0] == "ct2":
        return str(DATA_DIR / CT2_DIR_NAME)
    return str(DATA_DIR / rest)


def migrate_legacy_config() -> Migration | None:
    """Copy a config left by an older release into the current config dir.

    Two older layouts are handled: a config beside the source (the original
    layout) and one under the pre-rename XDG directory, `~/.config/tl-kun`. The
    XDG one wins when both exist, because that is the layout the previous release
    actually wrote.

    Only when the new location has none, and never by deleting the old file: if
    anything here is wrong, the user still has their settings. The copy's state
    paths are repointed, because a config that keeps writing its cache and its
    weights into a git checkout has not really moved.
    """
    if DEFAULT_CONFIG_PATH.exists():
        return None
    source = next(
        (p for p in (LEGACY_XDG_CONFIG_PATH, LEGACY_CONFIG_PATH) if p.exists()),
        None,
    )
    if source is None:
        return None
    try:
        raw = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    remapped: list[str] = []
    for section, key in (("translate", "ct2_model_dir"), (None, "cache_path")):
        holder = raw if section is None else raw.get(section)
        if not isinstance(holder, dict):
            continue
        new_value = _remap(holder.get(key) or "")
        if new_value and new_value != holder.get(key):
            holder[key] = new_value
            remapped.append(f"{section + '.' if section else ''}{key}")

    # The copy can legitimately fail - a read-only `$HOME`, a full disk, a
    # sandbox that only permits writes in the workspace. That must not stop the
    # app from starting when the old file is sitting right there and readable, so
    # the failure is recorded and the caller keeps using the source.
    error = ""
    try:
        write_private(DEFAULT_CONFIG_PATH, json.dumps(raw, indent=2) + "\n")
    except OSError as exc:
        error = str(exc)

    # The parts of the old state that are just files, so the message can say
    # what is still sitting there.
    legacy_files = [
        path
        for path in (
            source,
            LEGACY_DATA_DIR / "cache.json",
            LEGACY_DATA_DIR / "ct2" / "nllb-600m-int8",
            LEGACY_DATA_DIR / "tessdata",
            LEGACY_XDG_DATA_DIR / "ct2" / "nllb-600m-int8",
            LEGACY_XDG_CACHE_DIR / "cache.json",
        )
        if path.exists()
    ]
    return Migration(
        new_path=DEFAULT_CONFIG_PATH,
        old_path=source,
        remapped=remapped,
        legacy_files=legacy_files,
        error=error,
    )


def migration_warning(migration: Migration) -> str:
    """What to tell the user, in one line small enough for a status row."""
    if not migration.succeeded:
        # The settings were kept - they are just still where they were - so this
        # reads as a downgrade, not an error, and must not say "moved".
        return (
            f"could not copy your config to {migration.new_path} "
            f"({migration.error}); still using {migration.old_path}"
        )
    parts = [f"moved your config to {migration.new_path}"]
    if migration.remapped:
        parts.append("repointed " + ", ".join(migration.remapped))
    leftovers = [p for p in migration.legacy_files if p != migration.old_path]
    where = "the source tree" if _in_app_dir(migration.old_path) else "the old location"
    if leftovers:
        parts.append(
            f"{migration.old_path} and {len(leftovers)} other file(s) are still in "
            f"{where} — delete them, one holds your API key"
        )
    else:
        parts.append(
            f"delete {migration.old_path} in {where}, it still holds your API key"
        )
    return "; ".join(parts)


def _in_app_dir(path: Path) -> bool:
    """Whether `path` sits inside the checkout this process runs from."""
    try:
        Path(path).resolve().relative_to(APP_DIR.resolve())
    except (OSError, ValueError):
        return False
    return True
