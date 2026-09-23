"""Does the panel pair a new translation with the *previous* line's original?

The panel prints `event.display_source or event.source` (panel.py `_show_event`),
and the pipeline fills `display_source` from `self._last_display` - the display
text of the line emitted *before* - on the release path that actually fires in
practice, `emit:tick-timeout`, which never reads the screen itself.

This drives the real Pipeline with scripted multi-line OCR reads (so
`display_text`, which keeps the game's line breaks, differs from `text`, which is
joined for translation), a typewriter reveal and blinking-caret jitter, plus an
optional Re-read - then reports what the panel's source label would say beside
each new translation.

Run:  .venv/bin/python probe/stale_source_check.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from lintranslator.capture import Frame  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.ocr import OcrLine, OcrResult  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402
from lintranslator.translate import Translation  # noqa: E402

# Limbus-shaped lines, each wrapped by the game across two rows. `rows` is what
# the OCR recovers: `display_text` keeps the break, `text` does not.
LINES = [
    ("It has been determined that this case merits preservation as a record.",
     "The following is the case record pertaining to today's request."),
    ("Manager, the abnormality is approaching.",
     "Prepare for combat."),
    ("Don't worry. I have already calculated",
     "the optimal solution."),
    ("The price of silence is paid in blood.",
     "Remember that."),
    ("We are the ones who will decide",
     "what happens next."),
]


def joined(rows: tuple[str, str]) -> str:
    return " ".join(rows)


def displayed(rows: tuple[str, str]) -> str:
    return "\n".join(rows)


def own_display(source: str) -> str:
    """The display text of the line a translation was made from."""
    return next((displayed(rows) for rows in LINES if joined(rows) == source), "?")


@dataclass
class Script:
    """One entry per poll: which line is on screen, and how much has been typed."""

    line: int
    chars: int
    blink: bool = False


def build_script() -> list[Script]:
    script: list[Script] = []
    for index, rows in enumerate(LINES):
        text = joined(rows)
        for step in range(24, len(text) + 1, 24):  # typewriter, a chunk at a time
            script.append(Script(index, step, blink=True))
        script.extend(Script(index, len(text), blink=True) for _ in range(10))
    return script


class ScriptedScreen:
    def __init__(self, script: list[Script]) -> None:
        self.script = script
        self.poll = 0
        self.grabs = 0

    def grab(self) -> Frame:
        self.grabs += 1
        img = Image.new("RGB", (400, 80), (30, 30, 34))
        if self.script[min(self.poll, len(self.script) - 1)].blink and self.grabs % 2:
            ImageDraw.Draw(img).rectangle([390, 20, 396, 40], fill=(255, 255, 255))
        return Frame(image=img, full_size=img.size, region=(0, 0, 400, 80), elapsed=0.001)

    def close(self) -> None:
        pass

    @property
    def stats(self) -> dict:
        return {"grabs": self.grabs}


class ScriptedOcr:
    """Returns the first `chars` of the current line, wrapped into the game's rows."""

    def __init__(self, screen: ScriptedScreen) -> None:
        self.screen = screen

    def ensure_ready(self) -> None:
        return None

    def read(self, image) -> OcrResult:
        item = self.screen.script[min(self.screen.poll, len(self.screen.script) - 1)]
        rows = LINES[item.line]
        visible = joined(rows)[: item.chars]
        head = rows[0][: len(visible)]
        tail = visible[len(head):].strip()
        lines = [
            OcrLine(text=row, confidence=92.0, box=(0, 10 * n, 380, 12))
            for n, row in enumerate([head] + ([tail] if tail else []))
            if row
        ]
        return OcrResult(lines=lines, elapsed=0.001, engine="scripted", lang="eng")


class StubTranslator:
    name = "stub"

    def __init__(self) -> None:
        self.cache = type("C", (), {"stats": {}})()
        self.last_was_cached = False

    def translate(self, text: str, force: bool = False) -> Translation:
        return Translation(target=f"[ja] {text}", source=text, backend="stub", elapsed=0.001)

    def warmup(self) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass
class Shown:
    at: float
    decision: str
    source: str
    display_source: str
    last_display_after: str

    @property
    def panel_source(self) -> str:
        """What `TranslatorPanel._show_event` puts in the source label."""
        return self.display_source or self.source

    @property
    def verdict(self) -> str:
        want = own_display(self.source)
        if self.panel_source == want:
            return "ok"
        if self.panel_source.replace("\n", " ") == want.replace("\n", " "):
            return "same line, breaks dropped"
        return "WRONG LINE on screen"


def run(re_read_at: int | None, settle_max_wait: float | None = None) -> list[Shown]:
    cfg = Config()
    if settle_max_wait is not None:
        cfg.detect.settle_max_wait = settle_max_wait  # the live config.json value
    cfg.translate.backend = "none"

    script = build_script()
    screen = ScriptedScreen(script)
    shown: list[Shown] = []
    now = 0.0

    def on_event(event) -> None:
        shown.append(
            Shown(
                at=now,
                decision=pipe.last_decision,
                source=event.source,
                display_source=event.display_source,
                last_display_after=pipe._last_display or "",
            )
        )

    pipe = Pipeline(cfg, on_event=on_event)
    pipe.grabber = screen
    pipe.ocr = ScriptedOcr(screen)
    pipe.translator = StubTranslator()
    pipe.warmup = lambda: None
    pipe.start(0.0)

    for poll in range(len(script)):
        now = round(now + 0.5, 3)
        screen.poll = poll
        if re_read_at is not None and poll == re_read_at:
            pipe.request_reread()
        pipe.step(now)
    return shown


def report(title: str, shown: list[Shown]) -> int:
    print(f"=== {title} ===")
    wrong = 0
    for event in shown:
        wrong += 1 if event.verdict == "WRONG LINE on screen" else 0
        print(f"\nt={event.at:<5} released by {event.decision}")
        print(f"  translation of  : {event.source!r}")
        print(f"  panel shows as  : {event.panel_source!r}   [{event.verdict}]")
        if event.decision == "ocr:reread":
            print(f"  (a Re-read stores this line's display text in _last_display={event.last_display_after!r})")
    lines = [e for e in shown if e.verdict == "WRONG LINE on screen"]
    print(
        f"\n{len(shown)} translations shown; {len(lines)} of them paired with another "
        f"line's original text\n"
    )
    return wrong


def main() -> int:
    half = len(build_script()) // 2
    wrong = 0
    wrong += report("session with no Re-read", run(re_read_at=None))
    wrong += report(
        "the same session, one Re-read pressed about halfway",
        run(re_read_at=half),
    )
    wrong += report(
        "no Re-read, live config.json settings (settle_max_wait=2.0)",
        run(re_read_at=None, settle_max_wait=2.0),
    )
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
