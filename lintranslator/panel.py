"""The always-on-top translation panel."""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import theme  # noqa: E402
from .config import Config  # noqa: E402
from .languages import short_code  # noqa: E402
from .occlusion import GUARD  # noqa: E402
from .pipeline import Event, Pipeline  # noqa: E402


@dataclass
class UiMessage:
    """Something the worker thread wants the UI to show."""

    kind: str  # "event" | "error" | "status" | "gate" | "self-read"
    event: Event | None = None
    text: str = ""
    detail: str = ""  # e.g. line-broken source for display


KEEP_ABOVE_SHORT = "Not always-on-top in this session — see the README, 'Always on top'"


class LabelButton(Gtk.Button):
    """A button whose label can be aligned; Gtk.Button's own label is always centred."""

    def __init__(self, label: str = "") -> None:
        super().__init__()
        self._label = Gtk.Label(label=label, xalign=0.5)
        self.set_child(self._label)

    def set_label(self, text: str) -> None:  # noqa: D102 - mirrors Gtk.Button
        self._label.set_text(text)

    def get_label(self) -> str:  # noqa: D102 - mirrors Gtk.Button
        return self._label.get_text()

    def set_label_align(self, xalign: float) -> None:
        """Centre (0.5) for the row, left (0.0) for a menu item."""
        self._label.set_xalign(xalign)
        self._label.set_hexpand(xalign == 0.0)


class PipelineThread:
    """Runs the pipeline loop off the GTK main thread."""

    def __init__(
        self,
        config: Config,
        outbox: queue.Queue[UiMessage],
        gate=None,
    ) -> None:
        self.config = config
        self.outbox = outbox
        # Why capturing must wait right now, or None; supplied by the owning window
        self.gate = gate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pipeline: Pipeline | None = None
        self.ready = threading.Event()
        self.warmup_error: Exception | None = None
        # Why the loop ended, when it ended by itself (read by `_check_worker`)
        self.error: str | None = None
        self._pending_region = None
        self._pending_reread = False

    @property
    def alive(self) -> bool:
        """Whether the loop is still running."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lintranslator-pipeline", daemon=True)
        self._thread.start()

    def request_region(self, region) -> None:
        """Read a different area, without rebuilding the pipeline."""
        self._pending_region = region
        pipe = self.pipeline
        if pipe is not None:
            pipe.request_region(region)

    def request_reread(self) -> None:
        """Read the box again now and translate it, cache and dedupe aside."""
        self._pending_reread = True
        pipe = self.pipeline
        if pipe is not None:
            pipe.request_reread()

    def _run(self) -> None:
        """Run the loop, and report it if it dies."""
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001 - a dead worker must be visible
            self.error = f"{type(exc).__name__}: {exc}"
            # Nothing escapes the thread for `threading.excepthook`, so print it here
            traceback.print_exc()
            self.outbox.put(
                UiMessage("error", text=f"translation stopped: {self.error}")
            )

    def _loop(self) -> None:
        pipe = Pipeline(
            self.config,
            on_event=self._on_event,
            gate=self.gate,
            on_gate=self._on_gate,
            on_self_read=self._on_self_read,
            on_note=self._on_note,
        )
        pipe.on_error = lambda exc: self.outbox.put(  # type: ignore[attr-defined]
            UiMessage("error", text=f"{type(exc).__name__}: {exc}")
        )
        if self._pending_region is not None:
            pipe.request_region(self._pending_region)
            self._pending_region = None
        if self._pending_reread:
            pipe.request_reread()
            self._pending_reread = False
        self.pipeline = pipe
        try:
            pipe.warmup()
        except Exception as exc:  # noqa: BLE001
            self.warmup_error = exc
            self.error = f"startup failed: {exc}"
            self.outbox.put(UiMessage("error", text=f"startup failed: {exc}"))
            self.ready.set()
            return
        self.ready.set()
        self.outbox.put(UiMessage("status", text="watching the region"))
        # Config warnings; posted after the status line so the message survives
        for warning in list(getattr(self.config, "warnings", None) or []):
            self.outbox.put(UiMessage("note", text=warning))

        pipe.start()
        try:
            while not self._stop.is_set():
                pipe.step()
                # Sleep in slices of at most 0.1 s so stop() stays responsive
                self._stop.wait(min(pipe.sleep_time(), 0.1))
        finally:
            # Said before `close`: an exception there would swallow the stop line
            report = pipe.report()
            self.outbox.put(
                UiMessage(
                    "status",
                    text=(
                        f"stopped - {report['stats']['polls']} polls, "
                        f"{report['stats']['translations']} translated, "
                        f"{report['stats']['errors']} errors"
                    ),
                )
            )
            try:
                pipe.close()
            except Exception:  # noqa: BLE001 - closing must not hide the stop
                pass

    def _on_event(self, event: Event) -> None:
        self.outbox.put(UiMessage("event", event=event))

    def _on_gate(self, reason: str) -> None:
        self.outbox.put(UiMessage("gate", text=reason))

    def _on_self_read(self, text: str) -> None:
        self.outbox.put(UiMessage("self-read", text=text))

    def _on_note(self, message: str) -> None:
        self.outbox.put(UiMessage("note", text=message))

    def stop(self, timeout: float = 1.0) -> None:
        """Signal the loop to end and wait briefly for it to unwind."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None


class TranslatorPanel(Gtk.ApplicationWindow):
    """Frameless, always-on-top window showing the latest translation."""

    def __init__(
        self,
        app: Gtk.Application,
        config: Config,
        position: tuple[int, int] | None,
        demo_event: Event | None = None,
    ):
        super().__init__(application=app, title="LinTranslator")
        self.config = config
        self.outbox: queue.Queue[UiMessage] = queue.Queue()
        self.worker: PipelineThread | None = None
        self.last_event: Event | None = None
        self._translation_started = 0.0
        # Why capturing must wait right now, or None; a plain callable, never a GTK call
        self.gate = GUARD.reason
        # Set by the region picker to bring it back; None hides the Select region button
        self.on_region_request = None
        self._gated = False
        self.control = None
        self.hotkey = None
        self.hotkey_enabled = True
        self._start_control()

        self._css_provider = None
        # Width the overflow pass last laid out for (-1 = not yet)
        self._layout_width = -1
        self._relayout_id = 0
        self._requested_size = (config.display.width, config.display.height)
        self.apply_display_settings()
        self.set_decorated(False)
        self.set_resizable(True)
        self._restore_size()
        self.add_css_class("lintranslator-panel")
        self.card = self._build_body()
        self.set_child(self._with_resize_grips(self.card))
        # Both need the widget tree, so they run after `_build_body`
        self._apply_layout_budget()
        self._relayout_controls()

        if position:
            # Works under X11/XWayland; a no-op on native Wayland by design.
            self.connect("realize", lambda *_: self._try_move(position))

        # Stop the pipeline when this window goes away, however it was closed
        self.connect("close-request", self._on_close_request)

        if demo_event is not None:
            self._show_event(demo_event)

        self._refresh_backend_label()
        if config.display.keep_above:
            # Ask for keep-above only once the surface exists
            self.connect("map", self._on_first_map)

        # Drain worker messages on the GTK main thread.
        GLib.timeout_add(80, self._drain)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        """Re-split the control row whenever the width changes."""
        Gtk.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        if width != self._layout_width:
            self._layout_width = width
            # Deferred to an idle: the tree it rearranges is being allocated here
            self._schedule_relayout()

    def apply_display_settings(self) -> None:
        """(Re)build the stylesheet and re-pin the card's size."""
        self._css_provider = theme.install_for(self.config)
        self._restore_size()
        self._apply_layout_budget()
        self._schedule_relayout()

    def _apply_layout_budget(self) -> None:
        """Pin each text area to the number of lines the config reserves."""
        if getattr(self, "target_scroll", None) is None:
            return
        display_cfg = self.config.display
        self.target_scroll.set_size_request(
            -1, self._reserved_height(self.target_label, display_cfg.target_lines)
        )
        self.source_scroll.set_size_request(
            -1, self._reserved_height(self.source_label, display_cfg.source_lines)
        )
        self.source_scroll.set_visible(display_cfg.show_source)
        self.source_label.set_visible(display_cfg.show_source)

    @staticmethod
    def _reserved_height(label: Gtk.Label, lines: int) -> int:
        """Height of `lines` lines of `label`'s font, measured not assumed."""
        was_visible = label.get_visible()
        was_text = label.get_text()
        label.set_visible(True)
        try:
            label.set_text("Mg")
            _, one, _, _ = label.measure(Gtk.Orientation.VERTICAL, -1)
            label.set_text("Mg\nMg")
            _, two, _, _ = label.measure(Gtk.Orientation.VERTICAL, -1)
        finally:
            label.set_text(was_text)
            label.set_visible(was_visible)
        line = max(1, two - one)
        count = max(1, lines)
        return one + (count - 1) * line

    def open_settings(self) -> None:
        from .settings import SettingsDialog

        dialog = SettingsDialog(self, self.config, on_apply=self._on_settings_applied)
        dialog.present()

    def _on_settings_applied(self) -> None:
        """Rebuild the pipeline so backend/model/prompt changes take effect."""
        self.apply_display_settings()
        self._refresh_backend_label()
        self.status_label.set_text("settings saved - restarting pipeline")
        if self.worker:
            self.worker.stop()
        self.start_pipeline()

    def _build_body(self) -> Gtk.Widget:
        """The card: a header strip, the translation, the original, one control row."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("lintranslator-card")

        self.backend_label = Gtk.Label(label="", xalign=0)
        self.backend_label.add_css_class("lintranslator-status")
        self.backend_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.backend_label.set_tooltip_text(
            "Drag this strip to move the card.\n"
            "Names the backend, the model, the language pair and the box being read."
        )
        grip = Gtk.Label(label="⠿", xalign=0)
        grip.add_css_class("lintranslator-grip")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(grip)
        header.append(self.backend_label)
        box.append(self._drag_handle(header))
        box.append(self._rule())

        self.target_label = Gtk.Label(label="Waiting for dialogue…", xalign=0)
        self.target_scroll = self._text_area(self.target_label, "lintranslator-target")
        box.append(self.target_scroll)

        self.source_label = Gtk.Label(label="", xalign=0)
        self.source_label.set_visible(False)
        self.source_scroll = self._text_area(self.source_label, "lintranslator-source")
        self.source_scroll.add_css_class("lintranslator-source-area")
        self.source_scroll.set_visible(False)
        box.append(self.source_scroll)

        box.append(self._rule())

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.button_row = row

        self.status_label = Gtk.Label(label="starting…", xalign=0)
        self.status_label.add_css_class("lintranslator-status")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_hexpand(True)
        row.append(self.status_label)

        self.error_label = Gtk.Label(label="", xalign=0)
        self.error_label.add_css_class("lintranslator-error")
        self.error_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.error_label.set_hexpand(True)
        self.error_label.set_visible(False)
        row.append(self.error_label)

        self._build_actions()
        self.menu = self._build_menu()
        # A plain button, not Gtk.MenuButton: its CSS node is `menubutton`, so the
        # theme's button styling does not match it
        self.menu_btn = Gtk.Button(label="⋮")
        self.menu_btn.set_tooltip_text("Everything that did not fit on the card")
        self.menu_btn.add_css_class("lintranslator-btn")
        self.menu_btn.add_css_class("lintranslator-icon")
        self.menu_btn.set_visible(False)
        self.menu_btn.connect("clicked", self._on_menu)
        row.append(self.menu_btn)
        for name in self.ACTION_ORDER:
            # Connected here so it runs after the button's own click handler
            getattr(self, name).connect("clicked", self._close_overflow)
        box.append(row)

        # In-window shortcuts: GLOBAL scope is the whole app, not the desktop
        shortcuts = Gtk.ShortcutController()
        shortcuts.set_scope(Gtk.ShortcutScope.GLOBAL)
        for accel in ("<Control>r", "F5"):
            shortcuts.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(accel),
                    Gtk.CallbackAction.new(self._on_shortcut_reread),
                )
            )
        self.add_controller(shortcuts)
        return box

    def _build_menu(self) -> Gtk.Popover:
        """The overflow menu: whatever did not fit on the card."""
        self.menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.menu_rule = self._rule(soft_class="lintranslator-rule-tight")

        popover = Gtk.Popover()
        popover.add_css_class("lintranslator-menu")
        popover.set_has_arrow(False)
        popover.set_child(self.menu_box)
        return popover

    # Left to right on the card, and the reverse order in which they overflow
    ACTION_ORDER = (
        "region_btn",
        "reread_btn",
        "toggle_btn",
        "copy_btn",
        "settings_btn",
        "quit_btn",
    )

    # Space reserved for the status line, in px; below this it stops being readable
    STATUS_MIN_WIDTH = 150

    def _build_actions(self) -> None:
        """Create the action buttons, in the order they appear on the card."""
        self.region_btn = LabelButton("Select region")
        self.region_btn.set_tooltip_text(
            "Capture the screen again and show the region picker, to move or "
            "resize the box being read"
        )
        self.region_btn.set_visible(False)

        self.reread_btn = LabelButton("Re-read")
        self.reread_btn.set_tooltip_text(
            "Read this box again and translate it from scratch (Ctrl+R, F5, or the "
            "global hotkey — see Settings → Shortcuts)"
        )
        self.toggle_btn = LabelButton("Start")
        self.toggle_btn.set_tooltip_text("Start or pause translating")
        self.copy_btn = LabelButton("Copy")
        self.copy_btn.set_tooltip_text("Copy the translation to the clipboard")
        self.settings_btn = LabelButton("Settings…")
        self.settings_btn.set_tooltip_text("Backend, model, prompt and card size")
        self.quit_btn = LabelButton("Quit")
        self.quit_btn.set_tooltip_text("Shut lintranslator down")

        for button, handler in (
            (self.region_btn, self._on_region),
            (self.reread_btn, self._on_reread),
            (self.toggle_btn, self._on_toggle),
            (self.copy_btn, self._on_copy),
            (self.settings_btn, self._on_settings),
            (self.quit_btn, self._on_quit),
        ):
            button.connect("clicked", handler)

    def _schedule_relayout(self) -> None:
        """Ask for an overflow pass, at most one per main-loop turn."""
        # `getattr`: reachable from `apply_display_settings`, before this exists
        if getattr(self, "_relayout_id", 0):
            return
        self._relayout_id = GLib.idle_add(self._relayout_controls)

    def _relayout_controls(self) -> bool:
        """Keep as many action buttons on the card as its width allows."""
        self._relayout_id = 0
        row = getattr(self, "button_row", None)
        if row is None:
            return False
        buttons = [getattr(self, name) for name in self.ACTION_ORDER]

        # Re-attach first: a widget outside a tree has no CSS and measures as nothing
        for button in buttons:
            self._detach(button)
            self._style_as_row_button(button)
            row.append(button)
        # ⋮ goes back on last: appending the actions after it would put the menu
        # trigger to their left
        self._detach(self.menu_btn)
        row.append(self.menu_btn)
        self._detach(self.menu_rule)

        spacing = row.get_spacing()
        widths = [
            button.measure(Gtk.Orientation.HORIZONTAL, -1).natural
            if button.get_visible()
            else 0
            for button in buttons
        ]
        available = self._control_row_width()
        menu_width = (
            self.menu_btn.measure(Gtk.Orientation.HORIZONTAL, -1).natural + spacing
        )

        def span(sizes) -> int:
            sized = [size for size in sizes if size]
            return sum(sized) + spacing * max(0, len(sized) - 1)

        if span(widths) + self.STATUS_MIN_WIDTH <= available:
            keep = len(buttons)
        else:
            budget = available - self.STATUS_MIN_WIDTH - menu_width
            keep, used = len(buttons), 0
            for index, width in enumerate(widths):
                if not width:
                    continue
                extra = width + (spacing if used else 0)
                if used + extra > budget:
                    keep = index
                    break
                used += extra

        overflow = buttons[keep:]
        for button in overflow:
            self._detach(button)
            self._style_as_menu_item(button)
        for index, button in enumerate(overflow):
            if button is self.quit_btn and index:
                self.menu_box.append(self.menu_rule)
            self.menu_box.append(button)

        self.menu_btn.set_visible(bool(overflow))
        if not overflow and self.menu.get_visible():
            self.menu.popdown()
        return False  # one-shot

    def _control_row_width(self) -> int:
        """How much width the control row has to work with, in pixels."""
        allocated = self.button_row.get_width()
        if allocated > 1:
            return allocated
        return max(1, self.config.display.width - 2 * (theme.SPACE_MD + 2))

    @staticmethod
    def _detach(widget: Gtk.Widget) -> None:
        parent = widget.get_parent()
        if parent is not None:
            parent.remove(widget)

    @staticmethod
    def _style_as_row_button(button: "LabelButton") -> None:
        button.remove_css_class("lintranslator-menu-item")
        button.add_css_class("lintranslator-btn")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(False)
        button.set_label_align(0.5)

    @staticmethod
    def _style_as_menu_item(button: "LabelButton") -> None:
        button.remove_css_class("lintranslator-btn")
        button.add_css_class("lintranslator-menu-item")
        button.set_halign(Gtk.Align.FILL)
        button.set_hexpand(True)
        button.set_label_align(0.0)

    def _close_overflow(self, *_args) -> None:
        """Clicking an item in the overflow menu has to close it."""
        if self.menu.get_visible():
            self.menu.popdown()

    def _on_menu(self, _button: Gtk.Button) -> None:
        """Open the overflow under its button, or close it if it is already up."""
        if self.menu.get_visible():
            self.menu.popdown()
            return
        if self.menu.get_parent() is None:
            # Parented lazily: the popover needs a realized parent to position against
            self.menu.set_parent(self.menu_btn)
        self.menu.popup()

    @staticmethod
    def _rule(soft_class: str = "lintranslator-rule") -> Gtk.Widget:
        """A 1 px separator whose colour comes from the theme."""
        rule = Gtk.Box()
        rule.add_css_class(soft_class)
        return rule

    @staticmethod
    def _text_area(label: Gtk.Label, css_class: str) -> Gtk.ScrolledWindow:
        """A wrapping, selectable text area that scrolls instead of growing."""
        label.add_css_class(css_class)
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_yalign(0)
        label.set_selectable(True)
        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("lintranslator-textarea")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_width(False)
        scroll.set_propagate_natural_height(False)
        scroll.set_vexpand(True)
        scroll.set_child(label)
        return scroll

    def _on_shortcut_reread(self, _widget, _args) -> bool:
        self.request_reread()
        return True

    def enable_region_button(self, callback) -> None:
        """Show the Select region button, wired to whatever restores the picker."""
        self.on_region_request = callback
        self.region_btn.set_visible(True)
        self._schedule_relayout()

    def _on_region(self, _button: Gtk.Button) -> None:
        if self.on_region_request is not None:
            self.on_region_request()

    def _on_reread(self, _button: Gtk.Button) -> None:
        self.request_reread()

    def request_reread(self) -> str:
        """Read the box again now, from whichever button, key or command asked."""
        if self.worker is None:
            self.start_pipeline()
            self.toggle_btn.set_label("Pause")
        self.worker.request_reread()
        if not self._gated:
            self.status_label.remove_css_class("lintranslator-warn")
            self.status_label.set_text("re-reading the box…")
        return "re-reading"

    def _start_control(self) -> None:
        """Listen for `lintranslator reread` (and any other one-line command)."""
        from .control import ControlServer

        if self.control is not None:
            return
        self.control = ControlServer(self._on_control)
        self.control.start()

    def _on_control(self, command: str) -> str:
        verb = command.strip().lower()
        if verb in ("reread", "re-read", "reload"):
            return self.request_reread()
        if verb == "status":
            return self._status_reply()
        return f"unknown command {verb!r}; try: reread, status"

    def _status_reply(self) -> str:
        worker = self.worker
        running = worker is not None and getattr(worker, "alive", True)
        gated = "paused" if self._gated else "reading"
        return (
            f"{'watching' if running else 'stopped'}, {gated}, "
            f"backend {self.config.translate.backend}, "
            f"hotkey {'bound' if (self.hotkey and self.hotkey.bound) else 'not bound'}"
        )

    def _bind_hotkey(self) -> None:
        """Ask the compositor for a global shortcut, once, on first start."""
        if self.hotkey is not None or not self.hotkey_enabled:
            return
        from .hotkey import GlobalHotkey

        try:
            self.hotkey = GlobalHotkey(
                on_activated=self._on_hotkey, on_status=self._on_hotkey_status
            )
            self.hotkey.bind()
        except Exception as exc:  # noqa: BLE001 - a hotkey is never worth a crash
            self.hotkey = None
            self.hotkey_enabled = False
            self._note_idle(f"global hotkey unavailable: {type(exc).__name__}")

    def _on_hotkey(self, shortcut_id: str) -> None:
        if shortcut_id == "reread":
            self.request_reread()

    def _on_hotkey_status(self, ok: bool, detail: str) -> None:
        """Say what happened, without stepping on a translation update."""
        self.hotkey_ok = ok
        if ok:
            self._note_idle(f"global hotkey for Re-read: {detail}")
        else:
            self._show_notice(
                f"No global hotkey — {detail}. Run `lintranslator shortcut` for the setup, "
                "or press Ctrl+R while the card has focus."
            )

    def _note_idle(self, text: str) -> None:
        """Show a message unless the status line is reporting a translation."""
        current = self.status_label.get_text()
        if current.startswith(("press", "starting", "watching", "reading", "re-reading", "stopped")):
            self.status_label.set_text(text)

    @staticmethod
    def _on_first_map(self, *_args) -> None:
        """Ask for keep-above once the window is on screen."""
        GLib.timeout_add(250, self._keep_above_tick)

    def _keep_above_tick(self) -> bool:
        self._apply_keep_above()
        return False  # run once

    def _apply_keep_above(self) -> bool:
        """Ask the window manager to keep this card above other windows."""
        # DISPLAY is set on native Wayland too; checked by class name because
        # Gdk.X11Display does not resolve in every build
        backend = type(self.get_display()).__name__
        if "Wayland" in backend:
            # Native Wayland cannot raise itself and a compositor rule cannot be
            # detected, so nothing is attempted and nothing is said
            return False
        display_name = os.environ.get("DISPLAY")
        if not display_name:
            self._note_keep_above(False, "no DISPLAY")
            return False
        try:
            from Xlib import X, display as xdisplay
            from Xlib import protocol as xprotocol
        except ImportError:
            self._note_keep_above(False, "python-xlib not installed (pip install 'lintranslator[x11]')")
            return False
        try:
            conn = xdisplay.Display(display_name)
            root = conn.screen().root
            window = self._find_x11_window(conn, root)
            if window is None:
                self._note_keep_above(False, "window not found on X11")
                return False
            # EWMH needs a ClientMessage to the root window; setting the property
            # directly is ignored by the WM
            state = conn.intern_atom("_NET_WM_STATE")
            above = conn.intern_atom("_NET_WM_STATE_ABOVE")
            event = xprotocol.event.ClientMessage(
                window=window,
                client_type=state,
                data=(32, [1, above, 0, 0, 0]),  # 1 = _NET_WM_STATE_ADD
            )
            mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
            root.send_event(event, event_mask=mask)
            conn.sync()
            self._note_keep_above(True)
        except Exception as exc:  # noqa: BLE001
            self._note_keep_above(False, f"{type(exc).__name__}")
        return False

    def _find_x11_window(self, conn, root):
        """Locate this panel's X11 window by its title."""
        wanted = self.get_title()

        def walk(w):
            try:
                children = w.query_tree().children
            except Exception:  # noqa: BLE001
                return None
            for child in children:
                try:
                    if child.get_wm_name() == wanted:
                        return child
                except Exception:  # noqa: BLE001
                    pass
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(root)

    def _note_keep_above(self, ok: bool, detail: str = "") -> None:
        self._keep_above_ok = ok
        if ok:
            return
        self._show_notice(
            KEEP_ABOVE_SHORT + (f" [{detail}]" if detail else "")
        )

    @staticmethod
    def _drag_handle(widget: Gtk.Widget) -> Gtk.Widget:
        """Wrap the header strip so dragging it moves the window."""
        handle = Gtk.WindowHandle()
        handle.set_child(widget)
        return handle

    # GTK4 removed gtk_window_begin_resize_drag and exposes no Wayland input
    # serial, so the resize drag is handled here
    RESIZE_EDGES = (
        # name, halign, valign, cursor, (width, height) request
        ("e", Gtk.Align.END, Gtk.Align.FILL, "e-resize", (6, -1)),
        ("s", Gtk.Align.FILL, Gtk.Align.END, "s-resize", (-1, 6)),
        # Corners last: in a Gtk.Overlay the later child gets the event first
        ("se", Gtk.Align.END, Gtk.Align.END, "se-resize", (14, 14)),
    )

    def _with_resize_grips(self, card: Gtk.Widget) -> Gtk.Widget:
        """Wrap the card in an overlay carrying the resize grips."""
        from gi.repository import Gdk

        overlay = Gtk.Overlay()
        overlay.set_child(card)
        for name, halign, valign, cursor_name, request in self.RESIZE_EDGES:
            grip = Gtk.Box()
            grip.add_css_class("lintranslator-grip-area")
            grip.set_halign(halign)
            grip.set_valign(valign)
            grip.set_size_request(*request)
            cursor = Gdk.Cursor.new_from_name(cursor_name, None)
            if cursor is not None:
                grip.set_cursor(cursor)
            drag = Gtk.GestureDrag()
            drag.set_button(Gdk.BUTTON_PRIMARY)
            drag.connect("drag-begin", self._on_resize_begin)
            drag.connect("drag-update", self._on_resize_update, name)
            drag.connect("drag-end", self._on_resize_end, name)
            grip.add_controller(drag)
            overlay.add_overlay(grip)
        return overlay

    def _on_resize_begin(self, _gesture, _x: float, _y: float) -> None:
        """Remember the size the drag started from."""
        self._resize_from = (self.get_width(), self.get_height())

    def _on_resize_update(self, _gesture, dx: float, dy: float, edge: str) -> None:
        width, height = self._resize_from
        if "e" in edge:
            width = int(width + dx)
        if "s" in edge:
            height = int(height + dy)
        self._set_card_size(width, height)

    def _on_resize_end(self, _gesture, _dx: float, _dy: float, edge: str) -> None:
        """Keep the size the user chose, and write it down."""
        width, height = self._current_size()
        self.config.display.width = width
        # Only the axis that was dragged is pinned.
        if "s" in edge:
            self.config.display.height = height
        self._save_size()

    def _current_size(self) -> tuple[int, int]:
        """The size the card is at, or the one it was last asked to be."""
        width, height = self._requested_size
        if self.get_width() > 1:
            width = self.get_width()
        if self.get_height() > 1:
            height = self.get_height()
        return width, height

    def _set_card_size(self, width: int, height: int) -> None:
        """Resize the card, never below what its own contents need."""
        floor_w, floor_h = self._card_minimum()
        width = max(int(width), floor_w)
        height = max(int(height), floor_h)
        self.set_default_size(width, height)
        self.config.display.width = width
        self._requested_size = (width, height)

    # Floor under the card's size, in px: the buttons may measure 0 wide, so the
    # content minimum alone would let the card be dragged down to a sliver
    MIN_CARD = (260, 150)

    def _card_minimum(self) -> tuple[int, int]:
        """The smallest the card can be and still show its own controls."""
        floor_w, floor_h = self.MIN_CARD
        child = getattr(self, "card", None)
        if child is None:
            return (floor_w, floor_h)
        min_w, _, _, _ = child.measure(Gtk.Orientation.HORIZONTAL, -1)
        _, min_h, _, _ = child.measure(Gtk.Orientation.VERTICAL, -1)
        return (max(min_w, floor_w), max(min_h, floor_h))

    def _restore_size(self) -> None:
        """Open at the size the card was last dragged to, if it ever was."""
        display_cfg = self.config.display
        # -1 height means "whatever the line budgets add up to".
        self.set_default_size(display_cfg.width, display_cfg.height or -1)

    def _save_size(self) -> None:
        try:
            self.config.save()
        except OSError:
            pass

    def _try_move(self, position: tuple[int, int]) -> None:
        """Absolute placement - only honoured under X11/XWayland."""
        surface = self.get_surface()
        if surface is None:
            return
        from gi.repository import Gdk

        if isinstance(self.get_display(), Gdk.WaylandDisplay):
            self.status_label.set_text("watching · drag this panel where you want it")
            return
        surface.move(*position) if hasattr(surface, "move") else None

    def start_pipeline(self) -> None:
        self.worker = PipelineThread(self.config, self.outbox, gate=self.gate)
        self.worker.start()
        self._bind_hotkey()

    def _refresh_backend_label(self) -> None:
        """Name the backend, the pair and the region being read."""
        t = self.config.translate
        r = self.config.capture.region
        size = ""
        if r.mode == "fraction":
            size = f" · box {r.w:.3f}×{r.h:.3f}"
        pair = f"{short_code(t.source_lang)}→{short_code(t.target_lang)}"
        if t.backend == "none":
            # The pass-through backend returns the OCR text unchanged, so name it
            # and leave the model out
            self.backend_label.set_text(
                f"none (pass-through, no translation) · {pair}{size}"
            )
            return
        model = t.model or "(no model set)"
        if len(model) > 34:
            model = model[:31] + "…"
        self.backend_label.set_text(f"{t.backend} · {model} · {pair}{size}")

    def _on_settings(self, _button: Gtk.Button) -> None:
        self.open_settings()

    def _on_copy(self, _button: Gtk.Button) -> None:
        if not self.last_event:
            return
        clipboard = self.get_clipboard()
        clipboard.set(self.last_event.target)
        self.status_label.set_text("copied translation to clipboard")

    def _on_toggle(self, button: Gtk.Button) -> None:
        if self.worker is None:
            self.start_pipeline()
            button.set_label("Pause")
            self.status_label.set_text("starting…")
            return
        self.worker.stop()
        self.worker = None
        button.set_label("Start")
        self.status_label.set_text("paused — press Start to resume")

    def show_idle(self) -> None:
        """Say that the card is up but nothing is reading yet."""
        self.toggle_btn.set_label("Start")
        self.status_label.set_text("press Start to begin translating")

    def _on_close_request(self, _window) -> bool:
        self.close_pipeline()
        return False  # let the window close

    def _on_quit(self, _button: Gtk.Button) -> None:
        """Shut everything down, explicitly."""
        self.close_pipeline()
        application = self.get_application()
        if application is not None:
            for window in list(application.get_windows()):
                # Destroy rather than close: a close handler could re-show a window
                # that is on its way out
                window.destroy()
            application.quit()

    def _drain(self) -> bool:
        self._check_worker()
        drained = 0
        while drained < 20:
            try:
                message = self.outbox.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if message.kind == "event" and message.event:
                self._show_event(message.event)
            elif message.kind == "error":
                self._show_error(message.text)
            elif message.kind == "gate":
                self._show_gate(message.text)
            elif message.kind == "self-read":
                self._show_self_read(message.text)
            elif message.kind == "note":
                self.status_label.set_text(message.text)
            elif message.kind == "status":
                self.status_label.set_text(message.text)
        return True

    def _check_worker(self) -> None:
        """Say it when the pipeline has stopped, instead of claiming to watch."""
        worker = self.worker
        if worker is None or getattr(worker, "alive", True):
            return
        self.worker = None
        self.toggle_btn.set_label("Start")
        detail = getattr(worker, "error", None) or "the worker thread exited"
        self.status_label.add_css_class("lintranslator-warn")
        self.status_label.set_text(f"translation stopped — {detail}. Press Start.")

    def _show_gate(self, reason: str) -> None:
        """Reading is paused because one of our own windows is on screen."""
        self._gated = bool(reason)
        if reason:
            self.status_label.add_css_class("lintranslator-warn")
            self.status_label.set_text(f"paused — {reason}")
        else:
            self.status_label.remove_css_class("lintranslator-warn")
            self.status_label.set_text("reading the box again")

    def _show_self_read(self, text: str) -> None:
        snippet = " ".join(text.split())[:32]
        self.status_label.set_text(
            f"skipped {snippet!r} — lintranslator's own window is in the box"
        )

    def _show_event(self, event: Event) -> None:
        self.last_event = event
        self.target_label.remove_css_class("lintranslator-notice")
        self.target_label.remove_css_class("lintranslator-notice-error")
        self.target_label.set_text(event.target)
        # `display_source` keeps the original line breaks; fall back to `source`
        self.source_label.set_text(getattr(event, "display_source", "") or event.source)
        self._show_error("")
        self.status_label.set_tooltip_text("")

        when = time.strftime("%H:%M:%S", time.localtime(event.at))
        speed = "cached" if event.cached else f"{event.translate_elapsed * 1000:.0f} ms"
        # `total_elapsed` is measured from the capture to now, in seconds
        self.status_label.set_text(
            f"{when} · conf {event.confidence:.0f} · {speed} · +{event.total_elapsed:.1f}s"
        )

    def _show_notice(self, text: str, error: bool = False) -> None:
        """Explain a setup problem where the translation goes."""
        self.status_label.set_tooltip_text(text)
        if self.last_event is not None:
            self.status_label.set_text(text.split(" — ")[0])
            return
        level = "lintranslator-notice-error" if error else "lintranslator-notice"
        other = "lintranslator-notice" if error else "lintranslator-notice-error"
        self.target_label.remove_css_class(other)
        self.target_label.add_css_class(level)
        self.target_label.set_text(text)

    def _show_error(self, text: str) -> None:
        """Show an error in the status line's slot, never as an extra line."""
        self.error_label.set_text(text)
        self.error_label.set_visible(bool(text))
        self.error_label.set_tooltip_text(text or None)
        self.status_label.set_visible(not text)

    def close_pipeline(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        if self.hotkey is not None:
            self.hotkey.close()
            self.hotkey = None
        if self.control is not None:
            self.control.stop()
            self.control = None
