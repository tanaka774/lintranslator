"""Measure the panel's geometry, to check the card's size budget.

Run:  .venv-gi/bin/python probe/panel_layout_check.py

Builds the *real* panel widget tree (no pipeline, no network, no window shown)
and asks GTK for its natural and minimum sizes. The numbers this prints are the
ones the compositor would be handed.

The card is a fixed size by design: `display.target_lines` and
`display.source_lines` reserve space, and text past them scrolls rather than
resizing the card. So the questions here are different from the ones this probe
used to ask:

  * Does the card honour `display.width`, or does long text force it wider?
  * How tall is the card, and how much of that is text versus chrome?
  * Does the height depend on the text at all? (It must not.)

`panel_contact_sheet.py` renders the same states as PNGs; this prints numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from tlkun import panel as panel_mod  # noqa: E402
from tlkun import theme  # noqa: E402
from tlkun.config import Config  # noqa: E402
from tlkun.pipeline import Event  # noqa: E402

CONFIG = Path("/home/chiba/workspace/tl-kun/config.json")

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


def make_event(source: str, target: str) -> Event:
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


class Body(panel_mod.TranslatorPanel):
    """The panel's widget tree without the Gtk.ApplicationWindow machinery."""

    def __init__(self, config: Config):
        self.config = config
        # Everything `_build_body` touches that is not a widget.
        self.on_region_request = None
        self.last_event = None
        self.region_btn = None
        self.window = Gtk.Window()

    def set_default_size(self, *_args) -> None:
        """The one window-only call `apply_display_settings` makes."""

    def add_controller(self, controller) -> None:
        """Shortcuts are irrelevant to layout; the real window adds them."""

    def build(self) -> Gtk.Widget:
        panel_mod.TranslatorPanel.apply_display_settings(self)
        return self._build_body()


def measure(widget: Gtk.Widget, width: int) -> tuple[int, int, int, int]:
    """(min_w, natural_w, natural_h, h_at_width)."""
    min_w, nat_w, _, _ = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
    _, nat_h, _, _ = widget.measure(Gtk.Orientation.VERTICAL, -1)
    _, h_at, _, _ = widget.measure(Gtk.Orientation.VERTICAL, width)
    return min_w, nat_w, nat_h, h_at


def main() -> int:
    cfg = Config.load(str(CONFIG))
    cfg.translate.backend = "none"
    target_w = cfg.display.width

    if not Gtk.init_check():
        print("no display; cannot measure")
        return 1
    theme.install_for(cfg)

    display_cfg = cfg.display
    print(
        f"display.width={target_w}  base_font_size={display_cfg.base_font_size}  "
        f"font_scale={display_cfg.font_scale}  "
        f"target_lines={display_cfg.target_lines}  "
        f"source_lines={display_cfg.source_lines}  "
        f"show_source={display_cfg.show_source}"
    )
    print()

    body = Body(cfg)
    box = body.build()
    # `_apply_layout_budget` runs from the real window's `__init__`, after the
    # tree exists. Body bypasses that, so without this the scrolled areas have no
    # height request and the probe measures an unsized tree (118 px instead of
    # the ~205 px the compositor is actually given).
    body._apply_layout_budget()
    # The action buttons are placed by the overflow pass, so without this they
    # have no parent and contribute nothing to the card's measured width.
    body._relayout_id = 0
    body._relayout_controls()
    requested = body.target_scroll.get_size_request().height
    if requested <= 0:
        print("FAIL: the layout budget was not applied; measurement is meaningless")
        return 1
    window = Gtk.Window()
    window.set_child(box)

    cases = [
        ("idle", "Waiting for dialogue…", ""),
        ("one short line", "はい。", "Yes, sir."),
        ("the demo line", LONG_TARGET, LONG_SOURCE),
        ("a verbose 4-line reply", VERBOSE_TARGET, LONG_SOURCE),
        ("a 900-character reply", VERBOSE_TARGET * 4, LONG_SOURCE * 3),
    ]

    print("-- the card, by content --")
    heights: dict[str, int] = {}
    widths: dict[str, tuple[int, int]] = {}
    for name, target, source in cases:
        body._show_event(make_event(source, target))
        min_w, nat_w, nat_h, h_at = measure(box, target_w)
        heights[name] = h_at
        widths[name] = (min_w, nat_w)
        print(
            f"  {name:<24} min_w={min_w:>4}  natural_w={nat_w:>4}  "
            f"natural_h={nat_h:>4}  h@{target_w}={h_at:>4}"
        )

    print()
    print("-- status and error variants --")
    body.target_label.set_text(LONG_TARGET)
    body.source_label.set_text(LONG_SOURCE)
    for name, action in (
        (
            "translation status",
            lambda: body.status_label.set_text("12:04:31 · conf 94 · 1690 ms · +1.9s"),
        ),
        (
            "long error",
            lambda: body._show_error(
                "HTTPError: 429 Too Many Requests (retry in 12 seconds)"
            ),
        ),
        ("cleared error", lambda: body._show_error("")),
    ):
        action()
        _, _, _, h_at = measure(box, target_w)
        heights[name] = h_at
        print(f"  {name:<24} h@{target_w}={h_at:>4}")

    print()
    print("-- where the height goes --")
    target_reserved = body._reserved_height(body.target_label, display_cfg.target_lines)
    source_reserved = body._reserved_height(body.source_label, display_cfg.source_lines)
    control_row = body.menu_btn.get_parent()
    _, _, _, control_h = measure(control_row, target_w)
    _, _, _, header_h = measure(body.backend_label.get_parent().get_parent(), target_w)
    card_h = heights["the demo line"]
    chrome = card_h - target_reserved - source_reserved
    print(f"  card height          {card_h:>4}")
    print(f"  translation area     {target_reserved:>4}  ({display_cfg.target_lines} lines)")
    print(f"  original area        {source_reserved:>4}  ({display_cfg.source_lines} lines)")
    print(f"  header               {header_h:>4}")
    print(f"  control row          {control_h:>4}")
    print(f"  rules, padding, rest {chrome - header_h - control_h:>4}")
    print(f"  chrome total         {chrome:>4}  ({chrome * 100 // card_h}% of the card)")

    print()
    print("-- the invariants --")
    failures = []
    if len(set(heights.values())) != 1:
        failures.append(f"card height varies with content: {sorted(set(heights.values()))}")
    for name, (min_w, _nat_w) in widths.items():
        if min_w > target_w:
            failures.append(f"{name}: minimum width {min_w} exceeds display.width {target_w}")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print(f"  OK — height is {card_h} px for every content and status variant")
    print(f"  OK — minimum width stays within display.width ({target_w} px)")

    if sweep():
        return 1
    return 0


def sweep() -> int:
    """The same invariants across font scales, widths and line budgets.

    The line height is measured from the font, so the budget has to grow with
    `font_scale` on its own. A hardcoded line height would pass at the default
    scale and clip the text at 2.0, which is the setting a user reaches for when
    the game's text is small.
    """
    print()
    print("-- the same invariants across settings --")
    failures = []
    print(
        f"  {'font':>5} {'width':>6} {'tgt':>4} {'src':>4} "
        f"{'card_h':>7} {'min_w':>6} {'tgt_px':>7} {'src_px':>7}"
    )
    for scale in (0.7, 1.0, 1.2, 2.0, 2.6):
        for width in (420, 900):
            for target_lines, source_lines in ((1, 1), (3, 2), (8, 6)):
                cfg = Config.load(str(CONFIG))
                cfg.translate.backend = "none"
                cfg.display.font_scale = scale
                cfg.display.width = width
                cfg.display.target_lines = target_lines
                cfg.display.source_lines = source_lines
                theme.install_for(cfg)

                body = Body(cfg)
                box = body.build()
                body._apply_layout_budget()
                window = Gtk.Window()
                window.set_child(box)

                body._show_event(make_event("Yes, sir.", "はい。"))
                _, _, _, short_h = measure(box, width)
                body._show_event(make_event(LONG_SOURCE, VERBOSE_TARGET * 3))
                min_w, _, _, long_h = measure(box, width)

                target_px = body._reserved_height(body.target_label, target_lines)
                source_px = body._reserved_height(body.source_label, source_lines)
                print(
                    f"  {scale:>5} {width:>6} {target_lines:>4} {source_lines:>4} "
                    f"{short_h:>7} {min_w:>6} {target_px:>7} {source_px:>7}"
                )
                if short_h != long_h:
                    failures.append(
                        f"scale={scale} width={width} lines={target_lines}/"
                        f"{source_lines}: height moved {short_h} -> {long_h}"
                    )
                if target_px <= 0 or source_px <= 0:
                    failures.append(
                        f"scale={scale}: reserved {target_px}/{source_px} px is not a height"
                    )
                if min_w > width:
                    failures.append(
                        f"scale={scale} width={width}: minimum width {min_w} exceeds it"
                    )
                window.destroy()
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  OK — height is constant and minimum width is honoured in all of them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
