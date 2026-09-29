"""Probe: KWin ScreenShot2 CaptureArea over a pipe fd, no file on disk.

Kept as the evidence for a rejected approach. `CaptureArea` looks ideal - the
compositor crops and the image arrives on a unix fd - but KWin gates it behind
`X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2` in a desktop file
found by `readlink /proc/<pid>/exe`. For a Python app that exe is its
interpreter, so matching is fragile and KDE-only. The app reads a PipeWire
ScreenCast stream instead, and this should fail with
`org.kde.KWin.ScreenShot2.Error.NoAuthorized`.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jeepney import DBusAddress, FileDescriptor, new_method_call  # noqa: E402
from jeepney.io.blocking import open_dbus_connection  # noqa: E402
from PIL import Image  # noqa: E402

KWIN_BUS = "org.kde.KWin"
KWIN_PATH = "/org/kde/KWin/ScreenShot2"
KWIN_IFACE = "org.kde.KWin.ScreenShot2"

# QImage::Format -> (PIL raw mode, channels)
FORMATS = {
    4: ("BGRX", 4),   # Format_RGB32
    5: ("BGRA", 4),   # Format_ARGB32
    6: ("BGRA", 4),   # Format_ARGB32_Premultiplied
    13: ("RGB", 3),   # Format_RGB888
    16: ("RGBX", 4),  # Format_RGBX8888
    17: ("RGBA", 4),  # Format_RGBA8888
}


def read_exact(fd: int, count: int) -> bytes:
    chunks = []
    got = 0
    while got < count:
        chunk = os.read(fd, count - got)
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def capture(conn, method, signature, args):
    read_fd, write_fd = os.pipe()
    try:
        addr = DBusAddress(KWIN_PATH, bus_name=KWIN_BUS, interface=KWIN_IFACE)
        # FileDescriptor owns write_fd and closes it on leaving the block, which
        # is also what gives the read end its EOF.
        with FileDescriptor(write_fd) as wfd:
            msg = new_method_call(addr, method, signature + "h", args + (wfd,))
            reply = conn.send_and_get_reply(msg, timeout=30)
        results = {k: (v[1] if isinstance(v, tuple) and len(v) == 2 else v)
                   for k, v in reply.body[0].items()}
    except BaseException:
        os.close(read_fd)
        raise

    try:
        print(f"  results: {results}")
        if results.get("type") != "raw":
            raise SystemExit(f"unexpected type {results.get('type')!r}")
        w, h = int(results["width"]), int(results["height"])
        stride, fmt = int(results["stride"]), int(results["format"])
        print(f"  {w}x{h} stride={stride} qimage_format={fmt}")
        if fmt not in FORMATS:
            raise SystemExit(f"unhandled QImage format {fmt}")
        mode, channels = FORMATS[fmt]
        print(f"  stride check: stride == w*{channels}? {stride == w * channels}")
        data = read_exact(read_fd, stride * h)
        print(f"  read {len(data)} bytes (expected {stride * h})")
        img = Image.frombuffer("RGB", (w, h), data, "raw", mode, stride, 1)
        return img
    finally:
        os.close(read_fd)


conn = open_dbus_connection(bus="SESSION", enable_fds=True)
try:
    ver = conn.send_and_get_reply(
        new_method_call(
            DBusAddress(KWIN_PATH, bus_name=KWIN_BUS, interface="org.freedesktop.DBus.Properties"),
            "Get", "ss", (KWIN_IFACE, "Version"),
        )
    ).body[0][1]
    print(f"KWin ScreenShot2 version: {ver}\n")

    print("--- CaptureArea 320x120 at (0,0), no cursor ---")
    t0 = time.monotonic()
    img = capture(conn, "CaptureArea", "iiuua{sv}",
                  (0, 0, 320, 120,
                   {"include-cursor": ("b", False),
                    "native-resolution": ("b", False),
                    "hide-caller-windows": ("b", False)}))
    print(f"  elapsed {1000*(time.monotonic()-t0):.1f} ms  image={img.size} mode={img.mode}")
    out = Path("probe/kwin_area_probe.png")
    img.convert("RGB").save(out)
    print(f"  wrote {out} ({out.stat().st_size} bytes)")

    print("\n--- CaptureWorkspace (size only, native resolution) ---")
    t0 = time.monotonic()
    full = capture(conn, "CaptureWorkspace", "a{sv}",
                   ({"include-cursor": ("b", False),
                     "native-resolution": ("b", True),
                     "hide-caller-windows": ("b", False)},))
    print(f"  elapsed {1000*(time.monotonic()-t0):.1f} ms  workspace={full.size}")
finally:
    conn.close()

print("\nNOTE: nothing was written outside probe/ -- no file ever left the pipe.")
