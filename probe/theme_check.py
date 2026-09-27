"""Check that the app's own surfaces win over the desktop theme."""
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

FORCED = os.environ.get("LINTRANSLATOR_PROBE_THEME", "Breeze:light")
if "--as-is" not in sys.argv and os.environ.get("GTK_THEME") != FORCED:
    # Before gi is imported: GTK reads GTK_THEME when it initialises.
    os.execvpe(
        sys.executable,
        [sys.executable, str(Path(__file__).resolve()), "--as-is"],
        dict(os.environ, GTK_THEME=FORCED),
    )

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from lintranslator import theme  # noqa: E402
from lintranslator.config import Config  # noqa: E402

OUT = APP_DIR / ".cache"
TIMEOUT_MS = 30_000


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


POPOVER_RGB = hex_rgb(theme.SURFACE_POPOVER)

failures: list[str] = []
skipped = {"v": False}
shots: dict[str, Path] = {}


def shoot(widget, name: str) -> None:
    """Render `widget` to a PNG and remember where it went."""
    w, h = widget.get_width(), widget.get_height()
    if w <= 1 or h <= 1:
        failures.append(f"{name}: not laid out ({w}x{h})")
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        failures.append(f"{name}: no render node")
        return
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    node.draw(cairo.Context(surf))
    path = OUT / f"theme_check_{name}.png"
    surf.write_to_png(str(path))
    shots[name] = path


def pixel(name: str, x: int, y: int) -> tuple[int, int, int, int]:
    """The RGBA pixel at (x, y) of a shot, or (0, 0, 0, 0) when it is missing."""
    from PIL import Image

    path = shots.get(name)
    if path is None:
        return (0, 0, 0, 0)
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        if not (0 <= x < rgba.width and 0 <= y < rgba.height):
            failures.append(f"{name}: sample ({x}, {y}) is outside {rgba.size}")
            return (0, 0, 0, 0)
        return rgba.getpixel((x, y))


def find_descendant(widget, kind):
    """The first `kind` in `widget`'s subtree, or None."""
    if isinstance(widget, kind):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_descendant(child, kind)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def row_sample(child, ancestor, *, from_right: int = 15, height_fraction: float = 0.5):
    """A point inside `child` (a row), `from_right` px in from its right edge."""
    ok, rect = child.compute_bounds(ancestor)
    if not ok:
        failures.append("a row could not be located inside its parent")
        return None
    return (
        int(rect.origin.x + rect.size.width - from_right),
        int(rect.origin.y + rect.size.height * height_fraction),
    )


def expect_dark(name: str, point, what: str) -> None:
    """The surface at `point` must be this app's dark popover, not the theme's."""
    if point is None:
        return
    got = pixel(name, *point)
    ok = got[3] == 255 and sum(got[:3]) < 200
    print(f"  {what:<22} {got[:3]} at {point}", flush=True)
    if not ok:
        failures.append(
            f"{what} is {got[:3]} at {point} - the desktop theme's colour, not "
            f"this app's {POPOVER_RGB}"
        )


def make_listbox() -> Gtk.ListBox:
    lb = Gtk.ListBox()
    lb.add_css_class("navigation-sidebar")
    for text in ("google/gemini-2.5-flash-lite", "tencent/hy-mt2-1.8b"):
        row = Gtk.ListBoxRow()
        row.set_child(Gtk.Label(label=text, xalign=0))
        lb.append(row)
    return lb


def main() -> int:
    theme.install_for(Config.load())
    loop = GLib.MainLoop()

    plain = Gtk.Window(title="theme check - control")
    plain.set_default_size(360, 120)
    control = make_listbox()
    plain.set_child(control)
    plain.present()

    win = Gtk.Window(title="theme check")
    win.add_css_class("lintranslator-app")
    win.set_default_size(420, 260)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.set_child(box)

    picker = Gtk.MenuButton(label="models")
    popover = Gtk.Popover()
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    inner.append(Gtk.SearchEntry())
    listed = make_listbox()
    inner.append(listed)
    popover.set_child(inner)
    picker.set_popover(popover)
    box.append(picker)

    dropdown = Gtk.DropDown.new_from_strings(["openrouter", "openai", "chat"])
    box.append(dropdown)

    view = Gtk.TextView()
    view.set_size_request(-1, 80)
    view.get_buffer().set_text("translate {source} into {target}, one line only")
    box.append(view)
    win.present()

    def step_picker():
        picker.popup()
        GLib.timeout_add(500, step_shot_picker)
        return False

    def step_shot_picker():
        shoot(control, "control")
        shoot(popover, "list")
        list_point = row_sample(listed.get_row_at_index(1), popover)
        picker.popdown()
        GLib.timeout_add(300, lambda: (step_dropdown(list_point), False)[1])
        return False

    def step_dropdown(list_point):
        # a DropDown's popup is private and has no public "open" call - toggle its button
        dropdown.get_first_child().set_active(True)
        GLib.timeout_add(700, lambda: (step_shot_dropdown(list_point), False)[1])
        return False

    def step_shot_dropdown(list_point):
        popup = None
        child = dropdown.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Popover):
                popup = child
            child = child.get_next_sibling()
        view_point = None
        if popup is None or popup.get_child() is None:
            failures.append("the DropDown popup did not open")
        else:
            shoot(popup, "view")
            listview = find_descendant(popup, Gtk.ListView)
            if listview is None:
                failures.append("the DropDown popup has no ListView")
            else:
                view_point = row_sample(listview, popup, from_right=15, height_fraction=0.0)
                if view_point is not None:
                    # row 0 rather than the middle: the list fills the popup and the surface shows there
                    view_point = (view_point[0], view_point[1] + 18)
        dropdown.get_first_child().set_active(False)
        GLib.timeout_add(300, lambda: (step_shot_textview(list_point, view_point), False)[1])
        return False

    def step_shot_textview(list_point, view_point):
        shoot(view, "textview")
        GLib.timeout_add(200, lambda: (report(list_point, view_point), False)[1])
        return False

    def report(list_point, view_point):
        # same sampling as the app's surfaces: inside a row, at its right end, not a border
        control_point = row_sample(control.get_row_at_index(1), plain)
        control_rgb = pixel("control", *control_point)[:3] if control_point else (0, 0, 0)
        print(f"  desktop theme paints lists {control_rgb}", flush=True)
        # under a dark theme this app's colour and the theme's are both dark, so a
        # pass would mean nothing - "cannot run here", not a failure
        if sum(control_rgb) < 400:
            print(
                f"cannot test: this theme paints lists {control_rgb}, which is not "
                "light.\nRun with no arguments to force Breeze:light, or set "
                "LINTRANSLATOR_PROBE_THEME=<theme>:light",
                flush=True,
            )
            skipped["v"] = True
            loop.quit()
            return False
        expect_dark("list", list_point, "popover list")
        expect_dark("view", view_point, "dropdown popup")
        # the prompt editor is translucent, so the check is that it is not the
        # theme's opaque light slab; sampled below the first line of text
        got = pixel("textview", 12, 60)
        print(f"  {'prompt editor':<22} {got} at (12, 60)", flush=True)
        if got[3] > 250 and sum(got[:3]) > 600:
            failures.append(f"the prompt editor is the theme's colour {got[:3]}")
        if failures:
            print(f"{len(failures)} failure(s):", flush=True)
            for message in failures:
                print(f"  FAIL: {message}", flush=True)
        else:
            print(f"OK - the app's surfaces are the app's, shots in {OUT}", flush=True)
        loop.quit()
        return False

    GLib.timeout_add(700, step_picker)

    def on_timeout():
        print("TIMEOUT", flush=True)
        failures.append("timed out")
        loop.quit()
        return False

    GLib.timeout_add(TIMEOUT_MS, on_timeout)
    loop.run()
    if skipped["v"]:
        return 2  # the convention the other probes use for "cannot run here"
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
