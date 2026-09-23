"""Does Gtk.Window.minimize() actually take a window off screen here?

The pipeline resumes reading the moment the picker unmaps, so this decides
whether minimising keeps the picker's taskbar entry while watching, or whether
the hide fallback is what really runs.

Measured on this machine (KDE Plasma / kwin_wayland 6.7.4): **minimize() is
ignored** - the window stays mapped for the whole two seconds after the call - so
`picker._ensure_off_screen` is the path that takes effect, and the panel's Region
button is the only way back. Run this again after a compositor or GTK upgrade to
see whether that is still true.

Run:  .venv/bin/python probe/minimise_check.py
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

SECONDS = 2.0
app = Gtk.Application(
    application_id="dev.lintranslator.minimisecheck", flags=Gio.ApplicationFlags.NON_UNIQUE
)
state = {"t0": None, "samples": []}


def on_activate(application):
    win = Gtk.ApplicationWindow(application=application, title="lintranslator minimise probe")
    win.set_default_size(400, 200)
    win.present()

    def minimise():
        state["t0"] = GLib.get_monotonic_time() / 1e6
        win.minimize()
        GLib.timeout_add(50, sample)
        return False

    def sample():
        t = GLib.get_monotonic_time() / 1e6 - state["t0"]
        state["samples"].append((round(t, 2), win.get_mapped()))
        if t < SECONDS:
            return True
        unmapped = [s for s in state["samples"] if not s[1]]
        print("mapped samples:", [(s[0], s[1]) for s in state["samples"][::4]], flush=True)
        print(
            "minimize() unmapped the window:",
            ("YES at t=%.2f" % unmapped[0][0]) if unmapped else "NO",
            flush=True,
        )
        application.quit()
        return False

    GLib.timeout_add(800, minimise)


app.connect("activate", on_activate)
app.run([])
