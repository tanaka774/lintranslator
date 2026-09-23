"""Present the real panel and report the size the compositor is actually given.

Run:  .venv-gi/bin/python probe/panel_size_live.py

`panel_layout_check.py` asks GTK what the card *would* like to be. This asks the
mapped window what it *is*, which is the number that decides how much of the
game's dialogue box the card covers - and whether it jumps when a new line
arrives. A window is shown for a few seconds; nothing is captured or translated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator.config import Config  # noqa: E402
from lintranslator.panel import TranslatorPanel  # noqa: E402
from lintranslator.pipeline import Event  # noqa: E402

CONFIG = Path("/home/chiba/workspace/lintranslator/config.json")

LONG_SOURCE = (
    "[It has been determined that this case merits preservation as a record. "
    "The following is the case record pertaining to today's request.]"
)
LONG_TARGET = (
    "[この事件は記録として保存されるべきであると決定された。"
    "今日の要請に関する事件記録は以下のとおりです。]"
)
VERBOSE_TARGET = (
    "Of all the actions taken by our men during the last operation, his alone "
    "merits compliment. It was an efficient method of neutralizing the enemy. "
    "The record will be preserved in full, and the request filed today shall be "
    "attached to it as an appendix."
)


def event(target: str, source: str = LONG_SOURCE) -> Event:
    return Event(
        source=source,
        target=target,
        confidence=94.0,
        translate_elapsed=1.69,
        total_elapsed=1.9,
        cached=False,
        backend="openrouter",
        display_source=source,
    )


def main() -> int:
    cfg = Config.load(str(CONFIG))
    cfg.translate.backend = "none"
    cfg.gui.autostart = False
    asked = cfg.display.width
    rows: list[tuple[str, int, int]] = []

    app = Gtk.Application(
        application_id="dev.lintranslator.sizeprobe", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app: Gtk.Application) -> None:
        panel = TranslatorPanel(app, cfg, None)
        panel.present()

        def measure(name: str):
            def step():
                rows.append((name, panel.get_width(), panel.get_height()))
                print(f"  {name:<28} {panel.get_width():>5} x {panel.get_height():>4}", flush=True)
                return False

            return step

        steps = [
            ("idle (as opened)", lambda: None),
            ("one short line", lambda: panel._show_event(event("はい。"))),
            ("the demo line", lambda: panel._show_event(event(LONG_TARGET))),
            ("a verbose 4-line reply", lambda: panel._show_event(event(VERBOSE_TARGET))),
            (
                "back to a short line",
                lambda: panel._show_event(event("はい。")),
            ),
            (
                "keep-above warning",
                lambda: panel.status_label.set_text(
                    "not always-on-top — on Wayland add a KWin window rule for "
                    "'lintranslator', or launch with GDK_BACKEND=x11 (see README) "
                    "[native Wayland]"
                ),
            ),
        ]
        delay = 700
        for name, action in steps:
            GLib.timeout_add(delay, action)
            GLib.timeout_add(delay + 400, measure(name))
            delay += 900

        def finish():
            print()
            print(f"asked for width  : {asked}")
            widths = {w for _, w, _ in rows}
            heights = [h for _, _, h in rows]
            print(f"widths observed  : {sorted(widths)}")
            print(f"height range     : {min(heights)} – {max(heights)} px "
                  f"(varies by {max(heights) - min(heights)} px)")
            for name, w, h in rows:
                flag = "  <-- WIDER THAN ASKED" if w > asked + 4 else ""
                print(f"  {name:<28} {w:>5} x {h:>4}{flag}")
            app.quit()
            return False

        GLib.timeout_add(delay + 600, finish)

    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
