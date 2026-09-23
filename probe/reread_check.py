"""Does Re-read work end to end: button -> pipeline, and `tlkun reread` -> GUI?

Drives the real picker/panel, starts watching, then asks for a re-read three ways -
the button, the control socket, and the `tlkun reread` CLI in a separate process -
and reports what the panel did each time.

The translation backend is forced to `none` so this costs nothing and needs no
network; what is being tested is the re-read path, not the model.

Run:  .venv-gi/bin/python probe/reread_check.py
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, "/home/chiba/workspace/tl-kun")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from tlkun.config import Config  # noqa: E402
from tlkun.control import send  # noqa: E402
from tlkun.portal import ScreenshotPortal  # noqa: E402

CONFIG = Path("/tmp/tlkun_probe_config.json")  # never write the real config
# A writable XDG_RUNTIME_DIR for this probe: this sandbox blocks /run/user/1000,
# which a normal desktop session allows. The CLI subprocess gets the same value,
# which is what lets two processes agree on the socket path.
RUNTIME = Path("/home/chiba/workspace/tl-kun/data/probe_runtime")
RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ["XDG_RUNTIME_DIR"] = str(RUNTIME)
VENV = Path("/home/chiba/workspace/tl-kun/.venv-gi/bin/tlkun")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'ok ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}", flush=True)


def guard(step):
    """Run a step, and carry on to the next one even if it raises."""

    def wrapped(*args):
        try:
            return step(*args)
        except Exception as exc:  # noqa: BLE001
            check(f"{step.__name__} raised", False, f"{type(exc).__name__}: {exc}")
            return False

    return wrapped


def main() -> int:
    cfg = Config.load("/home/chiba/workspace/tl-kun/config.json")
    cfg.path = CONFIG
    cfg.translate.backend = "none"
    cfg.capture.fps = 2.0

    portal = ScreenshotPortal()
    png, size, elapsed = portal.grab()
    portal.close()
    print(f"screen {size} in {elapsed * 1000:.0f} ms", flush=True)

    app = Gtk.Application(
        application_id="dev.tlkun.rereadcheck", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    state: dict = {"panel": None, "picker": None, "before": None}

    @guard
    def start_watching():
        picker = state["picker"]
        picker._on_start()
        print(f"\nwatching: picker mapped={picker.get_mapped()}", flush=True)
        control = picker._panel.control
        check("control socket listening", control.listening, control.error or str(control.path))
        GLib.timeout_add(5000, step_button)
        return False

    @guard
    def step_button():
        panel = state["panel"]
        state["before"] = panel.last_event
        panel.reread_btn.emit("clicked")
        check(
            "button sets the status",
            panel.status_label.get_text().startswith("re-reading"),
            panel.status_label.get_text(),
        )
        GLib.timeout_add(4000, step_socket)
        return False

    @guard
    def step_socket():
        # A thread on purpose: this probe shares a process with the GUI, and the
        # GUI answers the socket on its main loop - a synchronous call from that
        # same loop would deadlock. `tlkun reread` is a separate process.
        replies: list[str] = []

        def call():
            try:
                replies.append(send("reread"))
            except Exception as exc:  # noqa: BLE001
                replies.append(f"error: {exc}")

        threading.Thread(target=call, daemon=True).start()

        def wait():
            check("control socket answers", replies == ["re-reading"], str(replies))
            GLib.timeout_add(1500, step_cli)
            return False

        GLib.timeout_add(1500, wait)
        return False

    @guard
    def step_cli():
        panel = state["panel"]
        out: dict = {}

        def call():
            # Both subprocesses in a thread: `subprocess.run` blocks, and this
            # probe's own main loop is what answers the socket. A real user's
            # `tlkun reread` waits on a loop that is free.
            env = dict(os.environ, XDG_RUNTIME_DIR=str(RUNTIME))
            out["reread"] = subprocess.run(
                [str(VENV), "--config", str(CONFIG), "reread"],
                capture_output=True,
                text=True,
                env=env,
            )
            out["status"] = subprocess.run(
                [str(VENV), "--config", str(CONFIG), "status"],
                capture_output=True,
                text=True,
                env=env,
            )

        threading.Thread(target=call, daemon=True).start()

        def wait():
            if "status" not in out:
                return True  # still running
            proc, status = out["reread"], out["status"]
            check(
                "`tlkun reread` from another process",
                proc.returncode == 0 and "re-reading" in proc.stdout,
                f"rc={proc.returncode} out={proc.stdout.strip()!r} err={proc.stderr.strip()!r}",
            )
            check(
                "`tlkun status` reports state",
                "watching" in status.stdout,
                status.stdout.strip(),
            )
            check(
                "a translation arrived (the re-read translated it)",
                panel.last_event is not None,
                f"last event: {(panel.last_event.source[:40] if panel.last_event else None)!r}",
            )
            GLib.timeout_add(800, finish)
            return False

        GLib.timeout_add(200, wait)
        return False

    @guard
    def finish():
        panel = state["panel"]
        print(f"\npanel status: {panel.status_label.get_text()!r}", flush=True)
        if panel.worker is not None and panel.worker.pipeline is not None:
            print(f"pipeline stats: {panel.worker.pipeline.stats.as_dict()}", flush=True)
        state["picker"]._shutdown_panel()
        app.quit()
        return False

    def on_activate(_app):
        from tlkun.picker import RegionPicker

        picker = RegionPicker(app, cfg, screenshot_png=png)
        picker._panel.hotkey_enabled = False  # no compositor dialog in a probe
        picker.present()
        state["picker"] = picker
        state["panel"] = picker._panel
        GLib.timeout_add(1200, start_watching)

    app.connect("activate", on_activate)
    GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
    app.run([])

    print("\n" + "=" * 70)
    failed = [name for name, ok, _ in results if not ok]
    for name, ok, _ in results:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print("PASS" if not failed else f"{len(failed)} FAILED")
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
