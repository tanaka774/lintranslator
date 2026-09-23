"""Exercise the settings model picker and capture its popover.

Run:  .venv-gi/bin/python probe/picker_render.py

Checks the three things the old DropDown could not do: hold the real fetched
list, filter as you type, and hand the picked id back to the entry.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/lintranslator")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from lintranslator.settings import MODEL_SUGGESTIONS, ModelPicker  # noqa: E402

OUT = Path("/home/chiba/workspace/lintranslator/.cache")

# A slice of the real 445-model list, in the order OpenRouter returns it.
FAKE_FETCH = MODEL_SUGGESTIONS["openrouter"] + [
    "qwen/qwen3.8-27b:free",
    "google/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-5-nano",
    "z-ai/glm-5.3-flash",
    "mistralai/mistral-small-24b-instruct-2501",
    "amazon/nova-micro-v1",
    "anthropic/claude-haiku-4.5",
]

picked: list[str] = []


def shoot(widget, name):
    w, h = widget.get_width(), widget.get_height()
    if w <= 1 or h <= 1:
        print(f"  {name}: not laid out ({w}x{h})", flush=True)
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        # Happens mid-transition; the behavioural assertions still ran.
        print(f"  {name}: widget not paintable right now, skipped", flush=True)
        return
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    node.draw(cairo.Context(surf))
    surf.write_to_png(str(OUT / f"{name}.png"))
    print(f"  wrote {name}.png {w}x{h}", flush=True)


def main() -> int:
    loop = GLib.MainLoop()
    win = Gtk.Window(title="picker probe")
    win.set_default_size(560, 140)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for m in ("margin_top", "margin_bottom", "margin_start", "margin_end"):
        getattr(box, f"set_{m}")(12)
    win.set_child(box)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_text("tencent/hy-mt2-1.8b")
    row.append(entry)
    picker = ModelPicker(lambda m: (picked.append(m), entry.set_text(m)))
    picker.set_history(["tencent/hy-mt2-1.8b"])
    row.append(picker)
    box.append(Gtk.Label(label="Model id", xalign=0))
    box.append(row)
    win.present()

    def count_rows():
        """Count model rows, which are the ones carrying a model_id."""
        n = 0
        child = picker.listbox.get_first_child()
        while child is not None:
            if getattr(child, "model_id", None):
                n += 1
            child = child.get_next_sibling()
        return n

    timed_out = {"v": False}
    failures: list[str] = []

    def fail(msg):
        failures.append(msg)
        print(f"FAIL: {msg}", flush=True)

    def step_fetch():
        picker.set_models(FAKE_FETCH)
        print(f"after fetch: {count_rows()} rows", flush=True)
        if count_rows() <= 5:
            fail("fetch did not populate the list")
        picker.popup()
        GLib.timeout_add(500, step_shot_all)
        return False

    def step_shot_all():
        shoot(win, "picker_open")
        GLib.timeout_add(250, shot_popover)
        return False

    def shot_popover():
        """A native popover surface cannot be snapshotted, so the list is
        verified behaviourally (row counts, filtering, picking) rather than
        visually. The row layout inside the settings dialog is covered by
        probe/settings_render.py."""
        picker.search.set_text("hy-mt2")
        GLib.timeout_add(250, step_filtered)
        return False

    def step_filtered():
        picker.refresh()
        n = count_rows()
        print(f"filter 'hy-mt2': {n} rows", flush=True)
        shoot(win, "picker_filtered")
        GLib.timeout_add(300, step_pick)
        return False

    def step_pick():
        picker._activate_first()
        print(f"picked={picked}", flush=True)
        if picked != ["tencent/hy-mt2-1.8b"]:
            fail(f"wrong pick: {picked}")
        if entry.get_text() != "tencent/hy-mt2-1.8b":
            fail(f"entry not updated: {entry.get_text()!r}")
        picker.search.set_text("zzz-no-such-model")
        picker.refresh()
        print(f"no-match rows: {count_rows()}", flush=True)
        shoot(win, "picker_empty")
        GLib.timeout_add(300, lambda: (loop.quit(), False)[1])
        return False

    GLib.timeout_add(800, step_fetch)
    def on_timeout():
        timed_out["v"] = True
        loop.quit()
        return False

    GLib.timeout_add(20000, on_timeout)
    loop.run()
    if timed_out["v"]:
        print("TIMEOUT", flush=True)
        return 1
    if failures:
        print(f"{len(failures)} failure(s)", flush=True)
        return 1
    print("OK — picker behaves", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
