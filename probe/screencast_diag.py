"""Diagnose where the ScreenCast path breaks: session, remote fd, or pipeline.

Prints each step separately so the failing one is unambiguous, and listens on the
GStreamer bus for the real error instead of only reporting "no frame".
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from lintranslator.portal import PortalBus, create_screencast, open_pipewire_remote  # noqa: E402

TIMEOUT = 30.0

Gst.init(None)
bus = PortalBus()
print(f"bus = {bus.unique_name}")

print("\n[1] create_screencast (types=1, monitor)")
t0 = time.monotonic()
try:
    session = create_screencast(bus, types=1, timeout=TIMEOUT)
except Exception as exc:
    print(f"    FAILED after {time.monotonic() - t0:.1f}s: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
print(f"    ok in {time.monotonic() - t0:.1f}s")
print(f"    session_path = {session.session_path}")
print(f"    node_id      = {session.node_id}")
print(f"    size         = {session.size} position = {session.position}")

print("\n[2] open_pipewire_remote")
t0 = time.monotonic()
try:
    fd = open_pipewire_remote(bus, session.session_path)
except Exception as exc:
    print(f"    FAILED: {type(exc).__name__}: {exc}")
    session.close(bus)
    raise SystemExit(1)
print(f"    ok in {time.monotonic() - t0:.1f}s, fd = {fd}")

print("\n[3] GStreamer pipeline")
desc = (
    f"pipewiresrc path={session.node_id} fd={fd} do-timestamp=true "
    "! videoconvert ! video/x-raw,format=RGB "
    "! appsink name=sink max-buffers=1 drop=true sync=false"
)
print(f"    {desc}")
pipeline = Gst.parse_launch(desc)
sink = pipeline.get_by_name("sink")
gst_bus = pipeline.get_bus()
pipeline.set_state(Gst.State.PLAYING)

sample = None
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    msg = gst_bus.pop_filtered(
        Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
    )
    if msg is not None:
        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"    GST ERROR: {err.message}")
            print(f"    debug: {debug}")
            break
        if msg.type == Gst.MessageType.WARNING:
            warn, debug = msg.parse_warning()
            print(f"    gst warning: {warn.message}")
    sample = sink.try_pull_sample(200 * Gst.MSECOND)
    if sample is not None:
        break

if sample is not None:
    caps = sample.get_caps().get_structure(0)
    print(f"    GOT FRAME {caps.get_value('width')}x{caps.get_value('height')}")
else:
    print("    no frame")

print("\n[4] teardown")
pipeline.set_state(Gst.State.NULL)
session.close(bus)
bus.close()
print("    done")
