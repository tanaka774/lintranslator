"""End-to-end: what the real panel's labels say after each new translation."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from stale_source_check import (  # noqa: E402
    LINES,
    ScriptedOcr,
    ScriptedScreen,
    StubTranslator,
    build_script,
    joined,
    own_display,
)

from lintranslator.config import Config  # noqa: E402
from lintranslator.panel import TranslatorPanel  # noqa: E402
from lintranslator.pipeline import Pipeline  # noqa: E402

rows: list[tuple[float, str, str, str, str, str]] = []
failures: list[str] = []


def main() -> int:
    cfg = Config.load()
    cfg.translate.backend = "none"
    cfg.cache_path = ""  # never touch the real translation cache

    script = build_script()
    screen = ScriptedScreen(script)
    now = 0.0

    app = Gtk.Application(
        application_id="dev.lintranslator.probe.stalesource", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app) -> None:
        panel = TranslatorPanel(app, cfg, position=None)
        pipe = Pipeline(cfg)
        pipe.grabber = screen
        pipe.ocr = ScriptedOcr(screen)
        pipe.translator = StubTranslator()
        pipe.warmup = lambda: None
        pipe.start(0.0)
        re_read_at = len(script) // 2

        def poll() -> bool:
            nonlocal now
            index = poll.poll
            poll.poll += 1
            if index >= len(script):
                finish(panel)
                return False
            now = round(now + 0.5, 3)
            screen.poll = index
            if index == re_read_at:
                pipe.request_reread()
            event = pipe.step(now)
            if event is not None:
                panel._show_event(event)  # what _drain does for an "event"
                rows.append(
                    (
                        now,
                        pipe.last_decision,
                        panel.source_label.get_text(),
                        panel.target_label.get_text(),
                        event.source,
                        event.display_source,
                    )
                )
            return True

        poll.poll = 0
        GLib.timeout_add(1, poll)

    def finish(panel: TranslatorPanel) -> None:
        print("What the card showed, in order (source label / target label):\n")
        for at, decision, source_label, target_label, source, display_source in rows:
            want = own_display(source)
            if source_label == want:
                verdict = "ok"
            elif source_label.replace("\n", " ") == want.replace("\n", " "):
                verdict = "same line, game's line breaks dropped"
            else:
                verdict = "WRONG ORIGINAL - this English belongs to another translation"
                failures.append(f"t={at}: {source_label!r} shown for {source!r}")
            print(f"  t={at:<5} released by {decision}")
            print(f"    original label : {source_label!r}")
            print(f"    translation    : {target_label!r}")
            print(f"    -> {verdict}\n")
        print(f"display_source the pipeline sent for the last line: {display_source!r}")
        print(f"the English that line actually is                : {want!r}")
        panel.close_pipeline()
        app.quit()

    app.connect("activate", on_activate)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    app.run([])

    if failures:
        print(f"\nFAIL - {len(failures)} translation(s) shown with another line's original text")
        return 1
    print("\nOK - every translation was shown with its own original text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
