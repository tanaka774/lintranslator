"""The always-on-top translation panel.

Design constraints that shaped this:

* **Wayland forbids client-side window positioning.** A client cannot say "put me
  at (400, 900)". Absolute placement needs either the layer-shell protocol or
  X11. Layer-shell would need `gtk4-layer-shell`, which is not installed here, so
  the panel is a normal keep-above window that anchors itself as close to the
  target as the session allows and can be dragged. `--position x,y` works when
  running under XWayland (`GDK_BACKEND=x11`).
* **GTK is not thread-safe.** The capture/OCR/translate pipeline runs in a worker
  thread and communicates through a queue drained on the GTK main thread.

The panel shows the original text below the translation, because a translation
panel that covers the text you are trying to read is worse than no panel.

The card's *size* is fixed while its text is not. It floats over a game, so a
card that grew with its text would creep further over the dialogue box it is
reporting on - and GTK never shrinks a resizable window back, so a single long
reply would raise the card's floor for the rest of the session. Each text area
reserves a configured number of lines and scrolls beyond them.

All styling comes from `lintranslator/theme.py`, shared with the picker and the settings
dialog, which used to be stock Adwaita next to a custom card.
"""
from __future__ import annotations

import os
import queue
import threading
import time
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


class LabelButton(Gtk.Button):
    """A button whose label can be aligned, and that still answers set_label().

    These buttons move between the control row, where a centred label is right,
    and the overflow menu, where a centred row among full-width rows reads as
    another button rather than as a menu item. `Gtk.Button`'s own label is always
    centred and there is no API to change that, so this one owns a label child
    instead - and keeps `set_label`/`get_label` working, because the picker and
    the tests drive the toggle button by label and should not have to know which
    kind of button this is.
    """

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
        # Why capturing must wait right now (one of our own windows is on
        # screen), or None. Supplied by the window that owns this panel.
        self.gate = gate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pipeline: Pipeline | None = None
        self.ready = threading.Event()
        self.warmup_error: Exception | None = None
        self._pending_region = None
        self._pending_reread = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lintranslator-pipeline", daemon=True)
        self._thread.start()

    def request_region(self, region) -> None:
        """Read a different area, without rebuilding the pipeline.

        Restarting instead would reload the model (seconds, for a local one) and
        recreate the panel window, which on Wayland reappears wherever the
        compositor likes - so the card would jump every time the box moved.
        """
        self._pending_region = region
        pipe = self.pipeline
        if pipe is not None:
            pipe.request_region(region)

    def request_reread(self) -> None:
        """Read the box again now and translate it, cache and dedupe aside.

        Before the pipeline exists the request is held and applied as soon as it
        does, so pressing Re-read on a paused panel just works.
        """
        self._pending_reread = True
        pipe = self.pipeline
        if pipe is not None:
            pipe.request_reread()

    def _run(self) -> None:
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
            self.outbox.put(UiMessage("error", text=f"startup failed: {exc}"))
            self.ready.set()
            return
        self.ready.set()
        self.outbox.put(UiMessage("status", text="watching the region"))
        # Anything the config had to say when it was loaded - an unknown key, a
        # file whose permissions had to be tightened. They were collected but
        # never shown anywhere, which made them the same as not being detected.
        # Posted after the status line so the message survives.
        for warning in list(getattr(self.config, "warnings", None) or []):
            self.outbox.put(UiMessage("note", text=warning))

        pipe.start()
        try:
            while not self._stop.is_set():
                pipe.step()
                # Sleep in short slices so stop() is responsive.
                self._stop.wait(min(pipe.sleep_time(), 0.1))
        finally:
            report = pipe.report()
            pipe.close()
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

    def _on_event(self, event: Event) -> None:
        self.outbox.put(UiMessage("event", event=event))

    def _on_gate(self, reason: str) -> None:
        self.outbox.put(UiMessage("gate", text=reason))

    def _on_self_read(self, text: str) -> None:
        self.outbox.put(UiMessage("self-read", text=text))

    def _on_note(self, message: str) -> None:
        self.outbox.put(UiMessage("note", text=message))

    def stop(self, timeout: float = 1.0) -> None:
        """Signal the loop to end and wait briefly for it to unwind.

        The wait is short on purpose: a step is ~0.3 s, so a longer join just
        freezes the UI and delays quitting for no benefit. The thread is a daemon,
        so if it is mid-translation it cannot hold the process open.
        """
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
        # Why capturing must wait right now, or None. Read on the worker thread -
        # it is a plain callable into a lock-protected registry, never a GTK call.
        # Defaults to the process-wide guard so a standalone panel (`lintranslator gui
        # --panel`) also stops reading while its settings window is on screen.
        self.gate = GUARD.reason
        # Set by the region picker: brings the picker back so the box can be
        # adjusted. The Region button is hidden when there is no picker.
        self.on_region_request = None
        self._gated = False
        # `lintranslator reread` and friends; started here so the socket exists for the
        # whole life of the window.
        self.control = None
        # The compositor-granted global hotkey, bound on first start.
        self.hotkey = None
        self.hotkey_enabled = True
        self._start_control()

        self._css_provider = None
        # Width the overflow pass last laid out for, and the pending idle that
        # will do it - see `do_size_allocate`.
        self._layout_width = -1
        self._relayout_id = 0
        # The size last asked for, used until the window has been allocated.
        self._requested_size = (config.display.width, config.display.height)
        self.apply_display_settings()
        self.set_decorated(False)
        self.set_resizable(True)
        self._restore_size()
        self.add_css_class("lintranslator-panel")
        self.card = self._build_body()
        self.set_child(self._with_resize_grips(self.card))
        # Both need the widget tree, so they run after `_build_body`, not with
        # the stylesheet install above.
        self._apply_layout_budget()
        self._relayout_controls()

        if position:
            # Works under X11/XWayland; a no-op on native Wayland by design.
            self.connect("realize", lambda *_: self._try_move(position))

        # Stop the pipeline when this window goes away, whatever closed it. The
        # application's shutdown hook covers a normal quit, but relying on that
        # alone means a leaked worker thread can keep capturing if the window is
        # closed some other way.
        self.connect("close-request", self._on_close_request)

        if demo_event is not None:
            # Lets the layout be verified (and screenshotted) without running a
            # model: `lintranslator gui --demo`.
            self._show_event(demo_event)

        self._refresh_backend_label()
        if config.display.keep_above:
            # After the surface exists, ask the WM to raise the card.
            self.connect("map", self._on_first_map)

        # Drain worker messages on the GTK main thread.
        GLib.timeout_add(80, self._drain)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        """Re-split the control row whenever the width changes.

        A vfunc is the only hook GTK4 offers here: the `size-allocate` signal was
        removed, and `Gtk.Widget` has no width property to watch. The work itself
        is deferred to an idle by `_schedule_relayout`, because this runs in the
        middle of allocating the tree that the overflow pass rearranges.
        """
        Gtk.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        if width != self._layout_width:
            self._layout_width = width
            self._schedule_relayout()

    # -- construction ------------------------------------------------------ #
    def apply_display_settings(self) -> None:
        """(Re)build the stylesheet and re-pin the card's size.

        Font size and source visibility are CSS-driven so they can change live
        without rebuilding the widget tree. Adding a provider at the same
        priority replaces the previous one, so the old stylesheet does not
        linger and the picker and settings dialog pick the new one up too.
        """
        self._css_provider = theme.install_for(self.config)
        self._restore_size()
        self._apply_layout_budget()
        # Font size feeds the buttons' widths, so the split has to be redone.
        self._schedule_relayout()

    def _apply_layout_budget(self) -> None:
        """Pin each text area to the number of lines the config reserves.

        This is what makes the card's height independent of its content, and so
        what stops it from ratcheting taller on a long line and never coming
        back. Called after the tree exists and again on every apply.
        """
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
        """Height of `lines` lines of `label`'s font, measured not assumed.

        Measured because the font size comes from `base_font_size * font_scale`
        through CSS: a hardcoded line height would silently clip the text at
        large font sizes and waste space at small ones.

        The label is forced visible for the measurement, and its text is put
        back afterwards. A hidden widget never resolves its CSS, so it measures
        0 px - which is how the source area first came out reserved as 1 px and
        clipped its own text to nothing. Forgetting to restore the text meant
        that applying Settings while a translation was on screen wiped it.

        `n` lines are `one + (n - 1) * line`, not `line * n`: the difference
        between one and two lines is the *leading*, and the first line's height
        also carries the font's ascent and descent. Reserving `line * n` came up
        one pixel short and sliced the bottom off the last line.
        """
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
        """The card: a header strip, the translation, the original, one control row.

        Five controls used to sit in two right-aligned rows that cost 62 px of a
        172 px card and did not even line up (the rows measured 133 px and
        152 px wide, so the left edge stepped in by 19 px). Only two of them -
        Re-read and Pause - are pressed while playing, so those two stay on the
        card and Region, Copy, Settings and Quit move one click away.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("lintranslator-card")

        # -- header: the drag handle, and what is being read ---------------
        # Doubles as the drag hint, since there is no title bar to signal it.
        # Names the region too, so the picker can be minimised without losing
        # track of what is being captured.
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
        # Only the header is a drag handle. Wrapping the whole card meant a drag
        # over the translation was a window move rather than a text selection.
        box.append(self._drag_handle(header))
        box.append(self._rule())

        # -- the translation, with the original text below it ---------------
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

        # -- one control row: status on the left, the actions, then ⋮ --------
        #
        # Which actions are in this row is not fixed: it depends on how wide the
        # card is. `_relayout_controls` keeps as many as fit, in `ACTION_ORDER`,
        # and moves the rest into the overflow menu. Only the status line and
        # the ⋮ button belong here permanently.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.button_row = row

        self.status_label = Gtk.Label(label="starting…", xalign=0)
        self.status_label.add_css_class("lintranslator-status")
        # Ellipsised rather than wrapped: this row is a fixed height, and a
        # wrapped status was what made warnings add two lines to the card.
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_hexpand(True)
        row.append(self.status_label)

        # An error takes the status line's place rather than adding a line, so
        # the card's height does not depend on whether something went wrong.
        self.error_label = Gtk.Label(label="", xalign=0)
        self.error_label.add_css_class("lintranslator-error")
        self.error_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.error_label.set_hexpand(True)
        self.error_label.set_visible(False)
        row.append(self.error_label)

        self._build_actions()
        self.menu = self._build_menu()
        # A plain button, not a Gtk.MenuButton: MenuButton's CSS node is
        # `menubutton`, so `button.lintranslator-btn` does not match it and it kept the
        # desktop theme's light background - a white square in the control row.
        self.menu_btn = Gtk.Button(label="⋮")
        self.menu_btn.set_tooltip_text("Everything that did not fit on the card")
        self.menu_btn.add_css_class("lintranslator-btn")
        self.menu_btn.add_css_class("lintranslator-icon")
        # Hidden until something actually overflows: an empty menu is a lie.
        self.menu_btn.set_visible(False)
        self.menu_btn.connect("clicked", self._on_menu)
        row.append(self.menu_btn)
        for name in self.ACTION_ORDER:
            # Choosing something from the menu has to put the menu away, and the
            # handler is connected here so it runs after the action itself.
            getattr(self, name).connect("clicked", self._close_overflow)
        box.append(row)

        # In-window shortcuts. These work while this window has focus; the global
        # one (any window) comes from the compositor via `lintranslator/hotkey.py`.
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
        """The overflow menu: whatever did not fit on the card.

        Left empty here on purpose. The buttons in it are the very same widgets
        that sit on the card, moved in and out by `_relayout_controls` - so an
        action is never in two places, and never missing from both.
        """
        self.menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.menu_rule = self._rule(soft_class="lintranslator-rule-tight")

        popover = Gtk.Popover()
        popover.add_css_class("lintranslator-menu")
        popover.set_has_arrow(False)
        popover.set_child(self.menu_box)
        return popover

    # Left to right on the card, and - reading it backwards - the order they
    # overflow in. The first entry is the last to leave the card: Region comes
    # first because it is the way back to the picker, and once the panel has the
    # box there is no other way to reach it.
    ACTION_ORDER = (
        "region_btn",
        "reread_btn",
        "toggle_btn",
        "copy_btn",
        "settings_btn",
        "quit_btn",
    )

    # Space kept for the status line before any button may claim it: roughly what
    # "12:04:31 · conf 94 · 1690 ms · +1.9s" needs. Below this the status stops
    # being readable, and the card would rather push a button into the menu.
    STATUS_MIN_WIDTH = 150

    def _build_actions(self) -> None:
        """Create the action buttons, in the order they appear on the card."""
        self.region_btn = LabelButton("Region…")
        self.region_btn.set_tooltip_text(
            "Capture the screen again and show the region picker, to move or "
            "resize the box being read"
        )
        # Only useful when a picker owns this panel, so it starts hidden and
        # `enable_region_button` reveals it.
        self.region_btn.set_visible(False)

        self.reread_btn = LabelButton("Re-read")
        self.reread_btn.set_tooltip_text(
            "Read this box again and translate it from scratch (Ctrl+R, F5, or the "
            "global hotkey — see Settings → Shortcuts)"
        )
        # Starts as "Start" because the panel can be opened without running the
        # pipeline, so the card can be positioned before translation begins.
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

    # -- which buttons fit on the card ------------------------------------- #
    def _schedule_relayout(self) -> None:
        """Ask for an overflow pass, at most one per main-loop turn."""
        # `getattr` because this is reachable from `apply_display_settings`,
        # which runs before the window's own state is set up.
        if getattr(self, "_relayout_id", 0):
            return
        self._relayout_id = GLib.idle_add(self._relayout_controls)

    def _relayout_controls(self) -> bool:
        """Keep as many action buttons on the card as its width allows.

        The rest move into the overflow menu, in the same order, so the row and
        the menu always hold the same set of buttons and only the split point
        moves. Region is first on the card, and therefore last to leave it.

        Runs from an idle callback rather than during allocation, because it
        rearranges the very tree that is being allocated.
        """
        self._relayout_id = 0
        row = getattr(self, "button_row", None)
        if row is None:
            return False
        buttons = [getattr(self, name) for name in self.ACTION_ORDER]

        # Everything back onto the card first, so each button can be measured
        # with its row styling resolved. A widget that is not in a tree has no
        # CSS, and would measure as nothing at all.
        for button in buttons:
            self._detach(button)
            self._style_as_row_button(button)
            row.append(button)
        # The ⋮ button goes back on last, so it stays at the end of the row.
        # Appending the actions after it - which is what a naive rebuild does -
        # puts the menu trigger to the *left* of the buttons that stayed.
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
            # Everything fits, so nothing overflows and there is no ⋮ button
            # taking up room either.
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
                # Quit is destructive; keep it apart from the rest.
                self.menu_box.append(self.menu_rule)
            self.menu_box.append(button)

        self.menu_btn.set_visible(bool(overflow))
        if not overflow and self.menu.get_visible():
            self.menu.popdown()
        return False  # one-shot

    def _control_row_width(self) -> int:
        """How much width the control row has to work with, in pixels.

        The row's own allocation, which is the only thing that is actually true
        about the width the card has. The configured width is a fallback for
        before the first allocation and nothing more: it goes stale the moment
        anything else sizes the window - a compositor rule, or a test that
        resizes directly - and a split decided from it then keeps buttons off a
        card that has room for them.
        """
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
            # Parented lazily: the popover needs a realized parent to position
            # itself against, and this is the first moment one exists.
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
        """A wrapping, selectable text area that scrolls instead of growing.

        `propagate_natural_width` is off so the label's unwrapped width - 1639 px
        for a verbose reply - never reaches the window, and the height request
        set in `_apply_layout_budget` is what reserves the space.
        """
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
        """Show the Region button, wired to whatever restores the picker."""
        self.on_region_request = callback
        self.region_btn.set_visible(True)
        # A button appearing changes what fits, and Region outranks the others.
        self._schedule_relayout()

    def _on_region(self, _button: Gtk.Button) -> None:
        if self.on_region_request is not None:
            self.on_region_request()

    def _on_reread(self, _button: Gtk.Button) -> None:
        self.request_reread()

    def request_reread(self) -> str:
        """Read the box again now, from whichever button, key or command asked.

        Returns the one-line reply the control socket hands back to `lintranslator reread`,
        so a script gets something meaningful instead of silence.
        """
        if self.worker is None:
            # Re-read is also the obvious way to un-pause: do what it says.
            self.start_pipeline()
            self.toggle_btn.set_label("Pause")
        self.worker.request_reread()
        if not self._gated:
            self.status_label.remove_css_class("lintranslator-warn")
            self.status_label.set_text("re-reading the box…")
        return "re-reading"

    # -- control socket and global hotkey ---------------------------------- #
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
        running = self.worker is not None
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
        """Ask the window manager to keep this card above other windows.

        GTK4 removed `set_keep_above`, and native Wayland has no protocol for a
        client to raise itself - the compositor owns stacking. Under XWayland the
        standard `_NET_WM_STATE_ABOVE` hint still works, and KWin honours it
        there, so that is what this uses.

        On native Wayland it cannot work; the status line says so and points at
        the KWin window rule instead. Returns False so the timeout does not
        repeat.
        """
        # Only possible if this window is itself an X11 window. DISPLAY being set
        # is not enough: it is present on a native Wayland session too, where no
        # X11 window for the panel exists and the hint would go nowhere.
        # Checked by class name because the X11 backend typelib is not always
        # present (Gdk.X11Display does not even resolve in some builds).
        backend = type(self.get_display()).__name__
        if "Wayland" in backend:
            # Native Wayland gives a client no way to raise itself: stacking is
            # the compositor's, and GTK4 removed the keep-above API. The card may
            # still be kept above by a compositor window rule, but that cannot be
            # detected from here, so no claim is made either way.
            self._note_keep_above(False, "native Wayland")
            return False
        display_name = os.environ.get("DISPLAY")
        if not display_name:
            self._note_keep_above(False, "no DISPLAY")
            return False
        try:
            from Xlib import X, display as xdisplay
            from Xlib import protocol as xprotocol
        except ImportError:
            # Name the install, not just the module: this is the XWayland path for
            # always-on-top, and "not installed" alone leaves the user guessing
            # which package that is.
            self._note_keep_above(False, "python-xlib not installed (pip install 'lintranslator[x11]')")
            return False
        try:
            conn = xdisplay.Display(display_name)
            root = conn.screen().root
            window = self._find_x11_window(conn, root)
            if window is None:
                self._note_keep_above(False, "window not found on X11")
                return False
            # EWMH requires a ClientMessage _NET_WM_STATE request to the root
            # window; setting the property directly is ignored by the WM.
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
        # A window rule is the fix, and saying so is the whole point: an
        # unexplained card that sinks behind the game reads as broken.
        self._show_notice(
            "Not always-on-top. On Wayland add a KWin window rule for 'LinTranslator', "
            "or launch with GDK_BACKEND=x11 (see README)."
            + (f" [{detail}]" if detail else "")
        )

    @staticmethod
    def _drag_handle(widget: Gtk.Widget) -> Gtk.Widget:
        """Wrap the header strip so dragging it moves the window.

        The window is frameless, so there is no title bar. `Gtk.WindowHandle`
        asks the compositor to move the window on a drag, which is the supported
        approach on Wayland, where an application cannot position its own
        windows. Buttons still receive their clicks: a click is not a drag.

        Only the header is wrapped. When the whole card was the handle, a drag
        that started on the translation moved the window instead of selecting
        the text - and the translation is selectable precisely so it can be
        copied out.
        """
        handle = Gtk.WindowHandle()
        handle.set_child(widget)
        return handle

    # -- resizing ---------------------------------------------------------- #
    # The card is frameless, so there are no window-manager resize handles to
    # grab, and `gtk_window_begin_resize_drag` does not exist in GTK4 at all -
    # it was removed, and the replacement `Gdk.Toplevel.begin_resize()` needs the
    # Wayland input *serial*, which GTK4 exposes nowhere. So the drag is handled
    # here and the size is applied with `set_default_size`, which does resize an
    # already-mapped window (measured: 555x207 -> 700x340 on a mapped panel).
    #
    # Only the east, south and south-east edges are offered, on purpose. On
    # Wayland a client cannot move its own window, so a drag on the north or west
    # edge could not keep the opposite edge still: the card would grow rightward
    # while the pointer moved left. Offering those edges would be worse than not
    # offering them.
    RESIZE_EDGES = (
        # name, halign, valign, cursor, (width, height) request
        ("e", Gtk.Align.END, Gtk.Align.FILL, "e-resize", (6, -1)),
        ("s", Gtk.Align.FILL, Gtk.Align.END, "s-resize", (-1, 6)),
        # Corners last: in a Gtk.Overlay the later child is on top and gets the
        # event first, so the corner wins where it overlaps the two edges.
        ("se", Gtk.Align.END, Gtk.Align.END, "se-resize", (14, 14)),
    )

    def _with_resize_grips(self, card: Gtk.Widget) -> Gtk.Widget:
        """Wrap the card in an overlay carrying the resize grips.

        An overlay adds nothing to the size of its main child, so the grips
        cannot make the card bigger - the property `test_the_panel_height_...`
        pins.
        """
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
        """Remember the size the drag started from.

        Offsets are relative to the drag's start, so adding them to the starting
        size is what keeps the edge under the pointer instead of drifting.
        """
        self._resize_from = (self.get_width(), self.get_height())

    def _on_resize_update(self, _gesture, dx: float, dy: float, edge: str) -> None:
        width, height = self._resize_from
        if "e" in edge:
            width = int(width + dx)
        if "s" in edge:
            height = int(height + dy)
        self._set_card_size(width, height)

    def _on_resize_end(self, _gesture, _dx: float, _dy: float, edge: str) -> None:
        """Keep the size the user chose, and write it down.

        Saved at the end of the drag rather than on every motion event: one
        write per resize instead of one per pixel, and the size survives even if
        the panel is killed rather than closed.
        """
        width, height = self._current_size()
        self.config.display.width = width
        # Only the axis that was dragged is pinned.
        if "s" in edge:
            self.config.display.height = height
        self._save_size()

    def _current_size(self) -> tuple[int, int]:
        """The size the card is at, or the one it was last asked to be.

        The fallback matters before the window is mapped - and in tests, where
        it never is - because an unallocated widget reports a width of 0, which
        must never be written into the config.
        """
        width, height = self._requested_size
        if self.get_width() > 1:
            width = self.get_width()
        if self.get_height() > 1:
            height = self.get_height()
        return width, height

    def _set_card_size(self, width: int, height: int) -> None:
        """Resize the card, never below what its own contents need.

        The width is recorded as it changes, because the overflow split is
        decided from it and has to be current mid-drag. The height is deliberately
        *not* recorded here: it is pinned only by a drag that actually asked for a
        height, in `_on_resize_end`. Recording it on every update meant a sideways
        drag froze the card at whatever the line budgets happened to produce, and
        the budget sliders then did nothing without the user ever asking for that.
        """
        floor_w, floor_h = self._card_minimum()
        width = max(int(width), floor_w)
        height = max(int(height), floor_h)
        self.set_default_size(width, height)
        self.config.display.width = width
        self._requested_size = (width, height)

    # A floor under the card's size, independent of its contents. The action
    # buttons are allowed to measure 0 wide (that is what lets them overflow into
    # the menu at all), so the content minimum alone would let the card be
    # dragged down to a sliver.
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
            # A config that cannot be written is not a reason to break a resize;
            # the size simply will not survive the session.
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

    # -- controls ---------------------------------------------------------- #
    def start_pipeline(self) -> None:
        self.worker = PipelineThread(self.config, self.outbox, gate=self.gate)
        self.worker.start()
        self._bind_hotkey()

    def _refresh_backend_label(self) -> None:
        """Name the backend, the pair and the region being read.

        The region is shown so the picker can be minimised without losing track
        of what is being captured - the picker is the only other place it appears,
        and leaving that large window open can make it capture itself.

        The language pair belongs here for the same reason: it is set in a dialog
        that is then closed, and "this is translating into the wrong language" is
        something you want to see while it is happening rather than afterwards.
        """
        t = self.config.translate
        r = self.config.capture.region
        size = ""
        if r.mode == "fraction":
            size = f" · box {r.w:.3f}×{r.h:.3f}"
        pair = f"{short_code(t.source_lang)}→{short_code(t.target_lang)}"
        if t.backend == "none":
            # The pass-through backend returns the OCR text unchanged, so the
            # card shows the source where a translation belongs. Nothing is
            # broken, but it reads as a translator that failed, so name the
            # backend and leave the model out: naming a model that is doing no
            # work is what makes "none" plus "gemini-2.5-flash-lite" look like a
            # working setup that is echoing.
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
        """Present the card without running the pipeline.

        Used so the panel is on screen and can be dragged into place before
        translation starts, rather than appearing mid-session already working.
        """
        self.toggle_btn.set_label("Start")
        self.status_label.set_text("press Start to begin translating")

    def _on_close_request(self, _window) -> bool:
        self.close_pipeline()
        return False  # let the window close

    def _on_quit(self, _button: Gtk.Button) -> None:
        """Shut everything down, explicitly.

        `application.quit()` alone only *requests* a quit, and with more than one
        window it can be swallowed - pressing Quit appeared to do nothing and
        needed a second press. Closing each window outright is deterministic.
        """
        self.close_pipeline()
        application = self.get_application()
        if application is not None:
            for window in list(application.get_windows()):
                # Destroy rather than close: the picker's close handler would
                # otherwise re-show a window that is on its way out.
                window.destroy()
            application.quit()

    # -- worker -> UI ------------------------------------------------------ #
    def _drain(self) -> bool:
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
                # Something the user asked to be told about (a re-read that found
                # nothing, one queued while paused).
                self.status_label.set_text(message.text)
            elif message.kind == "status":
                self.status_label.set_text(message.text)
        return True

    def _show_gate(self, reason: str) -> None:
        """Reading is paused because one of our own windows is on screen.

        Said plainly, in a colour that does not look like ordinary status text,
        with the action to take: an unexplained pause is indistinguishable from a
        broken translator, which is the exact failure this whole change is about.
        """
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
        # `display_source` keeps the original line breaks so the panel does not
        # show one long wrapped run where the game showed two lines.
        self.source_label.set_text(getattr(event, "display_source", "") or event.source)
        self._show_error("")
        self.status_label.set_tooltip_text("")

        when = time.strftime("%H:%M:%S", time.localtime(event.at))
        speed = "cached" if event.cached else f"{event.translate_elapsed * 1000:.0f} ms"
        # `total_elapsed` is measured from the capture to this moment, so the age
        # shown is the one the user waited through - settling included. Kept short
        # on purpose: this row is shared with the two buttons, and the backend is
        # already named in the header, so it is not repeated here.
        self.status_label.set_text(
            f"{when} · conf {event.confidence:.0f} · {speed} · +{event.total_elapsed:.1f}s"
        )

    def _show_notice(self, text: str, error: bool = False) -> None:
        """Explain a setup problem where the translation goes.

        These are the long ones - "not always-on-top", "no global hotkey" - that
        run to two or three lines. The status row is a single ellipsised line, so
        text that long would be cut to a few characters there. The translation
        area has three reserved lines and is empty until the first line is
        translated, which is exactly when these appear; the first translation
        displaces the notice, and its text stays in the tooltip.
        """
        self.status_label.set_tooltip_text(text)
        if self.last_event is not None:
            # Something is already translated and being read: keep it, and put a
            # short form in the status row rather than overwriting the text.
            self.status_label.set_text(text.split(" — ")[0])
            return
        level = "lintranslator-notice-error" if error else "lintranslator-notice"
        other = "lintranslator-notice" if error else "lintranslator-notice-error"
        self.target_label.remove_css_class(other)
        self.target_label.add_css_class(level)
        self.target_label.set_text(text)

    def _show_error(self, text: str) -> None:
        """Show an error in the status line's slot, never as an extra line.

        Errors used to get a label of their own, which added height to the card
        exactly when something had gone wrong. The row holds one line either
        way; the full text stays reachable in the tooltip.
        """
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
