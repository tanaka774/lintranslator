"""Measure the region picker's sidebar: who sets its width, and how tall it is.

Run:  .venv-gi/bin/python probe/picker_sidebar_check.py

Two questions, both about the picker's right column:

* **Width.** The sidebar is wrapped in a Gtk.ScrolledWindow so its wrapping
  labels cannot dictate the window width, yet in `data/render_picker_idle.png`
  the sidebar looks far wider than its 320px request while the canvas - which
  has `hexpand` - gets the smaller half. An unwrapped label's *minimum* width is
  its whole text, and `propagate-natural-width=False` only suppresses the
  natural width, so the floor has to be found rather than guessed at.
* **Height.** How much of the column the content actually asks for, so the empty
  space at the bottom can be attributed to something instead of eyeballed.

It prints the widget requests first, then presents the window once with
realistic content and prints what each widget was actually allocated.
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from PIL import Image  # noqa: E402

from tlkun import picker as picker_mod  # noqa: E402
from tlkun.config import Config  # noqa: E402

CONFIG = Path("/home/chiba/workspace/tl-kun/config.json")

# What the picker really holds once a region has been dragged: the coordinate
# readout is one long unwrapped line and the OCR text is a whole sentence.
COORDS = "x=307 y=1213 w=1175 h=165   (fractions 0.1199, 0.8424, 0.4590, 0.1146)"
OCR = (
    "Hm. Of all the actions taken by our men during the last operation, his "
    "alone merits compliment. It was an efficient method of neutralizing the "
    "enemy. 2"
)
TRANSLATION = (
    "我々の部下が前回の作戦で行った行動の中で、彼の功績だけが称賛に値する。"
    "それは敵を無力化する効率的な方法であった。"
)


def png(size=(2560, 1440)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (40, 44, 52)).save(buffer, format="PNG")
    return buffer.getvalue()


def sizes(widget: Gtk.Widget, at_width: int = -1) -> tuple[int, int, int]:
    min_w, nat_w, _, _ = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
    _, nat_h, _, _ = widget.measure(Gtk.Orientation.VERTICAL, at_width)
    return min_w, nat_w, nat_h


def tree(widget: Gtk.Widget, depth: int = 0) -> None:
    """Every widget under `widget`, with its request and (once laid out) size."""
    alloc = widget.get_allocation()
    css = " ".join(widget.get_css_classes()) or "-"
    min_w, nat_w, _ = sizes(widget, -1)
    print(
        f"  {'  ' * depth}{type(widget).__name__:<14}"
        f" min_w={min_w:>5} nat_w={nat_w:>5} alloc={alloc.width:>5}x{alloc.height:<5}"
        f" [{css}]"
    )
    child = widget.get_first_child()
    while child is not None:
        tree(child, depth + 1)
        child = child.get_next_sibling()


def report_live(picker) -> bool:
    print("-- after layout, window at its default size --")
    print(f"  window {picker.get_width()}x{picker.get_height()}")
    for name, widget in (
        ("root box", picker.get_child()),
        ("canvas", picker.area),
        ("sidebar scroller", picker.side_scroller),
        ("sidebar box", picker.sidebar),
    ):
        alloc = widget.get_allocation()
        print(
            f"  {name:<18} alloc={alloc.width:>5}x{alloc.height:<5} "
            f"x={alloc.x:<5} y={alloc.y}"
        )
    print()
    print("-- the sidebar, as allocated --")
    tree(picker.sidebar)
    used = 0
    child = picker.sidebar.get_first_child()
    while child is not None:
        used += child.get_allocation().height
        child = child.get_next_sibling()
    gap = picker.sidebar.get_height() - used
    print(f"  children total {used} px of {picker.sidebar.get_height()} px"
          f" -> {gap} px unclaimed (box spacing not counted)")
    return False


def main() -> int:
    if not Gtk.init_check():
        print("no display; cannot measure")
        return 1

    cfg = Config.load(str(CONFIG))
    cfg.translate.backend = "none"
    app = Gtk.Application(application_id="dev.tlkun.sidebar.check", flags=0)

    def on_activate(_app) -> None:
        picker = picker_mod.RegionPicker(app, cfg, screenshot_png=png())
        picker._panel.present = lambda: None
        picker._panel.set_visible(False)

        print("-- requests, before any window exists --")
        for name, widget in (
            ("canvas", picker.area),
            ("sidebar scroller", picker.side_scroller),
            ("sidebar box", picker.sidebar),
            ("root box", picker.get_child()),
            ("window", picker),
        ):
            min_w, nat_w, nat_h = sizes(widget, 320)
            print(
                f"  {name:<18} min_w={min_w:>5}  natural_w={nat_w:>5}  "
                f"natural_h={nat_h:>5}"
            )
        print()

        print("-- sidebar children, in order --")
        child = picker.sidebar.get_first_child()
        index = 0
        while child is not None:
            min_w, nat_w, nat_h = sizes(child, 320)
            css = " ".join(child.get_css_classes()) or "-"
            print(
                f"  {index}:{type(child).__name__:<12} min_w={min_w:>5} "
                f"natural_w={nat_w:>6} h@320={nat_h:>4} [{css}]"
            )
            child = child.get_next_sibling()
            index += 1
        print()

        picker.coords_label.set_text(COORDS)
        picker.ocr_label.set_text(OCR)
        picker.conf_label.set_text("confidence 95   2 line(s)")
        picker.trans_label.set_text(TRANSLATION)
        picker.present()
        # A separate slot: changing text above invalidates the cached render
        # node, so the allocation is only meaningful a frame or two later.
        GLib.timeout_add(900, report_live, picker)
        GLib.timeout_add(1400, lambda: (picker._shutdown_panel(), app.quit(), False)[2])

    app.connect("activate", on_activate)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    app.run([])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
