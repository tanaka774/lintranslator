"""Ask the compositor for a global shortcut and report what happens.

Wayland forbids reading global keys, so the shortcut has to be granted by the
compositor through `org.freedesktop.portal.GlobalShortcuts`. KDE shows its own
binding dialog the first time (the app id is remembered after that), so this probe
prints every step and waits for an answer rather than assuming one.

If you press the bound key while this is running, it prints ACTIVATED - which is
the whole mechanism the Re-read hotkey uses.

Run:  .venv-gi/bin/python probe/hotkey_check.py [seconds]
"""
import sys

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from tlkun.hotkey import REREAD_DESCRIPTION, REREAD_TRIGGER, GlobalHotkey  # noqa: E402

WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
state = {"status": None, "activated": 0}


def on_status(ok: bool, detail: str) -> None:
    state["status"] = (ok, detail)
    print(f"[{'bound' if ok else 'not bound'}] {detail}", flush=True)


def on_activated(shortcut_id: str) -> None:
    state["activated"] += 1
    print(f"ACTIVATED: {shortcut_id} (press {state['activated']})", flush=True)


def main() -> int:
    app = Gtk.Application(
        application_id="dev.tlkun.hotkeycheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    hotkey = GlobalHotkey(on_activated=on_activated, on_status=on_status)

    def on_activate(_app):
        window = Gtk.ApplicationWindow(application=_app, title="tl-kun hotkey probe")
        window.set_default_size(360, 90)
        window.set_child(
            Gtk.Label(
                label=(
                    f"Asking the compositor to bind {REREAD_TRIGGER}\n"
                    f"({REREAD_DESCRIPTION})"
                )
            )
        )
        window.present()
        print(f"binding {REREAD_TRIGGER} ... (the compositor may show a dialog)", flush=True)
        hotkey.bind()
        return False

    def finish():
        ok, detail = state["status"] or (False, "no answer")
        print("\n" + "=" * 70)
        print(f"result: {'BOUND' if ok else 'NOT BOUND'} — {detail}")
        print(f"activations seen: {state['activated']}")
        print("=" * 70)
        hotkey.close()
        app.quit()
        return False

    app.connect("activate", on_activate)
    GLib.timeout_add(int(WAIT * 1000), finish)
    app.run([])
    return 0 if state["status"] and state["status"][0] else 1


if __name__ == "__main__":
    raise SystemExit(main())
