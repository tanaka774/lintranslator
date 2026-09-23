"""Which of lintranslator's own windows are on screen right now.

The pipeline reads pixels off the screen, so any lintranslator window over the selected
area is captured along with the game. That is not hypothetical: measured on a live
screen, the frame captured 10 ms after "Watch live" was pressed contained the
picker's own status line -

    'Watcning... captured 2560x1440 — dra without letting them in on all
     available information...'

at 93% confidence, while the dialogue line the picker's canvas covered fell to
54.7% and was dropped by `ocr.min_confidence` - a real line silently lost to our
own window.

Wayland gives a client no way to know where its windows are, so "does this window
overlap the box?" cannot be answered. What *can* be known is whether the window is
on screen at all, so that is the rule: while a window that must not be captured is
mapped, the pipeline does not capture at all. It pauses, says why, and resumes by
itself when the window goes away.

In the normal flow this costs nothing, because the picker minimises itself when
watching starts. It only bites when the user deliberately brings a window back
over the game - which is exactly when capturing it would be wrong.

The panel is deliberately *not* registered: it is the output, and it has to stay
on screen while watching. A panel over the box is caught by the self-text guard
instead (`lintranslator.selftext`).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class OcclusionGuard:
    """Tracks lintranslator windows that must not appear in a capture.

    Deliberately free of GTK: windows report their own map/unmap state, the
    pipeline only asks for a reason to wait. That keeps the pipeline testable
    without a display, and keeps GTK calls on the GTK thread.
    """

    _mapped: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_mapped(self, name: str, mapped: bool, note: str = "") -> None:
        """Record that window `name` is (or is no longer) on screen.

        `note` is shown to the user, so it must say what to do about it.
        """
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


# One process, one set of windows: the GUI runs the picker, the panel and the
# settings dialog together, and they all report here.
GUARD = OcclusionGuard()
