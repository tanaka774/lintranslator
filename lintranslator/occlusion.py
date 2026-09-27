"""Which of lintranslator's own windows are on screen right now."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class OcclusionGuard:
    """Tracks lintranslator windows that must not appear in a capture."""

    _mapped: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_mapped(self, name: str, mapped: bool, note: str = "") -> None:
        """Record that window `name` is (or is no longer) on screen; `note` is shown to the user."""
        with self._lock:
            if mapped:
                self._mapped[name] = note or f"{name} is on screen"
            else:
                self._mapped.pop(name, None)

    def clear(self, name: str) -> None:
        self.set_mapped(name, False)

    @property
    def blocked(self) -> bool:
        return self.reason() is not None

    def reason(self) -> str | None:
        """Why capturing must wait, or None when it is safe to capture."""
        with self._lock:
            for note in self._mapped.values():
                return note
        return None

    @property
    def windows(self) -> list[str]:
        with self._lock:
            return list(self._mapped)


GUARD = OcclusionGuard()
