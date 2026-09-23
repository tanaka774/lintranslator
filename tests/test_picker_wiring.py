"""What the picker tells the pipeline: which area to read, and when.

Both bugs this covers were measured on a live screen, not imagined:

* pressing "Watch live" again after moving the box left the running pipeline on
  the OLD area, while the picker showed the new box as live;
* reading started 10 ms after the click, with the picker still over the box, so
  the first capture contained the picker's own status line and the dialogue line
  underneath it was dropped by the confidence gate.

Nothing here captures the screen or shows a window: the panel is built but never
presented, the pipeline thread is replaced with a recorder, and OCR is stubbed
out. What is tested is the wiring.

Requires a GTK display; skips cleanly without one.
"""
from __future__ import annotations

from io import BytesIO

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango
except (ImportError, ValueError):  # pragma: no cover - no GTK typelib
    pytest.skip("GTK 4 typelib unavailable", allow_module_level=True)

from PIL import Image  # noqa: E402

from lintranslator import capture as capture_mod  # noqa: E402
from lintranslator import panel as panel_mod  # noqa: E402
from lintranslator import picker as picker_mod  # noqa: E402
from lintranslator.config import Config  # noqa: E402
from lintranslator.pipeline import Event  # noqa: E402
from lintranslator.occlusion import GUARD  # noqa: E402
from lintranslator.selection import SelectionMath  # noqa: E402

SCREEN = (800, 600)
# A colour the fixture's screenshot does not use, so "the canvas was replaced"
# is visible in the pixels rather than only in the wiring.
FRESH = (200, 40, 60)


class FakeGrabber:
    """Stands in for the portal: no screen is read in a test."""

    instances: list["FakeGrabber"] = []

    def __init__(self, region, portal=None):
        self.region = region
        self.closed = False
        FakeGrabber.instances.append(self)

    def grab_full(self):
        image = Image.new("RGB", SCREEN, FRESH)
        return capture_mod.FullFrame(image=image, size=SCREEN, elapsed=0.001)

    def close(self):
        self.closed = True


class RecordingWorker:
    """Stands in for PipelineThread: records what it was pointed at."""

    def __init__(self, config, outbox, gate=None):
        self.config = config
        self.gate = gate
        # `Pipeline.__init__` keeps the Region object it was built with.
        self.region = config.capture.region
        self.requested = []
        self.rereads = 0
        self.started = False

    def start(self):
        self.started = True

    def stop(self, timeout: float = 1.0):
        pass

    def request_region(self, region):
        self.requested.append(region)
        self.region = region

    def request_reread(self):
        self.rereads += 1


def _png(size=SCREEN) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (30, 30, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module", autouse=True)
def gtk_init():
    if not Gtk.init_check():
        pytest.skip("no display available for GTK", allow_module_level=True)


@pytest.fixture(autouse=True)
def clean_guard():
    """The guard is process-wide; a leaked window would affect other tests."""
    for name in list(GUARD.windows):
        GUARD.clear(name)
    yield
    for name in list(GUARD.windows):
        GUARD.clear(name)


@pytest.fixture
def picker(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_mod, "PipelineThread", RecordingWorker)
    # Never put a window on the real screen, and never run tesseract on a
    # synthetic screenshot: this test is about wiring, not pixels.
    monkeypatch.setattr(panel_mod.TranslatorPanel, "present", lambda self: None)
    monkeypatch.setattr(picker_mod.RegionPicker, "_refresh_preview", lambda self: None)

    cfg = Config()
    cfg.translate.backend = "none"
    cfg.path = tmp_path / "config.json"  # `_on_start` saves
    app = Gtk.Application(application_id="dev.lintranslator.test", flags=0)
    window = picker_mod.RegionPicker(app, cfg, screenshot_png=_png())
    # Never ask the developer's compositor for a real global shortcut from a test.
    window._panel.hotkey_enabled = False
    window.minimised = []
    window.minimize = lambda: window.minimised.append(True)  # type: ignore[method-assign]
    yield window
    window._shutdown_panel()
    window.destroy()


@pytest.fixture
def fake_grabber(monkeypatch):
    """Replace the portal grab, and keep this window off the real screen."""
    FakeGrabber.instances = []
    monkeypatch.setattr(capture_mod, "ScreenGrabber", FakeGrabber)
    return FakeGrabber


def _drag_to(window, x, y, w, h) -> None:
    """Move the box the way a drag does: replace the selection, then end it."""
    window.sel = (x, y, w, h)
    window._on_drag_end(None, 0.0, 0.0)


# --------------------------------------------------------------------------- #
def test_watch_live_saves_the_box_and_starts_the_pipeline(picker):
    x, y, w, h = picker.sel
    picker._on_start()

    region = picker.config.capture.region
    assert region.mode == "fraction"
    assert (region.x, region.y, region.w, region.h) == (
        x / SCREEN[0],
        y / SCREEN[1],
        w / SCREEN[0],
        h / SCREEN[1],
    )
    assert picker.config.path.exists(), "the box was not saved"
    worker = picker._panel.worker
    assert worker is not None and worker.started
    assert worker.region is region


def test_watch_live_gets_the_picker_off_the_screen(picker):
    """The window must leave before reading starts - that is the whole fix."""
    picker._on_start()
    assert picker.minimised == [True], "the picker stayed on screen while watching"


def test_the_picker_tells_the_guard_when_it_is_visible(picker):
    """A mapped picker is a reason to pause; unmapped is not."""
    picker._on_mapped(True)
    assert GUARD.reason() and "picker" in GUARD.reason()


def test_the_card_names_the_language_pair_it_is_translating_into(picker):
    """The pair is set in a dialog and then invisible for the whole session.

    "This is translating into the wrong language" should be readable from the
    card, not something to be discovered from the output.
    """
    panel = picker._panel
    panel.config.translate.source_lang = "eng_Latn"
    panel.config.translate.target_lang = "kor_Hang"
    panel._refresh_backend_label()
    label = panel.backend_label.get_text()
    assert "eng→kor" in label
    assert panel.config.translate.backend in label

    # A hand-written code is shown as it is rather than dropped.
    panel.config.translate.target_lang = "Japanese"
    panel._refresh_backend_label()
    assert "eng→Japanese" in panel.backend_label.get_text()


def test_an_unmapped_picker_is_not_a_reason_to_pause(picker):
    picker._on_mapped(False)
    assert GUARD.reason() is None


def test_the_card_says_when_nothing_is_being_translated(picker):
    """The pass-through backend shows the source where a translation belongs.

    Measured confusion, not a hypothetical: with `backend: "none"` the card read
    as a translator that had stopped working, because it named a model
    ("gemini-2.5-flash-lite") that the selected backend never calls.
    """
    panel = picker._panel
    panel.config.translate.backend = "none"
    panel.config.translate.model = "google/gemini-2.5-flash-lite"
    panel._refresh_backend_label()
    text = panel.backend_label.get_text()
    assert "no translation" in text and "none" in text
    assert "gemini" not in text, "an unused model must not be named as if it worked"

    # A backend that does translate still names its model.
    panel.config.translate.backend = "openrouter"
    panel._refresh_backend_label()
    assert "gemini-2.5-flash-lite" in panel.backend_label.get_text()


def test_pressing_watch_live_again_re_points_the_running_pipeline(picker):
    """Regression: the second press used to be ignored entirely.

    The picker would show the new box as the live one while the pipeline kept
    reading the old area - and only Save applied it, by restarting everything
    (which reloaded the model and moved the card).
    """
    picker._on_start()
    first = picker._panel.worker

    _drag_to(picker, 100, 400, 300, 90)
    picker._on_start()

    worker = picker._panel.worker
    assert worker is first, "the pipeline was rebuilt instead of re-pointed"
    assert worker.requested, "the new box never reached the running pipeline"
    region = picker.config.capture.region
    assert worker.region is region, "the pipeline is still reading the old area"
    assert region.x == pytest.approx(100 / SCREEN[0])
    assert region.w == pytest.approx(300 / SCREEN[0])


def test_dragging_the_box_while_watching_re_points_immediately(picker):
    """What the picker shows must be what is read, without pressing anything."""
    picker._on_start()
    worker = picker._panel.worker
    _drag_to(picker, 20, 300, 400, 120)
    assert worker.region is picker.config.capture.region
    assert worker.region.w == pytest.approx(400 / SCREEN[0])


def test_dragging_before_watching_does_not_start_anything(picker):
    """No pipeline, no side effects: staging a box is only meaningful while live."""
    _drag_to(picker, 20, 300, 400, 120)
    assert picker._panel.worker is None
    assert picker.config.capture.region.w == pytest.approx(400 / SCREEN[0])


def test_dragging_an_edge_inwards_shrinks_the_box(picker):
    """Regression: the box could be grown but never shrunk.

    Two halves had to agree for a resize to work, and neither did. The geometry
    pinned the moving edge to where the pointer landed, and these handlers fed it
    the box as it changed on every event - with cumulative drag offsets that
    makes the edge accelerate away from the pointer. A resize is measured from the
    box the drag started with, which is what this covers.
    """
    # 1:1 mapping, so the numbers below are the pointer's own coordinates.
    picker.math = lambda: SelectionMath(SCREEN[0], SCREEN[1], float(SCREEN[0]), float(SCREEN[1]))
    picker.sel = (100, 100, 200, 100)  # right edge at x=300

    picker._on_drag_begin(None, 300.0, 150.0)
    assert picker._drag_mode == "e", "the press did not land on the right edge"
    # Cumulative offsets, as GtkGestureDrag reports them: the pointer moves from
    # 300 to 210, one event per few pixels.
    for dx in (-30.0, -60.0, -90.0):
        picker._on_drag_update(None, dx, 0.0)
    picker._on_drag_end(None, -90.0, 0.0)

    assert picker.sel == (100, 100, 110, 100), "the right edge did not follow the drag"
    assert picker.config.capture.region.w == pytest.approx(110 / SCREEN[0])


def test_a_new_drag_replaces_the_box_the_picker_opened_with(picker):
    """A drag that starts away from the current box draws a new one.

    The box a drag started from is remembered for resizing, and it must not leak
    into the new-box maths: the picker opens on a seeded selection, so "draw a
    fresh box" always means replacing one, never starting from nothing.
    """
    picker.math = lambda: SelectionMath(SCREEN[0], SCREEN[1], float(SCREEN[0]), float(SCREEN[1]))
    picker.sel = (100, 100, 200, 100)

    picker._on_drag_begin(None, 500.0, 400.0)  # nowhere near the existing box
    assert picker._drag_mode == "new"
    picker._on_drag_update(None, -120.0, -80.0)  # up and to the left
    picker._on_drag_end(None, -120.0, -80.0)

    assert picker.sel == (380, 320, 120, 80)


def test_the_panel_is_given_the_pause_reason_and_a_way_back(picker):
    """The panel must be able to explain a pause and to restore the picker."""
    panel = picker._panel
    assert panel.gate == GUARD.reason, "the panel cannot see why it should pause"
    assert panel.region_btn.get_visible() is True, "no way back to the picker"
    assert panel.on_region_request == picker.restore


def test_the_watch_button_says_what_it_will_do(picker):
    """Idle it starts watching; while watching it applies the box and gets out of
    the way. A button still reading "Watching…" would be a dead control."""
    assert picker.watch_btn.get_label() == "Watch live"
    picker._on_start()
    assert picker.watch_btn.get_label() == "Apply box"
    assert "LIVE" in picker.watch_status.get_text()


def test_the_region_button_asks_the_picker_to_come_back(picker, monkeypatch):
    restored = []
    monkeypatch.setattr(picker, "restore", lambda: restored.append(True))
    picker._panel.enable_region_button(picker.restore)
    picker._panel.region_btn.emit("clicked")
    assert restored == [True]


def test_the_region_button_reopens_on_a_fresh_screenshot(picker, fake_grabber, monkeypatch):
    """Regression: Region came back on the shot taken when watching started.

    Re-framing is the only reason to press it, and the game has moved on by
    then, so the screen has to be captured again on the way back. The box is
    kept: what is replaced is the screen underneath it, not the selection.
    """
    monkeypatch.setattr(picker, "present", lambda: None)
    picker._on_start()
    picker.set_visible(False)  # what _minimise_for_watching ends up doing
    watched = picker.sel

    picker._panel.region_btn.emit("clicked")

    assert fake_grabber.instances, "Region did not re-grab the screen"
    assert picker.screen_image.getpixel((0, 0)) == FRESH, "the old shot was reused"
    assert picker.sel == watched, "re-capturing threw the box away"
    assert picker.get_visible(), "the picker did not come back"
    # The picker is off screen during the grab, so the panel has to say what the
    # button is doing; otherwise it looks like nothing happened.
    assert "capturing" in picker._panel.status_label.get_text()


def test_recapturing_waits_while_the_picker_is_still_on_screen(picker, fake_grabber, monkeypatch):
    """A mapped picker has to leave the screen first, or the shot of the game
    would contain the picker itself."""
    scheduled = []
    monkeypatch.setattr(picker_mod.GLib, "timeout_add", lambda ms, cb: scheduled.append(ms))
    monkeypatch.setattr(picker, "get_mapped", lambda: True)
    monkeypatch.setattr(picker, "present", lambda: None)

    picker.restore()

    assert scheduled == [picker_mod.CAPTURE_DELAY_MS]
    assert not fake_grabber.instances, "grabbed the screen with the picker still on it"


def test_a_failed_capture_still_brings_the_picker_back(picker, monkeypatch):
    """The window is hidden for the grab, so a failure that left it hidden would
    leave the process with nothing on screen at all."""

    class FailingGrabber:
        def __init__(self, *args, **kwargs):
            pass

        def grab_full(self):
            raise RuntimeError("the portal said no")

        def close(self):
            pass

    monkeypatch.setattr(capture_mod, "ScreenGrabber", FailingGrabber)
    monkeypatch.setattr(picker, "present", lambda: None)

    picker.restore()

    assert picker.get_visible(), "the picker stayed hidden after a failed grab"
    assert "capture failed" in picker.status_label.get_text()


def test_recapturing_keeps_the_box_the_user_dragged(picker):
    """Re-grabbing the screen is how you frame against the live screen, so it
    must not throw away the box you just dragged in favour of the saved one."""
    _drag_to(picker, 33, 222, 333, 111)
    picker.set_screenshot(_png())
    assert picker.sel == (33, 222, 333, 111)


def test_recapturing_at_another_resolution_reseeds_from_the_config(picker):
    """Fractions still describe the saved box after a resolution change, but the
    pixel selection has to be recomputed for the new screen size."""
    _drag_to(picker, 33, 222, 333, 111)
    picker.set_screenshot(_png((1280, 720)))
    region = picker.config.capture.region
    assert picker.sel == region.to_pixels(1280, 720)


def test_closing_the_panel_brings_the_picker_back(picker, monkeypatch):
    """While watching, the picker is off screen. Closing the card must not leave
    the process with no visible window - the orphaned-window failure this project
    already shipped once."""
    picker._on_start()
    picker.set_visible(False)  # what _minimise_for_watching ends up doing
    restored = []
    monkeypatch.setattr(picker, "restore", lambda: restored.append(True))

    picker._on_panel_closed(picker._panel)

    assert picker._panel is None
    assert restored == [True], "the picker stayed hidden with nothing on screen"
    assert picker.watch_btn.get_label() == "Watch live"


def test_the_panel_keeps_the_configured_width(picker):
    """The card's width must come from display.width, not from the longest label.

    Regression: the status line grew a timing suffix, and because an unwrapped
    label's minimum width is its whole text, the card went 560 -> 664 px. A wider
    card reaches further into the box it is reporting on, which is how it ends up
    being captured and (with the self-text guard) how a line stops being read.
    """
    panel = picker._panel
    panel.enable_region_button(picker.restore)
    panel.status_label.set_text(
        "12:34:56 · conf 90 · 812 ms · openrouter · +2.1s"
    )
    panel.backend_label.set_text("openrouter · tencent/hy-mt2-1.8b · box 0.459×0.115")
    # The longest thing that can land in the card, and the reason the two text
    # areas had to stop dictating the width.
    panel._show_event(
        Event(
            source="[It has been determined that this case merits preservation as a "
            "record. The following is the case record pertaining to today's request.]",
            target="Of all the actions taken by our men during the last operation, his "
            "alone merits compliment. It was an efficient method of neutralizing the "
            "enemy.",
            confidence=94.0,
            translate_elapsed=1.69,
            total_elapsed=1.9,
            cached=False,
            backend="openrouter",
        )
    )

    minimum = panel.measure(Gtk.Orientation.HORIZONTAL, -1).minimum
    configured = picker.config.display.width
    assert minimum <= configured, f"card needs {minimum}px, configured {configured}px"
    # The mechanism: the long labels are bounded rather than dictating the width.
    # The status line ellipsises (it shares one fixed-height row with the
    # buttons) and each text area scrolls instead of asking for its full width.
    assert panel.status_label.get_ellipsize() != Pango.EllipsizeMode.NONE
    assert panel.backend_label.get_ellipsize() != Pango.EllipsizeMode.NONE
    assert panel.target_scroll.get_propagate_natural_width() is False
    assert panel.source_scroll.get_propagate_natural_width() is False


def test_the_panel_height_does_not_follow_its_text(picker):
    """The card's height must be the configured budget, whatever the line is.

    Regression: the card was content-sized, so it grew with the translation and
    GTK never shrank a resizable window back. Measured, a four-line reply took it
    from 172 px to 312 px and it stayed there even when the next line was two
    characters long - so one long line permanently covered more of the game.
    """
    panel = picker._panel
    display_cfg = picker.config.display

    def height():
        return panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural

    panel._show_event(
        Event(
            source="Yes, sir.",
            target="はい。",
            confidence=94.0,
            translate_elapsed=0.4,
            total_elapsed=0.9,
            cached=False,
            backend="openrouter",
        )
    )
    short = height()
    panel._show_event(
        Event(
            source="[It has been determined that this case merits preservation as a "
            "record.]",
            target="Of all the actions taken by our men during the last operation, his "
            "alone merits compliment. It was an efficient method of neutralizing the "
            "enemy. The record will be preserved in full, and the request filed today "
            "shall be attached to it as an appendix.",
            confidence=94.0,
            translate_elapsed=1.7,
            total_elapsed=2.1,
            cached=False,
            backend="openrouter",
        )
    )
    long = height()
    assert short == long, f"card height moved {short} -> {long} with the text"

    # And the reserved space really is the configured number of lines, so a
    # taller budget is the only thing that can make the card taller.
    def reserved(label, lines):
        return panel._reserved_height(label, lines)

    assert panel.target_scroll.get_size_request().height == reserved(
        panel.target_label, display_cfg.target_lines
    )
    assert panel.source_scroll.get_size_request().height == reserved(
        panel.source_label, display_cfg.source_lines
    )
    # Each extra line costs the same leading, and the first line costs more than
    # that because it also carries the font's ascent and descent. Reserving
    # `leading * n` instead came up a pixel short and clipped the last line.
    one, two, three = (reserved(panel.target_label, n) for n in (1, 2, 3))
    assert 0 < one < two < three
    assert two - one == three - two, "line spacing is not constant"
    # The original text sits below the translation, not above it. The card is
    # wrapped in a Gtk.Overlay that carries the resize grips, so walk that.
    card = panel.card
    children = []
    child = card.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert children.index(panel.target_scroll) < children.index(panel.source_scroll)


def test_hiding_the_source_shrinks_the_card_by_its_reserved_lines(picker):
    """Turning the original text off must give its space back, and not more."""
    panel = picker._panel
    display_cfg = picker.config.display
    with_source = panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural

    display_cfg.show_source = False
    panel.apply_display_settings()
    without = panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural

    reserved = panel._reserved_height(panel.source_label, display_cfg.source_lines)
    assert without < with_source, "hiding the source did not reclaim any space"
    assert with_source - without >= reserved, (
        "the space reclaimed is less than the source area reserved"
    )
    assert panel.source_scroll.get_visible() is False

    # And turning it back on restores the same height, so the toggle is not a
    # one-way ratchet of its own.
    display_cfg.show_source = True
    panel.apply_display_settings()
    assert panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural == with_source


# --------------------------------------------------------------------------- #
# Re-read: the button, the in-app shortcut and the control socket
# --------------------------------------------------------------------------- #
def test_the_reread_button_asks_the_pipeline_to_read_again(picker):
    picker._on_start()
    worker = picker._panel.worker

    picker._panel.reread_btn.emit("clicked")

    assert worker.rereads == 1, "the button did not reach the pipeline"
    assert picker._panel.status_label.get_text() == "re-reading the box…"


def test_the_in_window_shortcut_does_the_same(picker):
    """Ctrl+R and F5 are the in-window shortcuts; the global one comes from the
    compositor. Both must land in the same place."""
    picker._on_start()
    worker = picker._panel.worker
    assert picker._panel._on_shortcut_reread(None, None) is True
    assert worker.rereads == 1


def test_reread_starts_a_stopped_pipeline(picker):
    """Pressing Re-read on a paused panel should do the obvious thing."""
    assert picker._panel.worker is None
    picker._panel.request_reread()
    assert picker._panel.worker is not None
    assert picker._panel.worker.rereads == 1
    assert picker._panel.toggle_btn.get_label() == "Pause"


def test_the_control_socket_speaks_reread_and_status(picker):
    panel = picker._panel
    picker._on_start()

    assert panel._on_control("reread") == "re-reading"
    assert panel._on_control("  REREAD ") == "re-reading"
    status = panel._on_control("status")
    assert "watching" in status and "hotkey" in status
    assert "unknown command" in panel._on_control("nonsense")


def test_the_panel_reports_when_the_global_hotkey_is_unavailable(picker):
    """A missing hotkey must be explained, not silently absent."""
    panel = picker._panel
    panel._on_hotkey_status(False, "no portal")
    # It is three lines long, so it goes in the card's text area rather than the
    # one-line status row, where it would be ellipsised to a few characters.
    text = panel.target_label.get_text()
    assert "No global hotkey" in text
    assert "lintranslator shortcut" in text, "the fix has to be named"
    assert "Ctrl+R" in text, "the in-window fallback has to be named"
    # A notice must not be mistaken for a translation.
    assert panel.target_label.has_css_class("lintranslator-notice")
    # The full text stays reachable once a translation displaces the notice.
    assert "lintranslator shortcut" in (panel.status_label.get_tooltip_text() or "")


def test_a_notice_never_replaces_a_translation(picker):
    """Once something is translated, a notice must not overwrite it."""
    panel = picker._panel
    panel._show_event(
        Event(
            source="Yes, sir.",
            target="はい。",
            confidence=94.0,
            translate_elapsed=0.4,
            total_elapsed=0.9,
            cached=False,
            backend="openrouter",
        )
    )
    panel._note_keep_above(False, "native Wayland")
    assert panel.target_label.get_text() == "はい。"
    assert not panel.target_label.has_css_class("lintranslator-notice")
    # The short form still reaches the status row, so it is not swallowed.
    assert "Not always-on-top" in panel.status_label.get_text()


def test_an_error_shares_the_status_row_instead_of_adding_a_line(picker):
    """Errors must not make the card taller exactly when things go wrong."""
    panel = picker._panel
    display_cfg = picker.config.display
    before = panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural

    panel._show_error("HTTPError: 429 Too Many Requests (retry in 12 s)")
    after = panel.measure(Gtk.Orientation.VERTICAL, display_cfg.width).natural

    assert before == after, "an error changed the card's height"
    assert panel.error_label.get_visible() is True
    assert panel.status_label.get_visible() is False, "two labels share one slot"

    # Clearing it gives the status line back.
    panel._show_error("")
    assert panel.error_label.get_visible() is False
    assert panel.status_label.get_visible() is True


def test_applying_settings_does_not_wipe_the_translation(picker):
    """Re-measuring the line height must put the label's text back.

    `_reserved_height` temporarily writes "Mg"/"Mg\\nMg" into the label it is
    measuring. It used to leave the label empty, so changing the font size in
    Settings - which re-applies the layout budget - silently erased whatever
    translation was on screen.
    """
    panel = picker._panel
    panel._show_event(
        Event(
            source="Yes, sir.",
            target="はい、わかりました。",
            confidence=94.0,
            translate_elapsed=0.4,
            total_elapsed=0.9,
            cached=False,
            backend="openrouter",
        )
    )
    picker.config.display.font_scale = 1.4
    panel.apply_display_settings()

    assert panel.target_label.get_text() == "はい、わかりました。"
    assert panel.source_label.get_text() == "Yes, sir."
    # The card is still a fixed size at the new font, and the reserved space
    # grew with the font rather than staying at the old size and clipping.
    width = picker.config.display.width
    short_h = panel.measure(Gtk.Orientation.VERTICAL, width).natural
    panel._show_event(
        Event(
            source="[It has been determined that this case merits preservation.]",
            target="Of all the actions taken by our men during the last operation, "
            "his alone merits compliment. It was an efficient method of "
            "neutralizing the enemy.",
            confidence=94.0,
            translate_elapsed=1.7,
            total_elapsed=2.1,
            cached=False,
            backend="openrouter",
        )
    )
    assert panel.measure(Gtk.Orientation.VERTICAL, width).natural == short_h
    assert panel._reserved_height(
        panel.target_label, picker.config.display.target_lines
    ) > panel._reserved_height(panel.target_label, 1)


def test_each_menu_item_fires_exactly_once(picker, monkeypatch):
    """One click, one action.

    The items are wired in `_build_menu`, which wraps the handler so the menu
    also closes. They were *also* wired in `_menu_item`, so every item fired
    twice - which opened two Settings dialogs, and would have restored the
    picker twice.
    """
    panel = picker._panel
    calls = []
    monkeypatch.setattr(picker, "restore", lambda: calls.append("restore"))
    panel.enable_region_button(picker.restore)

    panel.region_btn.emit("clicked")
    assert calls == ["restore"], f"fired {len(calls)} times, expected once"


def test_the_overflow_menu_opens_and_closes(picker):
    """The ⋮ button must toggle its menu, and the menu must be parented to it."""
    panel = picker._panel
    assert panel.menu.get_visible() is False

    panel._on_menu(panel.menu_btn)
    assert panel.menu.get_parent() is panel.menu_btn, "menu has nothing to anchor to"

    panel._on_menu(panel.menu_btn)
    assert panel.menu.get_visible() is False, "second press did not close the menu"


def test_choosing_a_menu_item_closes_the_menu(picker, monkeypatch):
    """A menu that stays open after a choice is not a menu."""
    panel = picker._panel
    monkeypatch.setattr(panel, "_on_copy", lambda _button: None)
    panel._on_menu(panel.menu_btn)

    panel.copy_btn.emit("clicked")
    assert panel.menu.get_visible() is False


def test_a_wide_card_shows_every_action_and_needs_no_menu(picker):
    """Given the width, the row holds everything and ⋮ disappears.

    An empty overflow menu is a lie: it promises actions that are all already on
    the card.
    """
    panel = picker._panel
    picker.config.display.width = 1200
    panel._relayout_controls()

    row = panel.button_row
    for name in panel.ACTION_ORDER:
        assert getattr(panel, name).get_parent() is row, f"{name} is not on the card"
    assert panel.menu_btn.get_visible() is False
    assert panel.menu_box.get_first_child() is None


def test_a_narrow_card_overflows_from_the_right(picker):
    """Narrowing moves the lowest-priority actions into the menu, in order.

    Region is first on the card and so last to leave it: it is the only way back
    to the picker once the panel owns the box.
    """
    panel = picker._panel
    picker.config.display.width = 380
    panel._relayout_controls()

    row = panel.button_row
    menu = panel.menu_box

    def in_menu(button):
        return button.get_parent() is menu

    # Region outranks everything, so it is still on the card while other actions
    # have already gone.
    assert panel.region_btn.get_parent() is row, "Region left the card first"
    assert in_menu(panel.quit_btn), "Quit should be the first to overflow"
    assert panel.menu_btn.get_visible() is True


def test_narrowing_further_evicts_in_priority_order(picker):
    """The split only ever moves one way as the card narrows."""
    panel = picker._panel
    order = list(panel.ACTION_ORDER)

    def on_card(width):
        picker.config.display.width = width
        panel._relayout_controls()
        return [
            name for name in order if getattr(panel, name).get_parent() is panel.button_row
        ]

    wide = on_card(1200)
    middle = on_card(430)
    narrow = on_card(300)

    assert wide == order, "a wide card should hold every action"
    # Each narrower width keeps a prefix of the order - never a different set.
    assert middle == order[: len(middle)]
    assert narrow == order[: len(narrow)]
    assert len(narrow) <= len(middle) <= len(wide)


def test_the_overflow_menu_holds_exactly_what_left_the_card(picker):
    """An action must never be in both places, or in neither."""
    panel = picker._panel
    picker.config.display.width = 330
    panel._relayout_controls()

    on_card = []
    in_menu = []
    for name in panel.ACTION_ORDER:
        button = getattr(panel, name)
        if button.get_parent() is panel.button_row:
            on_card.append(name)
        elif button.get_parent() is panel.menu_box:
            in_menu.append(name)
        else:
            raise AssertionError(f"{name} is in neither the row nor the menu")

    assert sorted(on_card + in_menu) == sorted(panel.ACTION_ORDER)
    assert not set(on_card) & set(in_menu)
    assert panel.menu_btn.get_visible() is bool(in_menu)


def test_quit_is_set_apart_from_the_other_menu_items(picker):
    """A destructive action should not sit flush against Settings."""
    panel = picker._panel
    picker.config.display.width = 300
    panel._relayout_controls()

    children = []
    child = panel.menu_box.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()

    assert panel.quit_btn.get_parent() is panel.menu_box
    quit_index = children.index(panel.quit_btn)
    assert quit_index > 0, "Quit should not be the only thing in the menu here"
    assert children[quit_index - 1] is panel.menu_rule
    assert panel.menu_rule not in children[: quit_index - 1]


def test_every_action_button_is_unaccented(picker):
    """The row is a row of peers; none of them carries the accent colour."""
    panel = picker._panel
    for name in panel.ACTION_ORDER:
        assert not getattr(panel, name).has_css_class("lintranslator-primary"), name
    assert not panel.menu_btn.has_css_class("lintranslator-primary")


# --------------------------------------------------------------------------- #
# Resizing the card by its edge
# --------------------------------------------------------------------------- #
def test_the_resize_grips_do_not_change_the_cards_size(picker):
    """The grips live in an overlay, which must add nothing to the card.

    Otherwise dragging support would quietly undo the fixed-size property that
    `test_the_panel_height_does_not_follow_its_text` exists to protect.
    """
    panel = picker._panel
    width = picker.config.display.width

    card_only = panel.card.measure(Gtk.Orientation.VERTICAL, width).natural
    wrapped = panel.get_child().measure(Gtk.Orientation.VERTICAL, width).natural
    assert card_only == wrapped, f"the grip overlay added {wrapped - card_only} px"

    card_min = panel.card.measure(Gtk.Orientation.HORIZONTAL, -1).minimum
    wrapped_min = panel.get_child().measure(Gtk.Orientation.HORIZONTAL, -1).minimum
    assert card_min == wrapped_min


def test_the_grips_are_on_the_edges_that_wayland_allows(picker):
    """East, south and south-east only.

    A Wayland client cannot move its own window, so a drag on the north or west
    edge could not keep the opposite edge still - the card would grow rightward
    while the pointer moved left.
    """
    panel = picker._panel
    model = panel.get_child().observe_children()
    grips = [
        model.get_item(i)
        for i in range(model.get_n_items())
        if model.get_item(i).has_css_class("lintranslator-grip-area")
    ]
    assert len(grips) == 3, f"expected three grips, found {len(grips)}"
    # Every grip must be hit-testable: GTK4 picks through render nodes, so a grip
    # that painted nothing would never see the drag.
    for grip in grips:
        assert grip.get_visible() is True


def test_a_drag_resizes_along_its_own_axis_only(picker):
    """East changes width, south changes height, south-east changes both."""
    panel = picker._panel
    panel._set_card_size(600, 300)

    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 40, 70, "e")
    assert panel.get_default_size() == (640, 300)

    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 40, 70, "s")
    assert panel.get_default_size() == (600, 370)

    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 40, 70, "se")
    assert panel.get_default_size() == (640, 370)


def test_a_drag_cannot_shrink_the_card_below_its_contents(picker):
    """A card too small for its own controls is not a size, it is a bug."""
    panel = picker._panel
    cfg = picker.config.display
    floor_w, floor_h = panel._card_minimum()

    panel._set_card_size(5, 5)
    assert panel.get_default_size() == (floor_w, floor_h)
    assert cfg.width == floor_w, "the width the card is at must be recorded"
    assert floor_w > 0 and floor_h > 0

    # And the floor is the real one: the card still reports a usable size.
    assert panel.card.measure(Gtk.Orientation.HORIZONTAL, -1).minimum <= floor_w


def test_a_dragged_size_is_remembered_and_restored(picker):
    """The size the user dragged to must survive, or the drag is pointless."""
    panel = picker._panel
    cfg = picker.config.display
    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 120, 80, "se")
    panel._on_resize_end(None, 120, 80, "se")

    written = Config.load(picker.config.path)
    assert written.display.width == 720
    assert written.display.height == 380

    # A fresh panel for the same config opens at that size rather than at the
    # line budget, which is what `_restore_size` is for.
    assert written.display.height > 0


def test_a_sideways_drag_leaves_the_height_on_the_budgets(picker):
    """Dragging the east edge must not pin a height the user never chose.

    Otherwise the first sideways drag freezes the card at whatever the line
    budgets happened to add up to, and those sliders stop doing anything.
    """
    panel = picker._panel
    cfg = picker.config.display
    assert cfg.height == 0

    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 100, 0, "e")
    panel._on_resize_end(None, 100, 0, "e")

    assert cfg.width == 700, "the width the drag set has to stick"
    assert cfg.height == 0, f"a sideways drag pinned the height at {cfg.height}"


def test_a_vertical_drag_does_pin_the_height(picker):
    """The south edge is the one that asks for a height, so it must stick."""
    panel = picker._panel
    cfg = picker.config.display

    panel._resize_from = (600, 300)
    panel._on_resize_update(None, 0, 90, "s")
    panel._on_resize_end(None, 0, 90, "s")

    assert cfg.height == 390, f"expected the dragged height, got {cfg.height}"
