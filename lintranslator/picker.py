"""Visual region picker: drag a rectangle over a screenshot of the screen."""
from __future__ import annotations

import threading
import time
from io import BytesIO
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
from PIL import Image  # noqa: E402

from .config import Config, Region  # noqa: E402
from .ocr import (  # noqa: E402
    PSM_NAMES,
    TesseractOcr,
    otsu_threshold,
    threshold_value,
)
from .occlusion import GUARD  # noqa: E402
from .selection import HANDLE_WIDGET_MIN, SelectionMath  # noqa: E402

HANDLE = 8  # px grab tolerance for dragging an existing edge
# preview magnification, not the recipe's OCR upscale
PREVIEW_SCALE = 3
PICKER_WINDOW = "picker"
# Why reading is paused while this window is up. "Press Apply box" is the one
# instruction that works everywhere: a compositor-side minimise is invisible to
# GTK on Wayland, so telling the user to minimise would be a dead end there.
PICKER_ON_SCREEN = (
    "the region picker is on screen — press Apply box to keep translating"
)
# ms to wait for the compositor to unmap this window before grabbing, so the
# picker is not captured as part of the screenshot
CAPTURE_DELAY_MS = 1000


class RegionPicker(Gtk.ApplicationWindow):
    """Pick a rectangle; preview its OCR; save it as a fraction region."""

    def __init__(
        self,
        app: Gtk.Application,
        config: Config,
        screenshot_png: bytes | None = None,
        from_file: str | None = None,
    ):
        super().__init__(application=app, title="LinTranslator — select the dialogue box")
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        if monitor is not None:
            geo = monitor.get_geometry()
            self.set_default_size(
                max(900, min(1500, int(geo.width * 0.78))),
                max(600, min(980, int(geo.height * 0.78))),
            )
        else:
            self.set_default_size(1320, 820)

        self.add_css_class("lintranslator-app")

        self.config = config
        self.screen_image: Image.Image | None = None
        self.screen_size: tuple[int, int] = (0, 0)

        self._ocr_built_from = self._ocr_kwargs()
        self.ocr = self._build_ocr()
        # until this is set the preview must not call into tesseract: doing the
        # first fetch on the main loop froze the whole window
        self._ocr_ready = threading.Event()
        self._ocr_error: Exception | None = None
        self._prepare_ocr_async()

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

        self.connect("close-request", self._on_close_request)

        # This window must not be captured: report whether it is on screen so the
        # pipeline pauses instead of reading its own UI. The guard is re-derived
        # from the window's state on every one of these events rather than being
        # toggled by whichever signal arrived, so a missed or late event cannot
        # leave reading paused with nothing on screen to explain it.
        self.connect("map", lambda *_: self._sync_guard())
        self.connect("unmap", lambda *_: self._sync_guard())
        self.connect("notify::visible", lambda *_: self._sync_guard())

        self._build()
        self._set_watching(False)
        self._prepare_panel()
        if from_file:
            self.set_screenshot(Path(from_file).read_bytes())
        elif screenshot_png:
            self.set_screenshot(screenshot_png)

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
            x, y, w, h = self.sel
            self.sel = (
                max(0, min(x, self.screen_size[0] - 1)),
                max(0, min(y, self.screen_size[1] - 1)),
                max(1, min(w, self.screen_size[0] - x)),
                max(1, min(h, self.screen_size[1] - y)),
            )
            GLib.idle_add(self._refresh_preview)
        self.area.queue_draw()

    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # must expand or GTK keeps the box at its natural size and centres it
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
        # FILL is required: a DrawingArea's natural size is tiny, so without it
        # the canvas can be allocated a stale, much smaller size
        self.area.set_halign(Gtk.Align.FILL)
        self.area.set_valign(Gtk.Align.FILL)
        # without a minimum width the sidebar claims the whole window and the
        # canvas is allocated 0px wide, rendering nothing at all
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

        # must be wrapped: a wrapping label reports its unwrapped width as its
        # natural size, which would starve the canvas
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
        """A 1px rule whose colour and spacing come from the theme."""
        rule = Gtk.Box()
        rule.add_css_class("lintranslator-rule")
        return rule

    def _build_toolbar(self) -> Gtk.Widget:
        """The window's one control row: status, then every action."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("lintranslator-toolbar")

        # ellipsised, not wrapped: this row is a fixed height and a two-line
        # status would make the whole window jump
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

        settings = Gtk.Button(label="Settings")
        settings.set_tooltip_text("Backend, model, API key and prompt")
        settings.connect("clicked", lambda *_: self._open_settings())
        bar.append(settings)

        self.quit_btn = Gtk.Button(label="Quit")
        self.quit_btn.set_tooltip_text("Quit the picker and stop translating")
        self.quit_btn.connect("clicked", self._on_quit_clicked)
        bar.append(self.quit_btn)

        spacer = Gtk.Box()
        spacer.set_size_request(14, -1)
        bar.append(spacer)

        self.watch_btn = Gtk.Button(label="Start")
        self.watch_btn.set_tooltip_text(
            "Save this region, open the panel and translate each new line"
        )
        self.watch_btn.connect("clicked", lambda *_: self._on_start())
        bar.append(self.watch_btn)

        for button in (self.capture_btn, settings, self.quit_btn, self.watch_btn):
            button.add_css_class("lintranslator-btn")
            button.add_css_class("lintranslator-tool")
        self.watch_btn.add_css_class("lintranslator-primary")
        return bar

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.add_css_class("lintranslator-side")
        # 340px width request, not a floor - a child's minimum can still exceed it
        side.set_size_request(340, -1)
        side.set_valign(Gtk.Align.FILL)

        hint = Gtk.Label(
            label=(
                "A box that is slightly too short still produces plausible OCR "
                "while silently dropping a line."
            ),
            xalign=0,
            wrap=True,
        )
        hint.add_css_class("lintranslator-hint")
        side.append(hint)

        self.coords_label = Gtk.Label(label="no selection", xalign=0, wrap=True)
        self.coords_label.add_css_class("lintranslator-readout")
        self.coords_label.add_css_class("lintranslator-dim")
        side.append(self.coords_label)

        self.preview_choice = Gtk.DropDown.new_from_strings(
            ["Preview: raw", "Preview: OCR input", "Preview: threshold"]
        )
        self.preview_choice.set_selected(1)
        self.preview_choice.set_tooltip_text(self._preview_explanation(1))
        self.preview_choice.connect("notify::selected", lambda *_: self._refresh_preview())
        side.append(self.preview_choice)

        side.append(self._rule())

        # height only: a width request here would be a floor under the whole sidebar
        self.preview = Gtk.Picture()
        self.preview.set_size_request(-1, 110)
        self.preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        # NOT hexpand: expansion propagates up through the sidebar and its
        # scroller, so the column splits the spare width with the canvas
        self.preview.set_hexpand(False)
        # a Picture's natural width is the image's own, so cap it with a scroller
        preview_clip = Gtk.ScrolledWindow()
        preview_clip.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        preview_clip.set_propagate_natural_width(False)
        preview_clip.set_propagate_natural_height(False)
        preview_clip.set_child(self.preview)
        preview_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        preview_frame.add_css_class("lintranslator-frame")
        preview_frame.append(preview_clip)
        side.append(preview_frame)

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
        note = Gtk.Label(label="preview only", xalign=0)
        note.add_css_class("lintranslator-caption")
        # not hexpand: expansion would propagate up and cost the canvas half the
        # window's spare width
        trans_head.append(note)
        self.translate_btn = Gtk.Button(label="Translate")
        self.translate_btn.add_css_class("lintranslator-btn")
        self.translate_btn.set_tooltip_text(
            "Translate this region now, using the configured backend"
        )
        self.translate_btn.connect("clicked", self._on_translate_clicked)
        trans_head.append(self.translate_btn)
        side.append(trans_head)

        self.backend_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.backend_label.add_css_class("lintranslator-caption")
        self.backend_label.set_max_width_chars(34)
        side.append(self.backend_label)
        self._refresh_backend_label()

        self.watch_status = Gtk.Label(label="", xalign=0, wrap=True)
        self.watch_status.add_css_class("lintranslator-hint")
        self.watch_status.set_max_width_chars(34)
        side.append(self.watch_status)

        self.trans_label = Gtk.Label(label="", xalign=0, wrap=True)
        self.trans_label.set_selectable(True)
        self.trans_label.set_max_width_chars(34)
        self.trans_label.add_css_class("lintranslator-readout")
        self.trans_label.add_css_class("lintranslator-body")
        side.append(self.trans_label)

        # a wrapping label's natural width is its unwrapped width, so cap it
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


    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        self._drag_mode = self._hit_test(x, y)
        self._drag_origin = (x, y)
        self._drag_last = (x, y)
        # the box this drag started from, so a resize edge cannot drift
        self._drag_sel = self.sel
        if self._drag_mode == "new":
            sx, sy = self._widget_to_screen(x, y)
            self.sel = (sx, sy, 1, 1)

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self._drag_origin is None or self._drag_mode is None or self.screen_image is None:
            return
        ox, oy = self._drag_origin
        # GestureDrag offsets are cumulative from the drag start; a move must
        # shift by the delta since the last update or it would run away from the
        # pointer
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
        # re-point a running pipeline, but never save the config here -
        # `_on_start` is the only writer
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

    def _on_draw(self, area, cr, width: int, height: int) -> None:
        # use the real allocation: the callback's size can be the DrawingArea's
        # natural (requested) size
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

        # full-resolution surface under a cairo transform, so the image always
        # fills exactly the rect the selection maths uses
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

            # handles at the corners and mid-edges, sized to the pointer tolerance
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
        """PIL image -> Gdk.Texture; Gtk.Picture rejects a GdkPixbuf."""
        if image.mode != "RGB":
            image = image.convert("RGB")
        return Gdk.MemoryTexture.new(
            image.width,
            image.height,
            Gdk.MemoryFormat.R8G8B8,
            GLib.Bytes.new(image.tobytes()),
            image.width * 3,
        )

    def _ocr_progress(self, message: str) -> None:
        """Show preparation progress from the setup thread via the main loop."""
        GLib.idle_add(self.status_label.set_text, message)

    def _ocr_kwargs(self) -> dict:
        """The config values the OCR engine is built from."""
        ocr = self.config.ocr
        return {
            "langs": ocr.langs,
            "psm": ocr.psm,
            "upscale": ocr.upscale,
            "autocontrast": ocr.autocontrast,
            "invert": ocr.invert,
            "threshold": ocr.threshold,
            "tessdata_dir": ocr.tessdata_dir,
            "min_confidence": ocr.min_confidence,
            "allow_unverified_tessdata": ocr.allow_unverified_tessdata,
        }

    def _build_ocr(self) -> TesseractOcr:
        """A new engine for the current config. Building is cheap; preparing is not."""
        return TesseractOcr(on_progress=self._ocr_progress, **self._ocr_kwargs())

    def _prepare_ocr_async(self) -> None:
        """Prepare the current engine off the main loop, re-runnable for a rebuilt one."""
        self._ocr_ready.clear()
        # a retry must clear an earlier failure, or `_ocr_blocked_reason` would
        # report it forever
        self._ocr_error = None
        # pass the engine in: a rebuild in flight must not be attributed to the
        # wrong engine
        engine = self.ocr
        threading.Thread(
            target=self._prepare_ocr,
            args=(engine,),
            name="lintranslator-ocr-setup",
            daemon=True,
        ).start()

    def _rebuild_ocr(self) -> None:
        """Install the engine the current config describes, rebuilt when Settings apply."""
        settings = self._ocr_kwargs()
        if settings == self._ocr_built_from:
            GLib.idle_add(self._repreview_if_selected)
            return
        self._ocr_built_from = settings
        self.ocr = self._build_ocr()
        self._prepare_ocr_async()

    def _prepare_ocr(self, ocr: TesseractOcr) -> None:
        """Fetch and validate the language data off the main loop before the first read."""
        try:
            ocr.ensure_ready()
        except Exception as exc:  # noqa: BLE001 - surfaced through the status row
            if ocr is self.ocr:
                self._ocr_error = exc
                GLib.idle_add(self.status_label.set_text, f"OCR unavailable: {exc}")
        finally:
            if ocr is self.ocr:
                self._ocr_ready.set()
                GLib.idle_add(self._repreview_if_selected)

    def _repreview_if_selected(self) -> bool:
        """Run the preview that was skipped while preparation was in flight."""
        if self.sel is not None and self.screen_image is not None:
            self._schedule_preview()
        return False

    def _ocr_blocked_reason(self) -> str | None:
        """Why OCR cannot run yet, or None when it can."""
        if self._ocr_error is not None:
            return f"OCR unavailable: {self._ocr_error}"
        if not self._ocr_ready.is_set():
            return "preparing OCR language data…"
        return None

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
            mode = self.preview_choice.get_selected()
            self.preview.set_paintable(
                self._to_texture(self._preview_image(crop, mode))
            )
            self.preview_choice.set_tooltip_text(self._preview_explanation(mode))
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

        shown, caption = self._readout(result)
        self.ocr_label.set_text(shown)
        self.conf_label.set_text(caption)

        if result.lines:
            self._last_ocr_text = result.text
            self._translate_async(result.text, debounce_ms=700)

    def _readout(self, result) -> tuple[str, str]:
        """What the OCR pane and its caption say about one read."""
        if result.lines:
            caption = f"confidence {result.confidence:.0f}   {len(result.lines)} line(s)"
            if result.dropped:
                caption += f"   {result.dropped} dropped"
            return result.display_text, caption
        if result.rejected:
            best = max(line.confidence for line in result.rejected)
            return (
                "\n".join(line.text for line in result.rejected if line.text),
                f"read {len(result.rejected)}, best {best:.0f}% — "
                f"under the {self.ocr.min_confidence:.0f}% gate",
            )
        return (
            "(no text found in this region)\ninput: "
            + ", ".join(self._recipe_steps())
            + " — layout: "
            + self._layout_label(),
            "",
        )

    def _layout_label(self) -> str:
        """The layout in force, as `ocr.PSM_NAMES` spells it."""
        return PSM_NAMES.get(int(self.ocr.psm), f"psm {self.ocr.psm}")

    def _recipe_steps(self) -> list[str]:
        """The recipe in force, as words, for the caption and the preview tooltip."""
        steps = ["grey"]
        if self.ocr.invert:
            steps.append("inverted")
        steps.append(
            "contrast stretched" if self.ocr.autocontrast else "contrast untouched"
        )
        cut = threshold_value(self.ocr.threshold)
        if cut:
            steps.append(f"cut at {cut}")
        return steps

    def _preview_image(self, crop: Image.Image, mode: int) -> Image.Image:
        """What the preview shows: 0 raw crop, 1 OCR input, 2 threshold, at `PREVIEW_SCALE`."""
        if mode == 2:
            view = self._cut_into_ink(crop)
        elif mode == 1:
            view = self.ocr.prepared(crop)
        else:
            view = crop
        target = (
            max(1, crop.width * PREVIEW_SCALE),
            max(1, crop.height * PREVIEW_SCALE),
        )
        if view.size != target:
            view = view.resize(target, Image.LANCZOS)
        return view.convert("RGB")

    def _cut_into_ink(self, crop: Image.Image) -> Image.Image:
        """The OCR input cut into ink and paper, at the configured cut or tesseract's own."""
        prepared = self.ocr.prepared(crop)
        if threshold_value(self.ocr.threshold):
            return prepared
        cut = otsu_threshold(prepared)
        return prepared.point(lambda p: 255 if p > cut else 0)

    def _preview_explanation(self, mode: int) -> str:
        """The tooltip for the preview chooser: what this view is, in the recipe's terms."""
        if mode == 0:
            return "The crop as it was captured."
        steps = self._recipe_steps()
        cut = threshold_value(self.ocr.threshold)
        if mode == 1:
            steps.append(f"cut at {cut}" if cut else "left grey for tesseract's own cut")
            return "Exactly what OCR is handed: " + ", ".join(steps) + "."
        if cut:
            return (
                f"The OCR input cut into ink and paper at {cut} — the same picture "
                "as the OCR input, because that cut is already applied there."
            )
        return (
            "The OCR input cut into ink and paper at the level tesseract would "
            "pick for itself. Set a cut in Settings to override it."
        )

    def _on_start(self) -> None:
        """Save the region, then open the panel and leave the screen before reading starts."""
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

        if getattr(self, "_panel", None) is None:
            self._prepare_panel()
        panel = self._panel
        panel.config = self.config
        panel.present()
        if panel.worker is None:
            panel.start_pipeline()
        else:
            panel.worker.request_region(region)
        panel.toggle_btn.set_label("Pause")
        self._set_watching(True)
        self._minimise_for_watching()

    def _sync_guard(self) -> None:
        """Tell the pipeline whether this window can appear in a capture.

        Asked of the window itself instead of trusting `unmap` alone: the pause
        belongs to the state the window is in now, not to the last signal GTK
        happened to deliver.
        """
        if self._is_on_screen():
            GUARD.set_mapped(PICKER_WINDOW, True, PICKER_ON_SCREEN)
        else:
            GUARD.clear(PICKER_WINDOW)
        self._refresh_watch_status()

    def _is_on_screen(self) -> bool:
        """Whether this window's surface can be captured right now."""
        return bool(self.get_visible() and self.get_mapped())

    def _minimise_for_watching(self) -> None:
        """Get this window off the screen; minimise is not honoured everywhere."""
        self.minimize()
        # 150ms: time for a compositor that honours minimise to unmap the window
        GLib.timeout_add(150, self._ensure_off_screen)

    def _ensure_off_screen(self) -> bool:
        """Fallback: hide the window if it is somehow still on screen."""
        if self._watching and self._is_on_screen():
            self.set_visible(False)
            # Hiding it is what lifts the pause, and saying so from the state
            # rather than waiting for `unmap` is the point of this method.
            self._sync_guard()
        return False

    def restore(self) -> None:
        """Bring the picker back, re-grabbing the screen so the box can be reframed."""
        if hasattr(self, "unminimize"):
            self.unminimize()
        self._refresh_watch_status()
        # wait only when still on screen: the window must leave it first or the
        # grab would contain it
        if self._is_on_screen():
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
        # disconnect first: the panel's own close handler would otherwise re-show
        # a picker that is going away
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
        # the panel closing while this window is off screen would leave nothing
        # visible at all, so bring the picker back
        if not self.get_visible():
            self.restore()
        return False  # let the panel actually close

    def _prepare_panel(self) -> None:
        """Build the translation card, wired to this window but not shown."""
        from .panel import TranslatorPanel

        self._panel = TranslatorPanel(self.get_application(), self.config, position=None)
        self._panel.connect("close-request", self._on_panel_closed)
        self._panel.gate = GUARD.reason
        self._panel.enable_region_button(self.restore)

    def _set_watching(self, watching: bool) -> None:
        """Reflect whether the panel is running, so the two windows agree."""
        self._watching = watching
        if watching:
            self.watch_btn.set_label("Apply box")
        else:
            self.watch_btn.set_label("Start")
        self._refresh_watch_status()

    def _refresh_watch_status(self) -> None:
        """Say whether the box is being read, and why not when it is not."""
        if not getattr(self, "_watching", False):
            self.watch_status.set_text("")
            return
        if self._is_on_screen():
            self.watch_status.set_text(
                "PAUSED — this window is on screen, so the box is not being read "
                "(it would be captured too). Press Apply box to get it out of the way."
            )
        elif self.get_visible():
            self.watch_status.set_text(
                "LIVE — this window is off screen and the box is being read. "
                "Press Select region on the panel to re-capture and bring it back."
            )
        else:
            self.watch_status.set_text(
                "LIVE — this window is hidden while watching. Press Select region on "
                "the panel to re-capture and bring it back."
            )

    def _on_quit_clicked(self, _button: Gtk.Button) -> None:
        """The toolbar's Quit, which is the panel's Quit by another name."""
        self._shutdown_panel()
        self.close()

    def _on_close_request(self, _window) -> bool:
        """Stop the panel and its worker, then quit: a hidden GTK window keeps the app alive."""
        self._shutdown_panel()
        GUARD.clear(PICKER_WINDOW)
        application = self.get_application()
        if application is not None:
            GLib.idle_add(application.quit)
        return False  # allow the picker itself to close

    def _open_settings(self) -> None:
        from .settings import SettingsDialog

        self._settings_dialog = SettingsDialog(
            self, self.config, on_apply=self._on_settings_applied
        )
        self._settings_dialog.present()

    def _on_settings_applied(self) -> None:
        """Install the saved settings: the OCR engine, the translator, the preview."""
        self._rebuild_ocr()
        if self._translator is not None:
            self._translator.close()
            self._translator = None
        self._refresh_backend_label()
        if self._last_ocr_text:
            self._translate_async(self._last_ocr_text, debounce_ms=0)

    def _refresh_backend_label(self) -> None:
        t = self.config.translate
        model = t.model or "(no model set)"
        if len(model) > 34:
            model = model[:31] + "…"
        self.backend_label.set_text(f"backend: {t.backend} · {model}")

    def _on_translate_clicked(self, _button: Gtk.Button) -> None:
        if not self._last_ocr_text:
            self.trans_label.set_text("no text in the region to translate")
            return
        self._translate_async(self._last_ocr_text, debounce_ms=0)

    def _translate_async(self, text: str, debounce_ms: int = 0) -> None:
        """Translate `text` on a worker thread, debounced and token-guarded."""
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

    def _on_capture(self, _button: Gtk.Button) -> None:
        """Re-grab the screen; the picker window itself must not be captured."""
        self._capture_screen(
            delay_ms=CAPTURE_DELAY_MS,
            note="capturing in 1s — switch to the game now…",
        )

    def _capture_screen(self, delay_ms: int, note: str) -> None:
        """Grab a fresh screenshot, hiding this window first and always restoring it."""
        from .capture import ScreenGrabber

        self.capture_btn.set_sensitive(False)
        self.status_label.set_text(note)
        panel = getattr(self, "_panel", None)
        if getattr(panel, "status_label", None) is not None:
            panel.status_label.set_text("capturing the screen for the picker…")
        self.set_visible(False)

        def do_grab() -> bool:
            try:
                # The screenshot portal on purpose, whatever `capture.backend`
                # says: this is the one grab the user asked for and is watching,
                # and it is why picking a region works before any screen-sharing
                # consent has been given.
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
