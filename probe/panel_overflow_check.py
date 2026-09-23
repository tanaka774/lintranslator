"""Watch the control row overflow as the card narrows.

Run:  .venv/bin/python probe/panel_overflow_check.py

The action buttons live on the card while they fit and move into the ⋮ menu when
they do not, so the split is a function of width and cannot be read off the
source. This presents the real panel, narrows it in steps, and for each width
records which buttons are on the card and renders the card to
`data/overflow_<width>.png`.

The split must only ever move one way as the card narrows: each narrower width
keeps a *prefix* of the action order, so Region - first on the card - is the last
to leave it.

A real window is resized for a few seconds. Nothing is captured or translated.
"""
from __future__ import annotations

import sys
import time
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

SCRATCH = Path("/tmp/lintranslator_overflow_probe_config.json")
OUT = APP_DIR / "data"
WIDTHS = [980, 700, 560, 460, 380, 300]
failures: list[str] = []


def pump(milliseconds: int) -> None:
    """Run the main loop so the compositor and the relayout idle can act."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def check(condition: bool, message: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        failures.append(message)


def shoot(window, name: str) -> None:
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        print(f"  {name}: no render node yet")
        return
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    # The card is translucent over a game; a mid grey stands in for one.
    context.set_source_rgb(0.32, 0.34, 0.38)
    context.paint()
    node.draw(context)
    surface.write_to_png(str(OUT / f"{name}.png"))


def main() -> int:
    cfg = Config.load()
    cfg.path = SCRATCH  # this probe resizes, and a resize saves the config
    cfg.translate.backend = "none"
    cfg.display.height = 0

    app = Gtk.Application(
        application_id="dev.lintranslator.overflow", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app: Gtk.Application) -> None:
        theme.install_for(cfg)
        panel = TranslatorPanel(app, cfg, None)
        panel.enable_region_button(lambda: None)  # a picker owns the panel in use
        panel.present()

        def run():
          try:
            order = list(panel.ACTION_ORDER)
            print(f"  priority order (first on the card): {', '.join(order)}")
            print()
            on_card_at: dict[int, list[str]] = {}
            for width in WIDTHS:
                panel.set_default_size(width, 260)
                pump(450)
                actual = panel.get_width()
                on_card = [
                    name
                    for name in order
                    if getattr(panel, name).get_parent() is panel.button_row
                ]
                in_menu = [
                    name
                    for name in order
                    if getattr(panel, name).get_parent() is panel.menu_box
                ]
                on_card_at[width] = on_card
                shoot(panel, f"overflow_{width}")
                print(
                    f"  window {actual:>4}px -> card: "
                    f"{', '.join(n.replace('_btn','') for n in on_card) or '(none)'}"
                    f"   menu: {', '.join(n.replace('_btn','') for n in in_menu) or '(empty)'}"
                )

            print()
            for width in WIDTHS:
                on_card = on_card_at[width]
                check(
                    on_card == order[: len(on_card)],
                    f"at {width}px the card holds a prefix of the order ({len(on_card)} buttons)",
                )
            counts = [len(on_card_at[w]) for w in WIDTHS]
            check(
                all(a >= b for a, b in zip(counts, counts[1:])),
                f"narrowing never adds a button back: {counts}",
            )
            check(
                "region_btn" in on_card_at[WIDTHS[-1]],
                "Region survives the narrowest card",
            )
            check(counts[0] == len(order), f"the widest card shows every action ({counts[0]})")
            app.quit()
            return False
          except Exception as exc:  # noqa: BLE001 - a probe must not pass quietly
            import traceback

            traceback.print_exc()
            failures.append(f"probe raised {type(exc).__name__}: {exc}")
            app.quit()
            return False

        GLib.timeout_add(1400, run)

    app.connect("activate", on_activate)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    app.run([])

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK — the row overflows from the right, keeping Region longest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
