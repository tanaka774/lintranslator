"""What lintranslator has put on disk, and how to get the space back.

The app downloads things that are easy to forget about:

* the fp32 checkpoint `lintranslator convert` reads - **2.5 GB**, and nothing
  deletes it afterwards, because the int8 weights it produces never need it
  again;
* the converted int8 weights - 629 MB, and the one artefact here that costs a
  2.5 GB download to rebuild;
* the language data tesseract reads - a rounding error next to those, but it is
  the app's only *automatic* download;
* the translation cache, if one is configured - the only file the app writes
  that contains screen text, so it is here for a different reason than the rest.

`remove` with no argument is a report: the paths and their sizes, nothing
touched. Naming a target deletes it. The deletion is deliberately narrow - only
paths under this app's own directories are ever removed, so a config pointing
`ct2_model_dir` at something else cannot turn this into an `rm -rf` of the
user's own data.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import Config
from .translate import DEFAULT_NLLB_MODEL

#: Targets that cannot be downloaded again, or not cheaply. `remove` asks before
#: deleting these even when it is otherwise happy to proceed.
EXPENSIVE = {"model"}


def format_size(size: int) -> str:
    """A size in the units a person reads, matching `check`'s MB convention."""
    if size >= 1e9:
        return f"{size / 1e9:.2f} GB"
    if size >= 1e6:
        return f"{size / 1e6:.0f} MB"
    if size >= 1e3:
        return f"{size / 1e3:.0f} KB"
    return f"{size} B"


def tree_size(path: Path) -> int:
    """Bytes under `path`, counting each real file once.

    Symlinks are not followed and not counted: a HuggingFace cache stores the
    weights once in `blobs/` and links to them from `snapshots/`, so a naive walk
    reports the checkpoint twice.
    """
    if path.is_file() and not path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            entry = Path(root) / name
            if entry.is_symlink():
                continue
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def hf_cache_root() -> Path:
    """Where `huggingface_hub` keeps downloaded models and tokenizers.

    Mirrors its own resolution order. `HF_HUB_CACHE` wins, then `$HF_HOME/hub`,
    then the default. Nothing here imports huggingface_hub: the base install does
    not have it, and `remove` has to work there too.
    """
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HF_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".cache" / "huggingface"
    return base / "hub"


def hf_repo_dir(repo_id: str) -> Path:
    """The cache directory for one repo: `facebook/x` -> `models--facebook--x`."""
    return hf_cache_root() / ("models--" + repo_id.strip("/").replace("/", "--"))


@dataclass(frozen=True)
class Target:
    """One thing that can be removed."""

    name: str
    path: Path
    title: str
    detail: str
    #: What removing it costs the user next time, or "" when it is free.
    cost: str = ""
    #: Something the user should know before deciding - a stale path, a
    #: mismatch between the config and the disk.
    note: str = ""

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def size(self) -> int:
        return tree_size(self.path) if self.exists else 0

    @property
    def expensive(self) -> bool:
        return self.name in EXPENSIVE


def checkpoint_repo(config: Config) -> str:
    """The HuggingFace repo whose cached weights are worth reclaiming.

    `translate.model` means different things per backend - a HuggingFace repo for
    the NLLB ones, an OpenRouter id like `google/gemini-2.5-flash-lite` for the
    chat ones - so reading it unconditionally produced a target named after a
    model that is never downloaded. Only the local backends have a checkpoint
    here at all; for anything else it is the default local model `convert`
    would fetch.
    """
    if config.translate.backend in ("ct2", "local"):
        return (config.translate.model or "").strip() or DEFAULT_NLLB_MODEL
    return DEFAULT_NLLB_MODEL


def targets(config: Config) -> list[Target]:
    """Everything the app has on disk, biggest first."""
    model_dir = Path(config.translate.ct2_model_dir)
    repo = checkpoint_repo(config)
    local = config.translate.backend in ("ct2", "local")

    model_note = ""
    default_dir = paths.default_ct2_dir()
    if not model_dir.exists() and model_dir != default_dir and default_dir.exists():
        # Seen for real: a config written before the app was renamed still names
        # the old directory, so 629 MB sit unread under a path nothing looks at.
        model_note = (
            f"configured dir does not exist; {format_size(tree_size(default_dir))} "
            f"of weights are at {default_dir}"
        )

    found = [
        Target(
            name="checkpoint",
            path=hf_repo_dir(repo),
            title=f"fp32 checkpoint for {repo}",
            detail=(
                "what `lintranslator convert` reads. The converted weights do "
                "not need it again, so this is the one to remove."
                + ("" if local else f" ({config.translate.backend} does not use it)")
            ),
            cost="a 2.5 GB download, only if you convert again",
        ),
        Target(
            name="model",
            path=model_dir,
            title="converted int8 weights",
            detail="what the ct2 backend loads at every start.",
            cost="re-running `lintranslator convert`, which downloads 2.5 GB again",
            note=model_note,
        ),
        Target(
            name="tessdata",
            path=paths.DATA_DIR / "tessdata",
            title="tesseract language data",
            detail="downloaded on the first OCR run, checksum-verified.",
            cost="a 4-6 MB download on the next OCR run",
        ),
    ]
    if config.cache_path:
        found.append(
            Target(
                name="cache",
                path=Path(config.cache_path),
                title="translation cache",
                detail=(
                    "the only file the app writes that holds screen text - a "
                    "plaintext copy of everything it has OCR'd."
                ),
            )
        )
    return sorted(found, key=lambda t: t.size, reverse=True)


def others_in_hf_cache(keep: Target) -> list[tuple[Path, int]]:
    """Other `models--*` directories in the same cache, for the report.

    Reported, never removed: they belong to whatever else the user runs, and
    this command has no business guessing.
    """
    root = hf_cache_root()
    if not root.is_dir():
        return []
    found = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.startswith("models--") and entry != keep.path:
            found.append((entry, tree_size(entry)))
    return found


def _allowed_roots() -> list[Path]:
    """Directories this command may delete inside."""
    return [
        paths.DATA_DIR,
        paths.CACHE_DIR,
        paths.CONFIG_DIR,
        hf_cache_root(),
        Path.home() / ".cache" / "huggingface",
    ]


def refusal(path: Path) -> str | None:
    """Why `path` must not be removed, or None when it is safe.

    The config can point `ct2_model_dir` or `cache_path` anywhere. Removing a
    path the user chose themselves is their business, not this command's, so it
    only ever touches what the app's own directories contain.
    """
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return f"cannot resolve {path}"
    for root in _allowed_roots():
        try:
            resolved.relative_to(root.expanduser().resolve())
            return None
        except (OSError, ValueError):
            continue
    return (
        f"{resolved} is not inside a directory this app owns "
        "(config, data or cache) - remove it yourself if you meant to"
    )


def remove(target: Target) -> int:
    """Delete one target, returning the bytes freed."""
    freed = target.size
    if target.path.is_dir():
        shutil.rmtree(target.path)
    else:
        target.path.unlink(missing_ok=True)
    return freed
