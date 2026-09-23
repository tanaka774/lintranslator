"""Render the panel in every state it can be in, as PNGs, to look at them.

Run:  .venv/bin/python probe/panel_contact_sheet.py

The panel is the one window that floats over a game, so its size and its button
placement cannot be judged from the source. This presents the real window and
renders it, once per state, into `data/panel_*.png`. Nothing is captured or
translated: the states are injected directly.

Order matters. The last two states are the ones that used to break: a long
translation (which ratcheted the card taller and never gave the height back) and
a return to a short one (which used to stay tall).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Run from anywhere: the package is imported from this checkout, not from
# whatever happens to be on `sys.path`.
APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator import theme  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.panel import TranslatorPanel  # noqa: E402
from lintranslator.pipeline import Event  # noqa: E402

OUT = APP_DIR / "data"

TWO_LINE_SOURCE = (
    "[The committee has resolved that this entry warrants retention as a standing record. "
    "The material below is the file concerning today's submission.]"
)
TWO_LINE_TARGET = (
    "[この事件は記録として保存されるべきであると決定された。"
    "今日の要請に関する事件記録は以下のとおりです。]"
)
VERBOSE_TARGET = (
    "Of all the work done by our team during the last deployment, hers alone "
    "deserves praise. It was an efficient way of defusing the situation. The "
    "file will be kept in full, and the request submitted today shall be "
    "attached to it as an appendix."
)
SHORT_SOURCE = "Yes, sir."
SHORT_TARGET = "はい、わかりました。"

sizes: list[tuple[str, int, int]] = []


def event(target: str, source: str) -> Event:
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


def shoot(window, name: str) -> None:
    width, height = window.get_width(), window.get_height()
    if Gtk.WidgetPaintable.new(window) is None:  # pragma: no cover - probe
        print(f"  {name}: no paintable", flush=True)
        return
    sizes.append((name, width, height))
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        print(f"  {name}: not paintable")
        return
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    # The card is translucent over a game; paint a mid grey behind it so the
    # screenshot shows what it will actually look like.
    context.set_source_rgb(0.32, 0.34, 0.38)
    context.paint()
    node.draw(context)
    surface.write_to_png(str(OUT / f"panel_{name}.png"))
    print(f"  wrote panel_{name}.png  ({width}x{height})", flush=True)


def main() -> int:
    cfg = Config.load()
    cfg.translate.backend = "none"
    cfg.gui.autostart = False
    # The real config's font scale, so the render matches the user's screen.
    print(
        f"card width {cfg.display.width}  target_lines={cfg.display.target_lines} "
        f"source_lines={cfg.display.source_lines} "
        f"font={cfg.display.base_font_size}x{cfg.display.font_scale}"
    )

    app = Gtk.Application(
        application_id="dev.lintranslator.contactsheet", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    states: list[tuple[str, object]] = []

    def on_activate(_app: Gtk.Application) -> None:
        panel = TranslatorPanel(app, cfg, None)
        panel.present()

        def shoot_state(name: str):
            def step():
                target = panel
                if name == "menu":
                    shoot(panel.menu, "09_menu")
                    return False
                shoot(target, f"{name}")
                return False

            return step

        def state_idle():
            # What the card looks like on this machine at startup: the keep-above
            # notice, since native Wayland cannot raise a window.
            panel.queue_draw()

        def state_short():
            panel._show_event(event(SHORT_TARGET, SHORT_SOURCE))

        def state_two_line():
            panel._show_event(event(TWO_LINE_TARGET, TWO_LINE_SOURCE))

        def state_verbose():
            panel._show_event(event(VERBOSE_TARGET, TWO_LINE_SOURCE))

        def state_back_to_short():
            panel._show_event(event(SHORT_TARGET, SHORT_SOURCE))

        def state_gated():
            panel._show_gate("the settings window is on screen")

        def state_error():
            panel._show_error("HTTPError: 429 Too Many Requests (retry in 12 s)")

        def state_no_source():
            cfg.display.show_source = False
            panel.apply_display_settings()
            panel._show_error("")
            panel._show_event(event(TWO_LINE_TARGET, TWO_LINE_SOURCE))

        def state_overflow():
            # The popover is its own surface, so it is rendered on its own rather
            # than as part of the card. Region is revealed first, since a picker
            # owns the panel in normal use.
            panel.enable_region_button(lambda: None)
            panel._on_menu(panel.menu_btn)

        # Each state is set in one timer slot and shot in the next. Snapshotting
        # in the same slot returns no render node: changing a label invalidates
        # the widget's cached node, and the compositor has not drawn the new one
        # yet. Measured, the node is back within ~500 ms of the change.
        sequence = [
            ("idle", state_idle),
            ("short", state_short),
            ("two_lines", state_two_line),
            ("verbose", state_verbose),
            ("back_to_short", state_back_to_short),
            ("paused", state_gated),
            ("error", state_error),
            ("no_source", state_no_source),
            ("menu", state_overflow),
        ]
        delay = 1200
        for name, step in sequence:
            GLib.timeout_add(delay, step)
            GLib.timeout_add(delay + 500, shoot_state(name))
            delay += 900

        def close_menu():
            panel.menu.popdown()
            return False

        GLib.timeout_add(delay, close_menu)

        def finish():
            print()
            print(
                f"reserved: "
                f"target={panel._reserved_height(panel.target_label, cfg.display.target_lines)}px "
                f"source={panel._reserved_height(panel.source_label, cfg.display.source_lines)}px"
            )
            by_name = {name: (w, h) for name, w, h in sizes}
            # Only the states that differ *by content* are evidence about the
            # card's size. `no_source` is a deliberate configuration change and
            # `menu` is a different surface altogether, so folding them into this
            # check would make it cry wolf on every run.
            content = [
                "idle", "short", "two_lines", "verbose", "back_to_short", "paused", "error",
            ]
            heights = {by_name[n][1] for n in content if n in by_name}
            widths = {by_name[n][0] for n in content if n in by_name}
            for name, (w, h) in by_name.items():
                print(f"  {name:<14} {w:>5} x {h:>4}{'' if name in content else '   (not a content state)'}")
            print()
            failed = False
            if len(heights) != 1:
                print(f"FAIL: card height follows the content: {sorted(heights)}")
                failed = True
            if widths and len(widths) != 1:
                print(f"FAIL: card width follows the content: {sorted(widths)}")
                failed = True
            if not failed:
                print(f"OK — every content state is {widths.pop()} x {heights.pop()}")
            app.quit()
            return failed

        GLib.timeout_add(delay + 400, finish)

    app.connect("activate", on_activate)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    code = app.run([])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
