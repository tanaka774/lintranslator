"""What lintranslator has put on disk, and how to get the space back."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import Config


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
    """Bytes under `path`, counting each real file once."""
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


@dataclass(frozen=True)
class Target:
    """One thing that can be removed."""

    name: str
    path: Path
    title: str
    detail: str
    #: What removing it costs the user next time, or "" when it is free.
    cost: str = ""
    #: Something the user should know before deciding, such as a config/disk
    #: mismatch.
    note: str = ""

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def size(self) -> int:
        return tree_size(self.path) if self.exists else 0


def targets(config: Config) -> list[Target]:
    """Everything the app has on disk, biggest first."""
    found = [
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


def _allowed_roots() -> list[Path]:
    """Directories this command may delete inside."""
    return [
        paths.DATA_DIR,
        paths.CACHE_DIR,
        paths.CONFIG_DIR,
    ]


def refusal(path: Path) -> str | None:
    """Why `path` must not be removed, or None when it is safe."""
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
