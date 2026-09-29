"""The ScreenCast backend: a live stream instead of a file per grab.

Nothing here needs a display, a compositor or PipeWire. The portal calls, the
GStreamer element and the frame source are all faked, so these pin the wiring:
which backend the loop reads from, what happens when the stream dies, and that a
bad `capture.backend` is refused instead of silently ignored.
"""
from __future__ import annotations

import os
import sys
from io import BytesIO

import pytest
from PIL import Image

from lintranslator import screencast
from lintranslator.capture import (
    BACKENDS,
    PORTAL_SCREENSHOT,
    SCREENCAST,
    ScreenGrabber,
    build_grabber,
)
from lintranslator.config import CaptureConfig, Config, Region
from lintranslator.screencast import ScreenCastError, ScreenCastStream


def _png_bytes(size=(8, 4)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class StubPortal:
    """Just enough of ScreenshotPortal for ScreenGrabber to fall back to."""

    def __init__(self, png: bytes | None = None, size=(8, 4)) -> None:
        self.png = png if png is not None else _png_bytes(size)
        self.size = size
        self.leaks = 0
        self.calls = 0
        self.closed = False

    def grab(self, timeout: float = 30.0):
        self.calls += 1
        if self.png is None:
            raise AssertionError("the portal must not be used on this path")
        return self.png, self.size, 0.001

    def close(self) -> None:
        self.closed = True


class FakeStream:
    """A frame source that either works or is dead."""

    def __init__(self, size=(100, 50), dead: bool = False) -> None:
        self.size = size
        self.dead = dead
        self.frames = 0
        self.closed = False

    def latest_frame(self) -> Image.Image:
        if self.dead:
            raise ScreenCastError("the session was revoked")
        self.frames += 1
        return Image.new("RGB", self.size, (5, 6, 7))

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Choosing a backend
# --------------------------------------------------------------------------- #
def test_an_unknown_backend_is_refused_not_ignored():
    """`backend` used to be read by nothing, so a typo meant the portal forever."""
    config = Config()
    config.capture.backend = "portal-screencastt"
    with pytest.raises(ValueError) as excinfo:
        build_grabber(config)
    assert "portal-screencastt" in str(excinfo.value)
    # there is no capture row in Settings, so the message must name the config key
    assert "config file" in str(excinfo.value)
    assert "capture.backend" in str(excinfo.value)


@pytest.fixture
def stream_available(monkeypatch):
    """Pretend this machine can stream, so the wiring is what is under test.

    Without this the suite would assert on the machine's GStreamer rather than on
    `build_grabber` - and CI has no PyGObject at all.
    """
    monkeypatch.setattr(
        "lintranslator.capture.screencast_available", lambda: (True, "")
    )


def test_the_screencast_backend_builds_a_stream(stream_available):
    config = Config()
    config.capture.backend = SCREENCAST
    grabber = build_grabber(config)
    try:
        assert grabber.stats["source"] == "screencast"
    finally:
        grabber.close()


def test_the_default_backend_is_the_one_that_writes_nothing(stream_available):
    """The default must not be the backend that leaves a full-screen PNG per poll."""
    assert CaptureConfig().backend == SCREENCAST
    grabber = build_grabber(Config())
    try:
        assert grabber.stats["source"] == "screencast"
    finally:
        grabber.close()


def test_the_portal_backend_builds_no_stream():
    config = Config()
    config.capture.backend = PORTAL_SCREENSHOT
    grabber = build_grabber(config)
    try:
        assert grabber.stats["source"] == "portal-screenshot"
    finally:
        grabber.close()


def test_every_declared_backend_is_buildable():
    for backend in BACKENDS:
        config = Config()
        config.capture.backend = backend
        build_grabber(config).close()


def test_the_restore_token_is_handed_to_the_stream(stream_available):
    config = Config()
    config.capture.backend = SCREENCAST
    config.capture.restore_token = "tok-123"
    grabber = build_grabber(config)
    try:
        assert grabber._stream.restore_token == "tok-123"
    finally:
        grabber.close()


# --------------------------------------------------------------------------- #
# Reading frames
# --------------------------------------------------------------------------- #
def test_the_loop_reads_the_stream_and_never_touches_the_portal():
    portal = StubPortal(png=None)
    stream = FakeStream(size=(640, 480))
    grabber = ScreenGrabber(Region(0, 0, 20, 10, "pixels"), portal=portal, stream=stream)

    frame = grabber.grab()

    assert frame.image.size == (20, 10)
    assert frame.full_size == (640, 480)
    assert stream.frames == 1
    assert portal.calls == 0
    assert grabber.stats["source"] == "screencast"
    assert grabber.stats["leaks"] == 0


def test_a_fraction_region_resolves_against_the_stream_size():
    """The stream reports the workspace size, which is what fractions need."""
    grabber = ScreenGrabber(
        Region(0.5, 0.5, 0.5, 0.5, "fraction"),
        portal=StubPortal(png=None),
        stream=FakeStream(size=(1000, 400)),
    )
    frame = grabber.grab()
    assert frame.region == (500, 200, 500, 200)
    assert frame.image.size == (500, 200)


def test_a_dead_stream_falls_back_to_the_portal_and_says_so():
    notes: list[str] = []
    portal = StubPortal(size=(8, 4))
    stream = FakeStream(dead=True)
    grabber = ScreenGrabber(
        Region(0, 0, 8, 4, "pixels"),
        portal=portal,
        stream=stream,
        on_note=notes.append,
    )

    frame = grabber.grab()

    assert frame.image.size == (8, 4)
    assert portal.calls == 1
    assert grabber.stream_failures == 1
    assert stream.closed is True
    assert len(notes) == 1
    assert "falling back" in notes[0]
    assert grabber.stats["source"] == "portal-screenshot"


def test_a_dead_stream_is_not_retried_on_every_poll():
    """A revoked session fails forever; a warning per poll is worse than the fallback."""
    notes: list[str] = []
    portal = StubPortal(size=(8, 4))
    grabber = ScreenGrabber(
        Region(0, 0, 8, 4, "pixels"),
        portal=portal,
        stream=FakeStream(dead=True),
        on_note=notes.append,
    )

    for _ in range(5):
        grabber.grab()

    assert grabber.stream_failures == 1
    assert len(notes) == 1
    assert portal.calls == 5


def test_closing_the_grabber_closes_the_stream():
    stream = FakeStream()
    grabber = ScreenGrabber(Region(0, 0, 8, 4, "pixels"), portal=StubPortal(), stream=stream)
    grabber.close()
    assert stream.closed is True


# --------------------------------------------------------------------------- #
# The portal calls behind the stream
# --------------------------------------------------------------------------- #
class ScriptedBus:
    """Answers the three ScreenCast requests in order, recording each body.

    The replies are in jeepney's raw shape - every value wrapped as
    (signature, value) - and go through the real `unwrap_dict`, so these tests
    exercise the same unwrapping the live portal path does.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, tuple]] = []

    def portal_request(self, iface, method, signature, body, token, timeout=120.0):
        from lintranslator import portal

        self.seen.append((method, signature, body))
        if method == "CreateSession":
            raw = {"session_handle": ("o", "/org/freedesktop/portal/desktop/session/1/x")}
        elif method == "SelectSources":
            raw = {}
        else:
            raw = {
                # a container-typed variant: nine-character signature
                "streams": (
                    "a(ua{sv})",
                    [
                        (
                            7,
                            {
                                "size": ("(ii)", (1920, 1080)),
                                "position": ("(ii)", (0, 0)),
                            },
                        )
                    ],
                ),
                "restore_token": ("s", "tok-abc"),
            }
        return 0, portal.unwrap_dict(raw), "/r"


def test_create_session_passes_the_vardict_as_one_argument():
    """`a{sv}` is one argument: a bare dict is not a valid body and never worked."""
    from lintranslator.portal import create_screencast

    bus = ScriptedBus()
    create_screencast(bus, types=1)

    method, signature, body = bus.seen[0]
    assert method == "CreateSession"
    assert signature == "a{sv}"
    assert isinstance(body, tuple), f"body must be a tuple, got {type(body).__name__}"
    assert len(body) == 1
    assert isinstance(body[0], dict)
    assert "session_handle_token" in body[0]


def test_start_reports_the_stream_and_restore_token():
    from lintranslator.portal import create_screencast

    session = create_screencast(ScriptedBus(), types=1)
    assert session.node_id == 7
    assert session.size == (1920, 1080)
    assert session.restore_token == "tok-abc"


# --------------------------------------------------------------------------- #
# Unwrapping what the portal sends back
# --------------------------------------------------------------------------- #
def test_a_container_typed_variant_is_unwrapped():
    """`a(ua{sv})` is nine characters, so a one-character test left it wrapped."""
    from lintranslator import portal

    raw = {"streams": ("a(ua{sv})", [(7, {"size": ("(ii)", (1920, 1080))})])}
    unwrapped = portal.unwrap_dict(raw)
    assert unwrapped["streams"] == [(7, {"size": ("(ii)", (1920, 1080))})]
    assert unwrapped["streams"][0][0] == 7


def test_a_simple_typed_variant_is_still_unwrapped():
    from lintranslator import portal

    assert portal.unwrap_variant(("s", "hello")) == "hello"
    assert portal.unwrap_variant(("b", True)) is True
    assert portal.unwrap_variant(("u", 7)) == 7


def test_nested_variants_are_peeled():
    from lintranslator import portal

    assert portal.unwrap_variant(("v", ("s", "deep"))) == "deep"


def test_a_value_that_is_not_a_signature_is_left_alone():
    """A 2-tuple of ordinary strings is a value, not a wrapped variant."""
    from lintranslator import portal

    value = ("hello", "world")
    assert portal.unwrap_variant(value) == value


def test_is_signature_accepts_exactly_one_complete_type():
    from lintranslator import portal

    assert portal.is_signature("s")
    assert portal.is_signature("a(ua{sv})")
    assert portal.is_signature("(ddd)")
    # a variant holds one type, so two types is not a variant signature
    assert not portal.is_signature("ss")
    assert not portal.is_signature("")
    assert not portal.is_signature("hello")
    assert not portal.is_signature("a{sv")


class FakeDescriptor:
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.converted = False

    def to_raw_fd(self) -> int:
        self.converted = True
        return self._fd


class FdReply:
    def __init__(self, body) -> None:
        self.body = body


class FdBus:
    def __init__(self, body) -> None:
        self.body = body
        self.seen: list[tuple[str, str, tuple]] = []

    def call(self, iface, method, signature, body, path=None):
        self.seen.append((method, signature, body))
        return FdReply(self.body)


def test_open_pipewire_remote_takes_ownership_of_the_fd():
    from lintranslator.portal import open_pipewire_remote

    read_fd, write_fd = os.pipe()
    try:
        bus = FdBus((FakeDescriptor(write_fd),))
        fd = open_pipewire_remote(bus, "/session/1")
        assert fd == write_fd
        assert bus.seen[0][0] == "OpenPipeWireRemote"
        assert bus.seen[0][1] == "oa{sv}"
        assert bus.seen[0][2] == ("/session/1", {})
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_open_pipewire_remote_refuses_a_reply_with_no_fd():
    from lintranslator.portal import PortalError, open_pipewire_remote

    with pytest.raises(PortalError):
        open_pipewire_remote(FdBus(("not-an-fd",)), "/session/1")


# --------------------------------------------------------------------------- #
# Missing pieces
# --------------------------------------------------------------------------- #
def test_start_names_what_is_missing_when_gstreamer_is_absent(monkeypatch):
    def no_gst():
        raise ScreenCastError("needs PyGObject with GStreamer and the pipewire plugin")

    monkeypatch.setattr(screencast, "_gst", no_gst)
    with pytest.raises(ScreenCastError) as excinfo:
        ScreenCastStream().start()
    assert "GStreamer" in str(excinfo.value)


def test_screencast_ready_is_false_without_gstreamer(monkeypatch):
    def no_gst():
        raise ScreenCastError("no typelibs here")

    monkeypatch.setattr(screencast, "_gst", no_gst)
    assert screencast.screencast_ready() is False


def test_screencast_ready_is_false_without_the_pipewire_element(monkeypatch):
    class NoPipewire:
        @staticmethod
        def find(name):
            return None

    class FakeGst:
        ElementFactory = NoPipewire

    monkeypatch.setattr(screencast, "_gst", lambda: FakeGst)
    assert screencast.screencast_ready() is False


def test_screencast_ready_is_true_when_the_pipewire_element_is_there(monkeypatch):
    seen: list[str] = []

    class WithPipewire:
        @staticmethod
        def find(name):
            seen.append(name)
            return object()

    class FakeGst:
        ElementFactory = WithPipewire

    monkeypatch.setattr(screencast, "_gst", lambda: FakeGst)
    assert screencast.screencast_ready() is True
    assert seen == ["pipewiresrc"]


def test_gstreamer_setup_loads_the_gstapp_namespace(monkeypatch):
    """appsink's pull methods only exist once GstApp is imported, not just Gst."""
    calls: list[tuple[str, str]] = []

    class FakeGst:
        @staticmethod
        def init(_):
            pass

    class FakeRepo:
        Gst = FakeGst
        GstApp = object()

    class FakeGi:
        @staticmethod
        def require_version(namespace, version):
            calls.append((namespace, version))

        repository = FakeRepo

    monkeypatch.setitem(sys.modules, "gi", FakeGi)
    monkeypatch.setitem(sys.modules, "gi.repository", FakeRepo)

    assert screencast._gst() is FakeGst
    assert ("Gst", "1.0") in calls
    assert ("GstApp", "1.0") in calls


class FakeGst:
    SECOND = 1_000_000_000

    class State:
        NULL = 0
        PLAYING = 4

    class MapFlags:
        READ = 1


class FakeSink:
    def __init__(self, sample) -> None:
        self.sample = sample
        self.timeouts: list[int] = []

    def try_pull_sample(self, timeout: int):
        self.timeouts.append(timeout)
        return self.sample


def test_a_stream_that_stops_delivering_frames_raises():
    stream = ScreenCastStream()
    stream._started = True
    stream._gst = FakeGst
    stream._sink = FakeSink(None)

    with pytest.raises(ScreenCastError) as excinfo:
        stream.latest_frame()
    assert "no frame arrived" in str(excinfo.value)
    assert stream._sink.timeouts == [int(screencast.FRAME_TIMEOUT * FakeGst.SECOND)]


def test_capture_config_round_trips_the_restore_token(tmp_path):
    """The token is what stops the consent dialog appearing on every launch."""
    config = Config()
    config.capture = CaptureConfig(backend=SCREENCAST, restore_token="tok-xyz")
    path = config.save(tmp_path / "config.json")
    assert Config.load(path).capture.restore_token == "tok-xyz"


# --------------------------------------------------------------------------- #
# Failures that arrive without a message
# --------------------------------------------------------------------------- #
class _FilterContext:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


class TimeoutConn:
    """A connection whose filtered receive times out, as jeepney's does."""

    def filter(self, rule):
        return _FilterContext()

    def recv_until_filtered(self, queue, timeout=None):
        raise TimeoutError  # bare, exactly as jeepney raises it


def _timeout_bus():
    from lintranslator.portal import PortalBus

    bus = PortalBus.__new__(PortalBus)  # no real D-Bus connection
    bus.unique_name = ":1.0"
    bus._addr = None
    bus.conn = TimeoutConn()

    def call(iface, method, signature, body, path=None):
        class Reply:
            body = ("/org/freedesktop/portal/desktop/request/1/x",)

        return Reply()

    bus.call = call
    return bus


def test_a_portal_timeout_is_reported_with_a_reason():
    """jeepney's TimeoutError carries no message; an empty failure explains nothing."""
    from lintranslator.portal import PortalError

    with pytest.raises(PortalError) as excinfo:
        _timeout_bus().portal_request("iface", "Method", "sa{sv}", ("", {}), token="t", timeout=0.01)
    message = str(excinfo.value)
    assert "timed out" in message
    assert "permission dialog" in message


class StubBus:
    def close(self) -> None:
        pass


def test_start_wraps_a_message_less_failure_with_its_type_name(monkeypatch):
    monkeypatch.setattr(screencast, "_gst", lambda: FakeGst)
    monkeypatch.setattr(screencast, "PortalBus", StubBus)

    def boom(*args, **kwargs):
        raise TimeoutError  # no message at all

    monkeypatch.setattr(screencast, "create_screencast", boom)
    with pytest.raises(ScreenCastError) as excinfo:
        ScreenCastStream().start()
    assert "TimeoutError" in str(excinfo.value)


def test_a_message_less_stream_failure_still_says_what_failed():
    notes: list[str] = []

    class DeadStream:
        def latest_frame(self):
            raise TimeoutError

        def close(self):
            pass

    grabber = ScreenGrabber(
        Region(0, 0, 8, 4, "pixels"),
        portal=StubPortal(size=(8, 4)),
        stream=DeadStream(),
        on_note=notes.append,
    )
    grabber.grab()

    assert len(notes) == 1
    assert "TimeoutError" in notes[0]
    assert "()" not in notes[0]


# --------------------------------------------------------------------------- #
# Which sessions a stream is worth asking for
# --------------------------------------------------------------------------- #
@pytest.fixture
def gstreamer_present(monkeypatch):
    class WithPipewire:
        @staticmethod
        def find(name):
            return object()

    class FakeGst:
        ElementFactory = WithPipewire

    monkeypatch.setattr(screencast, "_gst", lambda: FakeGst)


def test_a_wayland_kde_session_is_worth_asking(gstreamer_present, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert screencast.screencast_available() == (True, "")


def test_kde_on_x11_is_not_asked_at_all(gstreamer_present, monkeypatch):
    """KDE's portal refuses X11 outright and shows its own dialog doing so."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    available, why = screencast.screencast_available()
    assert available is False
    assert "X11" in why


def test_gnome_on_x11_is_still_asked(gstreamer_present, monkeypatch):
    """GNOME screencasts through mutter, which is not Wayland-only."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert screencast.screencast_available() == (True, "")


def test_a_missing_gstreamer_is_reported_as_the_reason(monkeypatch):
    def no_gst():
        raise ScreenCastError("no typelibs")

    monkeypatch.setattr(screencast, "_gst", no_gst)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    available, why = screencast.screencast_available()
    assert available is False
    assert "GStreamer" in why


def test_an_unavailable_stream_is_not_asked_for_and_is_explained(monkeypatch):
    """No portal call at all: asking is what pops KDE's own warning dialog."""
    from lintranslator.capture import build_grabber
    from lintranslator.config import Config

    monkeypatch.setattr(
        "lintranslator.capture.screencast_available", lambda: (False, "this is X11")
    )
    notes: list[str] = []
    config = Config()
    config.capture.backend = SCREENCAST
    grabber = build_grabber(config, on_note=notes.append)
    try:
        assert grabber.stats["source"] == "portal-screenshot"
        assert len(notes) == 1
        assert "this is X11" in notes[0]
        assert "screenshot portal" in notes[0]
    finally:
        grabber.close()
