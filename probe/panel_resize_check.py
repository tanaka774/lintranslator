"""Check the panel can be resized by dragging its edge."""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from lintranslator import theme  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.panel import TranslatorPanel  # noqa: E402

# Never the real config: this probe *saves* the size it drags to.
SCRATCH = Path("/tmp/lintranslator_resize_probe_config.json")
failures: list[str] = []


def pump(milliseconds: int) -> None:
    """Run the main loop so the compositor can answer a resize, which is not synchronous."""
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


def main() -> int:
    cfg = Config.load()
    cfg.path = SCRATCH
    cfg.translate.backend = "none"
    cfg.display.width = 555
    cfg.display.height = 0  # start from the budget, not from a pinned height

    app = Gtk.Application(
        application_id="dev.lintranslator.resizecheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(_app: Gtk.Application) -> None:
        theme.install_for(cfg)
        panel = TranslatorPanel(app, cfg, None)
        panel.present()

        def probe():
          try:
            return _probe(panel)
          except Exception as exc:  # noqa: BLE001 - a probe must not fail quietly
            # Without this an exception inside a GLib callback is swallowed, the
            # loop ends, and the probe prints OK having checked nothing.
            import traceback

            traceback.print_exc()
            failures.append(f"probe raised {type(exc).__name__}: {exc}")
            app.quit()
            return False

        def _probe(panel):
            width, height = panel.get_width(), panel.get_height()
            print(f"\n  card as opened: {width}x{height}")
            overlay = panel.get_child()
            # GtkOverlay keeps its overlay children as *internal* children, so
            # the ordinary get_first_child walk does not see them.
            model = overlay.observe_children()
            widgets = [model.get_item(i) for i in range(model.get_n_items())]
            grip_widgets = [w for w in widgets if w.has_css_class("lintranslator-grip-area")]
            check(
                len(grip_widgets) == 3,
                f"three resize grips exist (found {len(grip_widgets)})",
            )

            print("\n-- hit testing (what the event delivery would pick) --")
            points = {
                "east edge": (width - 2, height // 2),
                "south edge": (width // 2, height - 2),
                "south-east corner": (width - 3, height - 3),
                "top-left corner (no grip)": (2, 2),
                "centre (no grip)": (width // 2, height // 2),
            }
            for label, (x, y) in points.items():
                picked = panel.pick(x, y, Gtk.PickFlags.DEFAULT)
                hit = picked is not None and picked.has_css_class("lintranslator-grip-area")
                expects_grip = "no grip" not in label
                check(
                    hit == expects_grip,
                    f"{label}: picked {type(picked).__name__ if picked else None} "
                    f"(grip={hit}, expected {expects_grip})",
                )

            print("\n-- a drag on each grip --")
            for edge, dx, dy in (("e", 120, 0), ("s", 0, 90), ("se", 80, 60)):
                before = (panel.get_width(), panel.get_height())
                panel._on_resize_begin(None, 0.0, 0.0)
                panel._on_resize_update(None, dx, dy, edge)
                pump(400)
                after = (panel.get_width(), panel.get_height())
                check(
                    after[0] >= before[0] and after[1] >= before[1] and after != before,
                    f"drag {edge!r} by ({dx},{dy}): {before} -> {after}",
                )
                if edge == "e":
                    check(after[1] == before[1], f"drag {edge!r} left the height alone")
                if edge == "s":
                    check(after[0] == before[0], f"drag {edge!r} left the width alone")
                panel._on_resize_end(None, dx, dy, edge)

            print("\n-- the size is remembered --")
            check(cfg.display.height > 0, f"pinned height written to config ({cfg.display.height})")
            # A drag that never touched the south edge must not pin the height,
            # or the line budgets silently stop mattering.
            cfg.display.height = 0
            panel._on_resize_begin(None, 0.0, 0.0)
            panel._on_resize_update(None, 40, 0, "e")
            pump(300)
            panel._on_resize_end(None, 40, 0, "e")
            check(
                cfg.display.height == 0,
                f"an east-only drag left the height on the budgets ({cfg.display.height})",
            )
            check(cfg.display.width == panel.get_width(), "an east-only drag pinned the width")
            check(
                cfg.display.width == panel.get_width(),
                f"the width is on the config ({cfg.display.width} vs {panel.get_width()})",
            )
            check(
                cfg.display.height == 0,
                f"the height is unpinned again after a sideways drag ({cfg.display.height})",
            )

            print("\n-- a shrink is floored by the contents --")
            panel._on_resize_begin(None, 0.0, 0.0)
            panel._on_resize_update(None, -9999, -9999, "se")
            pump(400)
            floored = (panel.get_width(), panel.get_height())
            floor_w, floor_h = panel._card_minimum()
            check(
                floored[0] >= floor_w and floored[1] >= floor_h,
                f"clamped to the card minimum {floor_w}x{floor_h}, got {floored[0]}x{floored[1]}",
            )
            panel._on_resize_end(None, 0.0, 0.0, "se")

            print("\n-- content still must not move a resized card --")
            from lintranslator.pipeline import Event

            def translation(target):
                return Event(
                    source="[The committee has resolved that this entry warrants "
                    "retention as a standing record.]",
                    target=target,
                    confidence=94.0,
                    translate_elapsed=1.7,
                    total_elapsed=1.9,
                    cached=False,
                    backend="openrouter",
                )

            panel._show_event(translation("はい。"))
            pump(300)
            short = (panel.get_width(), panel.get_height())
            panel._show_event(translation(
                "Of all the work done by our team during the last deployment, "
                "hers alone deserves praise. It was an efficient way of defusing "
                "the situation. The file will be kept in full, and the request "
                "submitted today shall be attached to it as an appendix."
            ))
            pump(300)
            long = (panel.get_width(), panel.get_height())
            check(
                short == long,
                f"card stayed {short} with a long line too (was {short}, now {long})",
            )

            app.quit()
            return False

        GLib.timeout_add(1400, probe)

    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK — the panel resizes from its east, south and south-east edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
