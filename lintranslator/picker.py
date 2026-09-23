"""Visual region picker: drag a rectangle over a screenshot of the screen.

Why this design rather than a transparent fullscreen overlay: capturing the
mouse over a live fullscreen game requires either an override-redirect surface or
a compositor grab, both of which are restricted on Wayland. Showing a frozen
screenshot in a normal window sidesteps that entirely, works on every compositor,
and has a real advantage - the image does not move while you drag.

The picker shows what the pipeline will see:
  * a **Preview** choice of the crop itself, the autocontrasted crop, or a
    threshold view - the two derived views exist so it is obvious when a region
    is mostly background art, and neither one changes what OCR reads
  * live OCR text and confidence, so framing is judged by the actual result
"""
from __future__ import annotations

import threading
import time
from io import BytesIO
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from .config import Config, Region  # noqa: E402
from .ocr import TesseractOcr  # noqa: E402
from .occlusion import GUARD  # noqa: E402
from .selection import HANDLE_WIDGET_MIN, SelectionMath  # noqa: E402

HANDLE = 8  # px grab tolerance for dragging an existing edge
PICKER_WINDOW = "picker"
# How long a *mapped* picker waits before it grabs: long enough for the
# compositor to take this window off the screen (it would otherwise be captured
# as part of the screenshot) and for the user to switch to the game. Only spent
# when this window is on screen - see `_capture_screen`.
CAPTURE_DELAY_MS = 1000


class RegionPicker(Gtk.ApplicationWindow):
    """Pick a rectangle; preview its OCR; save it as a fraction region.

    The screenshot is captured on demand rather than once at construction, so
    you can alt-tab to the game, hit "Capture again", and frame the real
    dialogue box instead of whatever was on screen when the app launched.
    """

    def __init__(
        self,
        app: Gtk.Application,
        config: Config,
        screenshot_png: bytes | None = None,
        from_file: str | None = None,
    ):
        super().__init__(application=app, title="LinTranslator — select the dialogue box")
        # Size to the screen so the canvas is never the cramped minimum: the
        # screenshot is letterboxed into whatever the canvas gets, and a small
        # window makes precise selection needlessly hard.
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        if monitor is not None:
            geo = monitor.get_geometry()
            self.set_default_size(
                max(900, min(1500, int(geo.width * 0.78))),
                max(600, min(980, int(geo.height * 0.78))),
            )
        else:
            self.set_default_size(1320, 820)

        # Everything visual comes from lintranslator/theme.py. Without this the picker
        # was stock Adwaita/Breeze next to an already-styled panel - two visual
        # languages in one app - and on this desktop that means Breeze *light*,
        # so any widget nobody classified came out white.
        self.add_css_class("lintranslator-app")

        self.config = config
        self.screen_image: Image.Image | None = None
        self.screen_size: tuple[int, int] = (0, 0)

        self.ocr = TesseractOcr(
            langs=config.ocr.langs,
            psm=config.ocr.psm,
            upscale=config.ocr.upscale,
            autocontrast=config.ocr.autocontrast,
            tessdata_dir=config.ocr.tessdata_dir,
            min_confidence=config.ocr.min_confidence,
            allow_unverified_tessdata=config.ocr.allow_unverified_tessdata,
            on_progress=self._ocr_progress,
        )
        # The first OCR may have to fetch language data. Doing that here, on the
        # main loop, froze the whole window - the picker is the one place that
        # reads the screen *on* the main loop (the panel's pipeline has its own
        # thread), so its preparation is moved off it. Until this is set, the
        # preview says what it is waiting for instead of calling into tesseract.
        self._ocr_ready = threading.Event()
        self._ocr_error: Exception | None = None
        threading.Thread(target=self._prepare_ocr, name="lintranslator-ocr-setup", daemon=True).start()

        # Selection in *screen* pixels; mapped to widget space for drawing.
        self.sel: tuple[int, int, int, int] | None = None
        self._drag_origin: tuple[float, float] | None = None
        self._drag_last: tuple[float, float] | None = None
        self._drag_mode: str | None = None
        self._drag_sel: tuple[int, int, int, int] | None = None
        self._preview_token = 0
        self._watching = False
        # Translation runs off the GTK thread; the label updates via GLib.idle_add.
        self._trans_token = 0
        self._trans_pending = False
        self._translator = None
        self._last_ocr_text = ""

        # Closing the window (the X, or the WM's close) must shut everything
        # down. Previously only the Close *button* quit the app, so closing the
        # picker left the translation panel on screen with its pipeline still
        # running - an orphaned "Waiting for dialogue…" window.
        self.connect("close-request", self._on_close_request)

        # This window must not be captured. Report when it is on screen so the
        # pipeline pauses instead of reading its own UI; the map/unmap pair also
        # keeps the status line honest about whether reading is live.
        self.connect("map", lambda *_: self._on_mapped(True))
        self.connect("unmap", lambda *_: self._on_mapped(False))

        self._build()
        self._set_watching(False)
        self._prepare_panel()
        if from_file:
            # Useful for re-framing from an existing screenshot, and for testing
            # without grabbing the live screen.
            self.set_screenshot(Path(from_file).read_bytes())
        elif screenshot_png:
            self.set_screenshot(screenshot_png)

    # -- screenshot -------------------------------------------------------- #
    def set_screenshot(self, png: bytes) -> None:
        with Image.open(BytesIO(png)) as im:
            self.screen_image = im.convert("RGB")
            self.screen_image.load()
        previous_size, self.screen_size = self.screen_size, self.screen_image.size
        self._source_surf = None
        self._source_bytes = None
        self.status_label.set_text(
            f"captured {self.screen_size[0]}x{self.screen_size[1]} — drag over the dialogue text"
        )
        if self.sel is None or previous_size != self.screen_size:
            self._seed_from_config()
        else:
            # Re-capturing to reframe must not throw away the box the user just
            # dragged: they are re-grabbing *to* frame it against the live screen.
            x, y, w, h = self.sel
            self.sel = (
                max(0, min(x, self.screen_size[0] - 1)),
                max(0, min(y, self.screen_size[1] - 1)),
                max(1, min(w, self.screen_size[0] - x)),
                max(1, min(h, self.screen_size[1] - y)),
            )
            GLib.idle_add(self._refresh_preview)
        self.area.queue_draw()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        # Vertical root: the canvas and the sidebar share the top, and one
        # toolbar owns the bottom of the window.
        #
        # The buttons used to live at the bottom of the sidebar in two
        # homogeneous rows of full-width buttons - five controls of equal width
        # and therefore equal weight, so the primary action ("Watch live")
        # looked exactly like "Close", and the split between the rows (capture
        # and save, then watch, settings and close) said nothing. Five buttons
        # do not fit one row in a 340px sidebar, so the row spans the window
        # instead, which is also where a one-line status readout belongs.
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # The root must expand to fill the window. Without this the box keeps its
        # natural size (the sum of its children's requests) and GTK centres it,
        # so the canvas never gets the space the window appears to offer.
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.set_halign(Gtk.Align.FILL)
        root.set_valign(Gtk.Align.FILL)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.set_halign(Gtk.Align.FILL)
        body.set_valign(Gtk.Align.FILL)

        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        # FILL alignment is required: a DrawingArea's natural size is tiny, and
        # without it the canvas can be allocated (and drawn at) a stale, much
        # smaller size than the space it is given.
        self.area.set_halign(Gtk.Align.FILL)
        self.area.set_valign(Gtk.Align.FILL)
        # Without a minimum width the fixed-width sidebar plus its margins claims
        # the whole window and the canvas is allocated 0px wide, which renders
        # nothing at all - a silent failure that looks like a drawing bug.
        self.area.set_size_request(520, -1)
        self.area.set_draw_func(self._on_draw)

        drag = Gtk.GestureDrag()
        drag.set_button(Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        self.canvas_box = self.area
        body.append(self.area)

        # The sidebar must be wrapped: without this its wrapping labels report
        # their unwrapped width as the natural size and the sidebar requests
        # ~960px, starving the canvas down to a useless sliver.
        side_scroller = Gtk.ScrolledWindow()
        side_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side_scroller.set_propagate_natural_width(False)
        side_scroller.set_vexpand(True)
        self.sidebar = self._build_sidebar()
        side_scroller.set_child(self.sidebar)
        self.side_scroller = side_scroller
        body.append(side_scroller)

        root.append(body)
        root.append(self._build_toolbar())
        self.set_child(root)

    @staticmethod
    def _rule() -> Gtk.Widget:
        """A 1px rule whose colour and spacing come from the theme.

        A Gtk.Separator would be the same line in a colour this app does not
        own; the theme paints `.lintranslator-rule`, and stood on end in the toolbar the
        same class is what separates the groups of buttons.
        """
        rule = Gtk.Box()
        rule.add_css_class("lintranslator-rule")
        return rule

    def _build_toolbar(self) -> Gtk.Widget:
        """The window's one control row: status, then every action.

        The primary action carries the theme's accent and stands alone at the
        end of the row, past a rule: it is the reason the window exists, and it
        must not look like its neighbours. The region actions (Capture, Save)
        and the window actions (Settings, Close) are separated from each other
        by a second rule, so Close - which ends the session - is not adjacent to
        the button that starts one.
        """
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("lintranslator-toolbar")

        # The status line lives here rather than at the bottom of the sidebar:
        # it is one line of state ("captured 2560x1440 - drag over the dialogue
        # text", "saved to ..."), it has to be visible without scrolling, and it
        # has room here. Ellipsised rather than wrapped, because this row is a
        # fixed height and a two-line status would make the whole window jump.
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("lintranslator-hint")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_hexpand(True)
        bar.append(self.status_label)

        self.capture_btn = Gtk.Button(label="Capture")
        self.capture_btn.set_tooltip_text(
            "Re-grab the screen — switch to the game first, then click this"
        )
        self.capture_btn.connect("clicked", self._on_capture)
        bar.append(self.capture_btn)

        save = Gtk.Button(label="Save")
        save.set_tooltip_text("Save this region to config.json")
        save.connect("clicked", self._on_save)
        bar.append(save)

        bar.append(self._rule())

        settings = Gtk.Button(label="Settings")
        settings.set_tooltip_text("Backend, model, API key and prompt")
        settings.connect("clicked", lambda *_: self._open_settings())
        bar.append(settings)

        close = Gtk.Button(label="Close")
        close.set_tooltip_text("Close the picker and stop translating")
        close.connect("clicked", self._on_close_clicked)
        bar.append(close)

        bar.append(self._rule())

        self.watch_btn = Gtk.Button(label="Watch live")
        self.watch_btn.set_tooltip_text(
            "Save this region and open the panel that follows the game and "
            "translates each new line"
        )
        self.watch_btn.connect("clicked", lambda *_: self._on_start())
        bar.append(self.watch_btn)

        # One class for the row, so the buttons are one size and one weight...
        for button in (self.capture_btn, save, settings, close, self.watch_btn):
            button.add_css_class("lintranslator-btn")
            button.add_css_class("lintranslator-tool")
        # ...and one exception, which is the whole point of the row.
        self.watch_btn.add_css_class("lintranslator-primary")
        return bar

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.add_css_class("lintranslator-side")
        # A request, not a floor. It was set from the widest control in the
        # column - the "Re-translate when I adjust the box" checkbox, which
        # measured 309px - plus the theme's 14px padding on each side. That
        # checkbox is gone (see `_refresh_preview`) and its successor, the
        # Preview dropdown, measures 197px, so this floor is now roomier than
        # anything under it needs. Lowering it would mean re-measuring the
        # column, which is what this comment exists to warn about.
        #
        # This number used to be meaningless: the coordinate readout was one
        # unwrapped line whose 539px minimum became the sidebar's minimum, so
        # the column took 769px of the 1500px window and the canvas - the whole
        # point of the window - was left with 731px. Wrapping that readout is
        # what makes the request the real width.
        side.set_size_request(340, -1)
        side.set_valign(Gtk.Align.FILL)

        title = Gtk.Label(label="Drag over the dialogue text", xalign=0)
        title.add_css_class("lintranslator-title")
        title.set_tooltip_text(
            "Drag a box, or press Find box to snap it to the nearest text"
        )
        side.append(title)

        hint = Gtk.Label(
            label=(
                "Cover the whole text block. A region that is slightly too short "
                "still produces plausible OCR while silently dropping a line, so "
                "check the preview below."
            ),
            xalign=0,
            wrap=True,
        )
        hint.add_css_class("lintranslator-hint")
        side.append(hint)

        # The box, in a sunken readout: it changes on every drag, and a number
        # that moves belongs in a fixed frame rather than in a line of prose.
        self.coords_label = Gtk.Label(label="no selection", xalign=0, wrap=True)
        self.coords_label.add_css_class("lintranslator-readout")
        self.coords_label.add_css_class("lintranslator-dim")
        side.append(self.coords_label)

        # Which image the preview shows is one choice with three answers, so it
        # is one control. It used to be two checkboxes - "Show OCR input
        # (autocontrast)" and "Show threshold view" - which could both be
        # ticked while only one of them did anything (`if threshold / elif
        # autocontrast`), and which together left the raw crop unreachable
        # without unticking both. A dropdown cannot express that contradiction.
        #
        # Display only: none of the three is what OCR reads. The text below
        # comes from `self.ocr.read(crop)` whatever is selected here, and
        # "threshold" is this panel's own fixed cut, not the calibrator's
        # per-image one.
        self.preview_choice = Gtk.DropDown.new_from_strings(
            ["Preview: raw", "Preview: OCR input", "Preview: threshold"]
        )
        self.preview_choice.set_tooltip_text(
            "How the crop is drawn above. Display only — it does not change what "
            "OCR reads or the text below."
        )
        # Index 1 keeps the old default (autocontrast), which is the view the
        # eye judges glyphs by.
        self.preview_choice.set_selected(1)
        self.preview_choice.connect("notify::selected", lambda *_: self._refresh_preview())
        side.append(self.preview_choice)

        side.append(self._rule())

        # The crop itself. Height only: a width request here is a floor under
        # the whole sidebar, and the picture fills whatever the frame is given.
        self.preview = Gtk.Picture()
        self.preview.set_size_request(-1, 110)
        self.preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        # NOT hexpand, and that is load-bearing out of proportion to its size.
        # GtkBox decides which children get the window's spare width with
        # `gtk_widget_compute_expand`, which is true for a widget whose *any*
        # descendant expands. So this one flag propagated up through the sidebar
        # and its scroller, made the whole column count as expanding, and split
        # the surplus evenly with the canvas: measured, the column took 660px of
        # a 1500px window and the canvas - the entire point of the window - was
        # left with 840px, while the sidebar's own 340px request was ignored.
        # The frame still gives the picture its full width, because a Gtk.Box
        # fills its children by default.
        self.preview.set_hexpand(False)
        # A Gtk.Picture's natural width is the image's own, 3186px for a
        # 2560-wide screenshot. `set_size_request` only lowers the minimum, so
        # the natural width is capped here instead, by the same scroller trick
        # the sidebar itself uses.
        preview_clip = Gtk.ScrolledWindow()
        preview_clip.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        preview_clip.set_propagate_natural_width(False)
        preview_clip.set_propagate_natural_height(False)
        preview_clip.set_child(self.preview)
        preview_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        preview_frame.add_css_class("lintranslator-frame")
        preview_frame.append(preview_clip)
        side.append(preview_frame)

        # The OCR text is what the box is judged by, and it is the pane that
        # takes the column's leftover height. Before this, the sidebar's content
        # ended 310px above the bottom of a 960px column and the gap was simply
        # empty; here it becomes text room. Top-aligned, so more height shows
        # more of the reading rather than the same reading lower down.
        self.ocr_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.ocr_label.set_selectable(True)
        self.ocr_label.set_yalign(0)
        self.ocr_label.set_vexpand(True)
        self.ocr_label.add_css_class("lintranslator-readout")
        self.ocr_label.add_css_class("lintranslator-body")
        side.append(self.ocr_label)

        self.conf_label = Gtk.Label(label="", xalign=0)
        self.conf_label.add_css_class("lintranslator-caption")
        side.append(self.conf_label)

        side.append(self._rule())

        trans_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head = Gtk.Label(label="Translation", xalign=0)
        head.add_css_class("lintranslator-section")
        trans_head.append(head)
        # A caption rather than <small> markup: the type scale is the theme's,
        # and "preview only" is exactly what `.lintranslator-caption` is for.
        note = Gtk.Label(label="preview only", xalign=0)
        note.add_css_class("lintranslator-caption")
        # Not hexpand. It used to push Translate to the far right, but it also
        # propagated "expands" up to the sidebar's scroller and cost the canvas
        # half the window's spare width (see the picture above). The button sits
        # beside the caption instead, next to the thing it acts on.
        trans_head.append(note)
        self.translate_btn = Gtk.Button(label="Translate")
        self.translate_btn.add_css_class("lintranslator-btn")
        self.translate_btn.set_tooltip_text(
            "Translate this region now, using the configured backend"
        )
        self.translate_btn.connect("clicked", self._on_translate_clicked)
        trans_head.append(self.translate_btn)
        side.append(trans_head)

        # Translating as the region is adjusted is the whole point of this
        # panel, so it is not optional. The debounce in `_translate_async` and
        # the translator's cache are what keep that from being expensive; the
        # checkbox that used to live here only added a way to be surprised by a
        # stale translation (see `_refresh_preview` and `_on_settings_applied`).
        self.backend_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.backend_label.add_css_class("lintranslator-caption")
        self.backend_label.set_max_width_chars(34)
        side.append(self.backend_label)
        self._refresh_backend_label()

        # A visible explanation, because a still screenshot that never updates
        # looks exactly like a broken live translator.
        self.watch_status = Gtk.Label(label="", xalign=0, wrap=True)
        self.watch_status.add_css_class("lintranslator-hint")
        self.watch_status.set_max_width_chars(34)
        side.append(self.watch_status)

        self.snapshot_note = Gtk.Label(
            label=(
                "This window is a still screenshot, so it will not follow the "
                "game. Press Watch live to translate continuously."
            ),
            xalign=0,
            wrap=True,
        )
        self.snapshot_note.add_css_class("lintranslator-caption")
        self.snapshot_note.set_max_width_chars(34)
        side.append(self.snapshot_note)

        self.trans_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.trans_label.set_selectable(True)
        self.trans_label.set_max_width_chars(34)
        self.trans_label.add_css_class("lintranslator-readout")
        self.trans_label.add_css_class("lintranslator-body")
        side.append(self.trans_label)

        # Second line of defence: a wrapping label's natural width is its
        # unwrapped width, so cap it explicitly.
        child = side.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                child.set_max_width_chars(34)
                child.set_hexpand(False)
            child = child.get_next_sibling()
        return side

    def _seed_from_config(self) -> None:
        """Start from the configured region so the picker opens on something useful."""
        if self.screen_image is None:
            return
        region = self.config.capture.region
        x, y, w, h = region.to_pixels(*self.screen_size)
        self.sel = (x, y, w, h)
        GLib.idle_add(self._refresh_preview)

    # -- geometry ---------------------------------------------------------- #
    def math(self) -> SelectionMath:
        """Coordinate mapping for the current widget size."""
        return SelectionMath(
            screen_w=self.screen_size[0],
            screen_h=self.screen_size[1],
            widget_w=float(self.area.get_width()),
            widget_h=float(self.area.get_height()),
        )

    def _image_rect(self) -> tuple[float, float, float, float]:
        m = self.math()
        ox, oy = m.offset
        return (ox, oy, m.draw_w, m.draw_h)

    def _widget_to_screen(self, x: float, y: float) -> tuple[int, int]:
        return self.math().to_screen(x, y)

    def _screen_to_widget(self, x: float, y: float) -> tuple[float, float]:
        return self.math().to_widget(x, y)

    def _hit_test(self, x: float, y: float) -> str:
        if self.screen_size == (0, 0):
            return "new"
        return self.math().hit_test(x, y, self.sel, HANDLE)


    # -- interaction ------------------------------------------------------- #
    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        self._drag_mode = self._hit_test(x, y)
        self._drag_origin = (x, y)
        self._drag_last = (x, y)
        # The box this drag starts from. A resize edge is placed at its original
        # position plus the pointer's total travel, so the box cannot drift as
        # events arrive and the edge keeps the offset it was grabbed with.
        self._drag_sel = self.sel
        if self._drag_mode == "new":
            sx, sy = self._widget_to_screen(x, y)
            self.sel = (sx, sy, 1, 1)

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag_origin is None or self._drag_mode is None or self.screen_image is None:
            return
        ox, oy = self._drag_origin
        # GestureDrag offsets are cumulative from the drag start, which is what
        # edge resizing wants. A *move* must instead shift by the change since
        # the previous update, or the selection would advance by the total drag
        # distance on every event and run away from the pointer.
        cursor = (ox + dx, oy + dy)
        last = getattr(self, "_drag_last", (ox, oy))

        if self._drag_mode == "move" and self.sel is not None:
            self.sel = SelectionMath.apply_drag(
                "move",
                self.sel,
                self._widget_to_screen(*last),
                self._widget_to_screen(*cursor),
                self.screen_size,
            )
            self._drag_last = cursor
        else:
            self.sel = SelectionMath.apply_drag(
                self._drag_mode,
                self._drag_sel if self._drag_sel is not None else self.sel,
                self._widget_to_screen(ox, oy),
                self._widget_to_screen(*cursor),
                self.screen_size,
            )

        self.area.queue_draw()
        self._schedule_preview()

    def _on_drag_end(self, _gesture, _dx: float, _dy: float) -> None:
        self._drag_mode = None
        self._drag_origin = None
        self._drag_last = None
        self._drag_sel = None
        self._refresh_preview()
        # Keep a running pipeline pointed at the box the user can see. The
        # config file is only written by Save / Apply box, but the area being
        # read follows the drag, so the rectangle on screen is never a lie.
        if self._stage_region():
            self.status_label.set_text("box changed — press Apply box to keep watching it")

    def _region_from_selection(self) -> Region | None:
        """The current selection as a fraction region, or None without one."""
        if self.sel is None or self.screen_image is None:
            return None
        x, y, w, h = self.sel
        screen_w, screen_h = self.screen_size
        return Region(
            x=x / screen_w, y=y / screen_h, w=w / screen_w, h=h / screen_h, mode="fraction"
        )

    def _stage_region(self) -> bool:
        """Adopt the current selection in memory and re-point a running pipeline."""
        region = self._region_from_selection()
        if region is None:
            return False
        self.config.capture.region = region
        self._apply_region_live()
        return True

    def _on_motion(self, _controller, x: float, y: float) -> None:
        cursor = {
            "n": "n-resize",
            "s": "s-resize",
            "e": "e-resize",
            "w": "w-resize",
            "ne": "ne-resize",
            "nw": "nw-resize",
            "se": "se-resize",
            "sw": "sw-resize",
            "move": "move",
        }.get(self._hit_test(x, y), "crosshair")
        self.area.set_cursor(Gdk.Cursor.new_from_name(cursor, None))

    # -- drawing ----------------------------------------------------------- #
    def _on_draw(self, area, cr, width: int, height: int) -> None:
        # Use the widget's real allocation, not the callback's width/height:
        # those can be the DrawingArea's natural size (its size request), which
        # is much smaller than the space it was actually given.
        width = area.get_width()
        height = area.get_height()
        if self.screen_image is None:
            cr.set_source_rgb(0.10, 0.10, 0.12)
            cr.paint()
            cr.set_source_rgb(0.85, 0.85, 0.88)
            cr.select_font_face("sans")
            cr.set_font_size(16)
            cr.move_to(28, height / 2)
            cr.show_text("No screenshot yet — click \"Capture again\" to grab the screen.")
            return

        ox, oy, draw_w, draw_h = self._image_rect()

        # Paint the screenshot at FULL resolution under a cairo transform.
        #
        # Deliberately not a pre-scaled buffer: keeping one means two places
        # compute the displayed size (the buffer and the transform) and they can
        # silently disagree, which shows up as an image drawn far smaller than
        # the selection rectangle. Scaling here means the image always fills
        # exactly the rect that the selection maths uses.
        surface = self._source_surface()
        if surface is not None:
            cr.save()
            cr.translate(ox, oy)
            cr.scale(
                draw_w / float(self.screen_size[0]),
                draw_h / float(self.screen_size[1]),
            )
            cr.set_source_surface(surface, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()

        if self.sel is not None:
            x0, y0, w, h = self.sel
            wx0, wy0 = self._screen_to_widget(x0, y0)
            wx1, wy1 = self._screen_to_widget(x0 + w, y0 + h)

            # Dim everything outside the selection.
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.rectangle(ox, oy, draw_w, max(0.0, wy0 - oy))
            cr.rectangle(ox, wy1, draw_w, max(0.0, oy + draw_h - wy1))
            cr.rectangle(ox, wy0, max(0.0, wx0 - ox), max(0.0, wy1 - wy0))
            cr.rectangle(wx1, wy0, max(0.0, ox + draw_w - wx1), max(0.0, wy1 - wy0))
            cr.fill()

            cr.set_source_rgb(1.0, 0.85, 0.3)
            cr.set_line_width(2.0)
            cr.rectangle(wx0, wy0, wx1 - wx0, wy1 - wy0)
            cr.stroke()

            # Handles at the corners and at the middle of each edge. The whole
            # border is grabbable - that is what the resize cursor promises - but
            # a bare 2px outline does not look it, and a corner-only affordance
            # reads as "you can move this, maybe". Sized to the pointer
            # tolerance, so the thing you aim at is the thing that answers.
            half = HANDLE_WIDGET_MIN / 2.0
            middle_x, middle_y = (wx0 + wx1) / 2.0, (wy0 + wy1) / 2.0
            for hx in (wx0, middle_x, wx1):
                for hy in (wy0, middle_y, wy1):
                    if hx == middle_x and hy == middle_y:
                        continue  # the middle of the box is the move handle
                    cr.rectangle(hx - half, hy - half, half * 2, half * 2)
                    cr.fill()

    def _source_surface(self):
        """Full-resolution cairo surface of the screenshot, built once."""
        if self.screen_image is None:
            return None
        if getattr(self, "_source_surf", None) is not None:
            return self._source_surf
        # Keep the buffer alive: ImageSurface does not copy the data.
        self._source_bytes = self.screen_image.convert("RGBA").tobytes()
        self._source_surf = cairo.ImageSurface.create_for_data(
            bytearray(self._source_bytes),
            cairo.FORMAT_RGB24,
            self.screen_image.width,
            self.screen_image.height,
        )
        return self._source_surf

    @staticmethod
    def _to_texture(image: Image.Image):
        """PIL image -> Gdk.Texture, which is a Gdk.Paintable.

        Gtk.Picture.set_paintable rejects a GdkPixbuf, so previews must go
        through a texture.
        """
        if image.mode != "RGB":
            image = image.convert("RGB")
        return Gdk.MemoryTexture.new(
            image.width,
            image.height,
            Gdk.MemoryFormat.R8G8B8,
            GLib.Bytes.new(image.tobytes()),
            image.width * 3,
        )

    # -- OCR preparation ---------------------------------------------------- #
    def _ocr_progress(self, message: str) -> None:
        """Show what the background preparation is doing.

        Called from the setup thread, so the label is touched through the main
        loop rather than from that thread.
        """
        GLib.idle_add(self.status_label.set_text, message)

    def _prepare_ocr(self) -> None:
        """Fetch and validate the language data before the first read needs it.

        Off the main loop on purpose: `ensure_ready` can spend seconds on the
        network the first time, and this window is the one that reads the screen
        on the main loop - a picker frozen mid-drag with no explanation is worse
        than one that says what it is waiting for.
        """
        try:
            self.ocr.ensure_ready()
        except Exception as exc:  # noqa: BLE001 - surfaced through the status row
            self._ocr_error = exc
            GLib.idle_add(self.status_label.set_text, f"OCR unavailable: {exc}")
        finally:
            self._ocr_ready.set()
            GLib.idle_add(self._repreview_if_selected)

    def _repreview_if_selected(self) -> bool:
        """Run the preview that was skipped while preparation was in flight."""
        if self.sel is not None and self.screen_image is not None:
            self._schedule_preview()
        return False

    def _ocr_blocked_reason(self) -> str | None:
        """Why OCR cannot run yet, or None when it can.

        A failure is reported rather than retried: `ensure_ready` would raise
        again on the main loop, and a network failure can take a minute to say
        so.
        """
        if self._ocr_error is not None:
            return f"OCR unavailable: {self._ocr_error}"
        if not self._ocr_ready.is_set():
            return "preparing OCR language data…"
        return None

    # -- preview ----------------------------------------------------------- #
    def _schedule_preview(self) -> None:
        self._preview_token += 1
        token = self._preview_token
        GLib.timeout_add(180, self._deferred_preview, token)

    def _deferred_preview(self, token: int) -> bool:
        if token != self._preview_token:
            return False  # superseded by a newer drag position
        self._refresh_preview()
        return False

    def _refresh_preview(self) -> None:
        if self.sel is None or self.screen_image is None:
            return
        x, y, w, h = self.sel
        self.coords_label.set_text(
            f"x={x} y={y} w={w} h={h}   (fractions {x / self.screen_size[0]:.4f}, "
            f"{y / self.screen_size[1]:.4f}, {w / self.screen_size[0]:.4f}, "
            f"{h / self.screen_size[1]:.4f})"
        )
        crop = self.screen_image.crop((x, y, x + w, y + h))
        if crop.width < 2 or crop.height < 2:
            return

        try:
            # 0 raw, 1 the OCR input, 2 the ink mask. Raw is genuinely reachable
            # now, so there is no unreachable third branch as there was when
            # this was two checkboxes.
            mode = self.preview_choice.get_selected()
            if mode == 2:
                view = self._threshold_view(crop)
            elif mode == 1:
                view = ImageOps.autocontrast(crop.convert("L")).convert("RGB")
            else:
                view = crop
            shown = view.resize(
                (max(1, view.width * 3), max(1, view.height * 3)), Image.LANCZOS
            )
            self.preview.set_paintable(self._to_texture(shown))
        except Exception as exc:  # noqa: BLE001
            self.status_label.set_text(f"preview failed: {exc}")

        blocked = self._ocr_blocked_reason()
        if blocked:
            self.ocr_label.set_text(blocked)
            self.conf_label.set_text("")
            return

        try:
            result = self.ocr.read(crop)
        except Exception as exc:  # noqa: BLE001
            self.ocr_label.set_text(f"OCR error: {exc}")
            self.conf_label.set_text("")
            return

        if result.lines:
            self.ocr_label.set_text(result.display_text)
            self.conf_label.set_text(
                f"confidence {result.confidence:.0f}   {len(result.lines)} line(s)"
                + (f"   {result.dropped} dropped" if result.dropped else "")
            )
            self._last_ocr_text = result.text
            # Always translate. This was behind a "Re-translate when I adjust
            # the box" checkbox, which was doing more harm than good: the
            # debounce below coalesces a drag into one request and the
            # translator's cache makes unchanged text free, so the checkbox was
            # not holding back a flood - but the same flag also gated the
            # Settings re-translate, so switching model and pressing Apply with
            # it unticked left the old model's translation on screen under the
            # new settings. It also meant the picker could not answer "is the
            # box right?" the one way that settles the question - the text.
            self._translate_async(result.text, debounce_ms=700)
        else:
            self.ocr_label.set_text("(no text found in this region)")
            self.conf_label.set_text("")

    @staticmethod
    def _threshold_view(crop: Image.Image) -> Image.Image:
        """What the calibrator's ink mask sees for this crop."""
        gray = crop.convert("L")
        mask = gray.point(lambda p: 255 if p > 150 else 0)
        return mask.convert("RGB")

    # -- start watching ---------------------------------------------------- #
    def _on_start(self) -> None:
        """Save the region, start (or re-point) the panel, and get out of the way.

        Deliberately in-process: the panel is another window of the same
        application rather than a second `lintranslator gui` run. That keeps the
        picker-to-watching flow inside the GUI, with no command to type.

        The order matters and used to be wrong. Reading started instantly, while
        this window was still on screen over the box: the first capture contained
        the picker's own status line ("captured 2560x1440 — drag over the dialogue
        text") at 93% confidence, and the dialogue line underneath it was dropped
        by the confidence gate. So now the pipeline is started paused and this
        window takes itself off the screen, which is what lets reading begin.
        """
        if self.sel is None or self.screen_image is None:
            self.status_label.set_text("capture the screen and select a region first")
            return

        x, y, w, h = self.sel
        screen_w, screen_h = self.screen_size

        region = Region(
            x=x / screen_w, y=y / screen_h, w=w / screen_w, h=h / screen_h, mode="fraction"
        )
        self.config.capture.region = region
        self.config.save()

        # The panel already exists (shown idle at startup); just start it.
        if getattr(self, "_panel", None) is None:
            self._prepare_panel()
        panel = self._panel
        panel.config = self.config
        panel.present()
        if panel.worker is None:
            panel.start_pipeline()
        else:
            # Already watching: re-point the running loop instead of rebuilding
            # it. A rebuild would reload the model and move the card, and until
            # it happened the picker would be showing one box while the pipeline
            # read another.
            panel.worker.request_region(region)
        panel.toggle_btn.set_label("Pause")
        # The picker stays reachable (panel's Region button) but leaves the
        # screen: it is the only thing that shows which rectangle is being read,
        # and it is also the easiest way to accidentally translate it.
        self._set_watching(True)
        self._minimise_for_watching()

    # -- staying out of the capture ---------------------------------------- #
    def _on_mapped(self, mapped: bool) -> None:
        """Tell the pipeline whether this window is on screen."""
        if mapped:
            GUARD.set_mapped(
                PICKER_WINDOW,
                True,
                "the region picker is on screen — minimise it to keep translating",
            )
        else:
            GUARD.clear(PICKER_WINDOW)
        self._refresh_watch_status()

    def _minimise_for_watching(self) -> None:
        """Get this window off the screen, then let the pipeline start reading.

        The pipeline is gated on this window being unmapped, so this call is what
        actually starts translation - no sleep, no guessing at a delay long
        enough for the compositor.

        Both a minimise and a hide are attempted, because minimising is what we
        want (the taskbar entry stays, so the window can be brought back the usual
        way) but it is not universally honoured: measured on kwin_wayland 6.7.4
        with GTK 4, `minimize()` is ignored outright and the window stays mapped
        (see `probe/minimise_check.py`). There the hide is what takes effect, and
        the panel's Region button is the way back.
        """
        self.minimize()
        # Long enough for a compositor that does honour minimise to have unmapped
        # the window; short enough that the wait is not felt when it does not.
        # Measured on kwin_wayland: minimise is ignored outright, so on this
        # desktop the fallback is what runs and every millisecond here is latency
        # before the first capture.
        GLib.timeout_add(150, self._ensure_off_screen)

    def _ensure_off_screen(self) -> bool:
        """Fallback: hide the window if it is somehow still on screen."""
        if self._watching and self.get_visible() and self.get_mapped():
            self.set_visible(False)
        return False

    def restore(self) -> None:
        """Bring the picker back, re-grabbing the screen so the box can be reframed.

        Wired to the panel's Region button. Reading pauses by itself while this
        window is up, so the user can drag freely; pressing "Apply box" hides it
        again and reading resumes on the new area.

        The screen is captured again on the way back. Reopening on the shot taken
        when watching started means framing the box against a screen the game has
        long since moved on from - and reframing is the only reason to press
        Region at all. The box itself is kept (`set_screenshot` re-seeds it only
        when the screen size changed), so this is a reframe, not a reset.
        """
        if hasattr(self, "unminimize"):
            self.unminimize()
        self._refresh_watch_status()
        # While watching, this window is already off screen - that is what
        # watching means - so the grab can start at once. The wait is only owed
        # when the window is somehow still mapped: it has to leave the screen
        # first, or the "screenshot of the game" would contain the picker, and the
        # countdown is also the moment to switch to the game.
        if self.get_mapped():
            self._capture_screen(
                delay_ms=CAPTURE_DELAY_MS,
                note="capturing in 1s — switch to the game now…",
            )
        else:
            self._capture_screen(delay_ms=0, note="capturing the screen…")

    def _shutdown_panel(self) -> None:
        panel = getattr(self, "_panel", None)
        if panel is None:
            return
        # Disconnect first: the panel's own close handler would otherwise try to
        # re-show a picker that is going away.
        try:
            panel.disconnect_by_func(self._on_panel_closed)
        except (TypeError, RuntimeError):
            pass
        closer = getattr(panel, "close_pipeline", None)
        if callable(closer):
            closer()
        panel.destroy()
        self._panel = None

    def _on_panel_closed(self, _panel) -> bool:
        closer = getattr(self._panel, "close_pipeline", None)
        if callable(closer):
            closer()
        self._panel = None
        self._set_watching(False)
        # Closing the panel while this window is off screen would leave the
        # process with nothing visible at all - the orphaned-window failure this
        # project has already shipped once. Closing the card brings the picker
        # back, which is also where the user would expect to end up.
        if not self.get_visible():
            self.restore()
        return False  # let the panel actually close

    def _prepare_panel(self) -> None:
        """Put the translation card on screen straight away, not yet running.

        The panel is where translations appear, so showing it up front means it
        can be dragged clear of the captured area before translation begins -
        otherwise it tends to sit right over the text and get captured itself.
        """
        from .panel import TranslatorPanel

        self._panel = TranslatorPanel(self.get_application(), self.config, position=None)
        self._panel.connect("close-request", self._on_panel_closed)
        # Give the panel the pause reason and a way back to this window.
        self._panel.gate = GUARD.reason
        self._panel.enable_region_button(self.restore)
        self._panel.show_idle()
        self._panel.present()

    def _set_watching(self, watching: bool) -> None:
        """Reflect whether the panel is running, so the two windows agree."""
        self._watching = watching
        if watching:
            self.watch_btn.set_label("Apply box")
        else:
            self.watch_btn.set_label("Watch live")
        self._refresh_watch_status()

    def _refresh_watch_status(self) -> None:
        """Say whether the box is being read, and why not when it is not."""
        if not getattr(self, "_watching", False):
            self.watch_status.set_text(
                "Press Watch live to translate this box continuously."
            )
            return
        if self.get_mapped():
            self.watch_status.set_text(
                "PAUSED — this window is on screen, so the box is not being read "
                "(it would be captured too). Minimise it, or press Apply box."
            )
        elif self.get_visible():
            self.watch_status.set_text(
                "LIVE — this window is off screen and the box is being read. "
                "Press Region on the panel to re-capture and bring it back."
            )
        else:
            self.watch_status.set_text(
                "LIVE — this window is hidden while watching. Press Region on the "
                "panel to re-capture and bring it back."
            )

    # -- closing ----------------------------------------------------------- #
    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        self._shutdown_panel()
        self.close()

    def _on_close_request(self, _window) -> bool:
        """Stop the panel and its worker before the picker goes away, then exit.

        Returning False lets the window close; quitting afterwards matters
        because a GTK application keeps running while any window exists, and a
        hidden window counts. Without the quit, the process lingered with no
        visible window at all.
        """
        self._shutdown_panel()
        GUARD.clear(PICKER_WINDOW)
        application = self.get_application()
        if application is not None:
            # After this window is gone there is nothing left to show.
            GLib.idle_add(application.quit)
        return False  # allow the picker itself to close

    # -- settings ---------------------------------------------------------- #
    def _open_settings(self) -> None:
        from .settings import SettingsDialog

        self._settings_dialog = SettingsDialog(
            self, self.config, on_apply=self._on_settings_applied
        )
        self._settings_dialog.present()

    def _on_settings_applied(self) -> None:
        """Drop the cached translator so the new backend/model is used."""
        if self._translator is not None:
            self._translator.close()
            self._translator = None
        self._refresh_backend_label()
        # Re-translate the current selection with the new settings immediately:
        # changing the model and seeing nothing happen would look broken. This
        # used to be conditional on the "Re-translate when I adjust the box"
        # checkbox, which was indefensible - the setting had just been applied,
        # so the old translation was no longer even the old model's answer to
        # the current configuration.
        if self._last_ocr_text:
            self._translate_async(self._last_ocr_text, debounce_ms=0)

    def _refresh_backend_label(self) -> None:
        t = self.config.translate
        model = t.model or "(no model set)"
        # Keep it short: long OpenRouter ids would dominate the sidebar.
        if len(model) > 34:
            model = model[:31] + "…"
        self.backend_label.set_text(f"backend: {t.backend} · {model}")

    # -- translation preview ----------------------------------------------- #
    def _on_translate_clicked(self, _button: Gtk.Button) -> None:
        if not self._last_ocr_text:
            self.trans_label.set_text("no text in the region to translate")
            return
        self._translate_async(self._last_ocr_text, debounce_ms=0)

    def _translate_async(self, text: str, debounce_ms: int = 0) -> None:
        """Translate `text` on a worker thread and show the result.

        Debounced and token-guarded: dragging produces a new selection many times
        a second, and an un-debounced version would fire one API request per
        mouse-move.
        """
        self._trans_token += 1
        token = self._trans_token
        self._trans_pending = True
        self.trans_label.set_text("translating…")

        def start() -> bool:
            if token != self._trans_token:
                return False  # superseded by a newer selection
            threading.Thread(
                target=self._translate_worker, args=(token, text), daemon=True
            ).start()
            return False

        GLib.timeout_add(debounce_ms, start)

    def _translate_worker(self, token: int, text: str) -> None:
        try:
            translator = self._ensure_translator()
            started = time.monotonic()
            result = translator.translate(text)
            elapsed = time.monotonic() - started
            tag = "cached" if translator.last_was_cached else f"{elapsed:.2f}s"
            message = f"{result.target}\n\n— {translator.name} · {tag}"
        except Exception as exc:  # noqa: BLE001
            message = f"translation failed:\n{type(exc).__name__}: {exc}"
        GLib.idle_add(self._show_translation, token, message)

    def _show_translation(self, token: int, message: str) -> bool:
        if token == self._trans_token:
            self.trans_label.set_text(message)
            self._trans_pending = False
        return False

    def _ensure_translator(self):
        """Build the configured translator once, lazily."""
        if self._translator is None:
            from .pipeline import _cache_for, _glossary_for
            from .translate import CachedTranslator, build_translator

            self._translator = CachedTranslator(
                build_translator(self.config.translate),
                cache=_cache_for(self.config),
                glossary=_glossary_for(self.config),
            )
        return self._translator

    # -- actions ----------------------------------------------------------- #
    def _on_capture(self, _button: Gtk.Button) -> None:
        """Re-grab the screen. The picker window itself must not be captured,
        so hide it, wait for the compositor to actually remove it, then grab."""
        self._capture_screen(
            delay_ms=CAPTURE_DELAY_MS,
            note="capturing in 1s — switch to the game now…",
        )

    def _capture_screen(self, delay_ms: int, note: str) -> None:
        """Put a fresh screenshot of the screen into the canvas.

        Shared by the Capture button and the panel's Region button. This window
        is hidden first and shown again when the grab lands, whatever happens:
        leaving it hidden on a failed capture would leave the process with
        nothing on screen at all. `delay_ms` is 0 when the window is already off
        screen, in which case there is nothing to wait for and the shot lands in
        the same click.
        """
        from .capture import ScreenGrabber

        self.capture_btn.set_sensitive(False)
        self.status_label.set_text(note)
        # The picker can be off screen while this runs (the panel's Region
        # button), so say it on the panel too: a button that seems to do nothing
        # for the length of a portal round trip reads as a broken one.
        panel = getattr(self, "_panel", None)
        if getattr(panel, "status_label", None) is not None:
            panel.status_label.set_text("capturing the screen for the picker…")
        self.set_visible(False)

        def do_grab() -> bool:
            try:
                grabber = ScreenGrabber(self.config.capture.region)
                try:
                    full = grabber.grab_full()
                finally:
                    grabber.close()
                self.set_screenshot(full.to_png_bytes())
            except Exception as exc:  # noqa: BLE001
                self.status_label.set_text(f"capture failed: {exc}")
            finally:
                self.set_visible(True)
                self.capture_btn.set_sensitive(True)
                self.present()
            return False

        if delay_ms > 0:
            GLib.timeout_add(delay_ms, do_grab)
        else:
            do_grab()

    def _on_save(self, _button: Gtk.Button) -> None:
        if self.sel is None or self.screen_image is None:
            self.status_label.set_text("capture the screen and drag a rectangle first")
            return
        if self.ocr is not None and self._ocr_error is not None:
            # A known-broken OCR is worth refusing the save for, but waiting for
            # one that is merely still preparing is not: the region does not
            # depend on it, and this used to block the window until it finished.
            self.status_label.set_text(f"cannot use this region yet: {self._ocr_error}")
            return

        x, y, w, h = self.sel
        screen_w, screen_h = self.screen_size
        region = self._region_from_selection()
        if region is None:
            return
        self.config.capture.region = region
        path = self.config.save()
        self.status_label.set_text(f"saved to {path}")
        # A running panel is re-pointed at the new area rather than restarted:
        # restarting reloaded the model and rebuilt the card, so the translation
        # window jumped position and stopped working for a moment every time the
        # box was saved - and until it finished, the picker showed one box while
        # the pipeline read another.
        self._apply_region_live()
        print(f"region saved to {path}: x={x} y={y} w={w} h={h} (screen {screen_w}x{screen_h})")

    def _apply_region_live(self) -> None:
        """Point a running pipeline at the region in `config`, if we are watching."""
        if not getattr(self, "_watching", False):
            return
        panel = getattr(self, "_panel", None)
        worker = getattr(panel, "worker", None) if panel is not None else None
        if worker is None:
            return
        worker.request_region(self.config.capture.region)
        panel.status_label.set_text("reading the new box")

    def close_pipeline(self) -> None:
        if self._translator is not None:
            self._translator.close()
            self._translator = None
