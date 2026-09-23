#!/usr/bin/env python3
"""Phase 0 spike: probe the xdg-desktop-portal capture paths on this session.

Answers, in order of preference for lintranslator:
  1. X11 grab on $DISPLAY (XWayland root) - only valid if the game is an X11 window
  2. org.freedesktop.portal.ScreenCast - persistent PipeWire stream (needs handler + pipewire)
  3. org.freedesktop.portal.Screenshot - one-shot PNG per call

Run from a real desktop session. Prints a JSON report to stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQ_IFACE = "org.freedesktop.portal.Request"


# --------------------------------------------------------------------------- #
# D-Bus plumbing (jeepney): a blocking connection with background message pump
# --------------------------------------------------------------------------- #
class AlreadySet:
    pass


class Bus:
    """Blocking D-Bus client for xdg-desktop-portal (jeepney 0.9.0)."""

    def __init__(self) -> None:
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        self.conn = open_dbus_connection(bus="SESSION")
        self.unique_name = self.conn.unique_name
        self.DBusAddress = DBusAddress

    def call(self, iface: str, method: str, signature: str, body: tuple, path: str = PORTAL_PATH):
        from jeepney import new_method_call

        addr = self.DBusAddress(path, bus_name=PORTAL_BUS, interface=iface)
        return self.conn.send_and_get_reply(
            new_method_call(addr, method, signature, body), timeout=60
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # -- portal request helpers -------------------------------------------- #
    def _request_path(self, token: str) -> str:
        """The portal's request object path for a handle_token we chose.

        Path is /org/freedesktop/portal/desktop/request/<sender>/<token> where
        <sender> is our unique bus name with the leading ':' stripped and '.'
        replaced by '_'.
        """
        sender = self.unique_name.lstrip(":").replace(".", "_")
        return f"{PORTAL_PATH}/request/{sender}/{token}"

    def portal_request(
        self,
        iface: str,
        method: str,
        signature: str,
        body: tuple,
        token: str,
        timeout: float = 120.0,
    ):
        """Call a portal method that returns a Request handle, then await Response.

        Subscribes to the Response signal *before* issuing the call so there is
        no window in which the portal's reply could be missed.
        Returns (response_code, results_dict, request_path).
        """
        from jeepney import MatchRule

        req_path = self._request_path(token)
        rule = MatchRule(
            type="signal", interface=REQ_IFACE, member="Response", path=req_path
        )
        with self.conn.filter(rule) as queue:
            reply = self.call(iface, method, signature, body)
            returned = _unwrap_variant(reply.body[0])
            if isinstance(returned, str) and returned.startswith("/"):
                req_path = returned

            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no Response on {req_path} within {timeout}s")
                msg = self.conn.recv_until_filtered(queue, timeout=remaining)
                code, results = msg.body[0], msg.body[1]
                return code, _demarshal(results), req_path


def _unwrap_variant(v):
    """jeepney returns variants as (signature, value) tuples."""
    while isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) and len(v[0]) == 1:
        v = v[1]
    return v


def _demarshal(results: dict) -> dict:
    out = {}
    for k, v in (results or {}).items():
        out[k] = _unwrap_variant(v)
    return out


def portal_version(bus: Bus, iface: str):
    try:
        raw = bus.call(
            "org.freedesktop.DBus.Properties", "Get", "ss", (iface, "version")
        )
        return int(_unwrap_variant(raw.body[0]))
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


# --------------------------------------------------------------------------- #
# Path 1: X11 / XWayland grab
# --------------------------------------------------------------------------- #
def probe_x11() -> dict:
    from PIL import Image

    disp = os.environ.get("DISPLAY")
    if not disp:
        return {"available": False, "reason": "DISPLAY unset"}

    result: dict = {"available": False, "display": disp}
    try:
        from Xlib import display as xdisplay  # type: ignore
    except ImportError:
        result["reason"] = "python-xlib not installed (pip install python-xlib)"
        return result

    try:
        d = xdisplay.Display(disp)
        root = d.screen().root
        geo = root.get_geometry()
        result.update(
            {
                "available": True,
                "root_size": f"{geo.width}x{geo.height}",
                "n_screens": d.screen_count(),
            }
        )
        t0 = time.time()
        w, h = min(400, geo.width), min(300, geo.height)
        raw = root.get_image(0, 0, w, h, xdisplay.X.ZPixmap, 0xFFFFFFFF)
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.data)
        result["grab_ms"] = round((time.time() - t0) * 1000, 1)
        result["sample_mean"] = round(
            sum(img.convert("L").getdata()) / (raw.width * raw.height), 1
        )
        # A real XWayland root has content; a black root means no composited desktop.
        result["has_content"] = result["sample_mean"] > 3
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"{type(exc).__name__}: {exc}"
    return result


# --------------------------------------------------------------------------- #
# Path 2: ScreenCast portal -> PipeWire stream
# --------------------------------------------------------------------------- #
def probe_screencast(bus: Bus) -> dict:
    out: dict = {"available": False}
    try:
        version = portal_version(bus, "org.freedesktop.portal.ScreenCast")
        out["version"] = version
        caps_raw = bus.call(
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
            ("org.freedesktop.portal.ScreenCast", "AvailableSourceTypes"),
        )
        out["available_source_types"] = _unwrap_variant(caps_raw.body[0])
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"introspection failed: {exc}"
        return out

    token = f"lintranslator{os.getpid()}"
    session_path = f"{PORTAL_PATH}/session/{os.getpid()}/{token}"

    # --- CreateSession ---
    try:
        code, results, req = bus.portal_request(
            "org.freedesktop.portal.ScreenCast",
            "CreateSession",
            "a{sv}",
            (
                {
                    "handle_token": ("s", f"{token}_create"),
                    "session_handle_token": ("s", token),
                },
            ),
            token=f"{token}_create",
            timeout=30,
        )
        out["create_session"] = {"code": code, "request": req}
        if code != 0:
            out["reason"] = f"CreateSession denied/failed (code {code})"
            return out
        session_path = results.get("session_handle", session_path)
        out["session_path"] = session_path
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"CreateSession error: {type(exc).__name__}: {exc}"
        return out

    # --- SelectSources (KDE shows its picker dialog here) ---
    try:
        code, results, req = bus.portal_request(
            "org.freedesktop.portal.ScreenCast",
            "SelectSources",
            "oa{sv}",
            (
                session_path,
                {
                    "handle_token": ("s", f"{token}_sel"),
                    "types": ("u", 3),  # 1=monitor, 2=window
                    "multiple": ("b", False),
                    "cursor_mode": ("u", 1),
                },
            ),
            token=f"{token}_sel",
            timeout=180,
        )
        out["select_sources"] = {"code": code, "request": req}
        if code != 0:
            out["reason"] = f"SelectSources cancelled/denied (code {code})"
            return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"SelectSources error: {type(exc).__name__}: {exc}"
        return out

    # --- Start: yields PipeWire node id(s) to feed a stream consumer ---
    try:
        code, results, req = bus.portal_request(
            "org.freedesktop.portal.ScreenCast",
            "Start",
            "osa{sv}",
            (session_path, "", {"handle_token": ("s", f"{token}_start")}),
            token=f"{token}_start",
            timeout=180,
        )
        out["start"] = {"code": code, "request": req}
        if code != 0:
            out["reason"] = f"Start denied (code {code})"
            return out
        streams = results.get("streams") or []
        out["streams_raw"] = streams
        out["restore_token"] = results.get("restore_token")
        out["available"] = bool(streams)
        if streams:
            first = streams[0]
            out["node_id"] = first[0] if isinstance(first, (list, tuple)) else first
            props = first[1] if isinstance(first, (list, tuple)) and len(first) > 1 else {}
            out["stream_props"] = _demarshal(props) if isinstance(props, dict) else props
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"Start error: {type(exc).__name__}: {exc}"
    return out


def probe_screenshot(bus: Bus) -> dict:
    out: dict = {"available": False}
    try:
        out["version"] = portal_version(bus, "org.freedesktop.portal.Screenshot")
        token = f"lintranslatorshot{os.getpid()}"
        t0 = time.time()
        code, results, req = bus.portal_request(
            "org.freedesktop.portal.Screenshot",
            "Screenshot",
            "sa{sv}",
            ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
            token=token,
            timeout=120,
        )
        out["elapsed_s"] = round(time.time() - t0, 2)
        out["code"] = code
        if code != 0:
            out["reason"] = f"denied/cancelled (code {code})"
            return out
        uri = results.get("uri")
        out["uri"] = uri
        out["available"] = bool(uri)
        if uri:
            path = uri.replace("file://", "")
            try:
                from PIL import Image

                with Image.open(path) as im:
                    out["image_size"] = list(im.size)
                    out["image_mode"] = im.mode
                out["readable"] = True
            except Exception as exc:  # noqa: BLE001
                out["readable"] = False
                out["read_error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    report: dict = {
        "session": {
            "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
            "XDG_CURRENT_DESKTOP": os.environ.get("XDG_CURRENT_DESKTOP"),
            "DISPLAY": os.environ.get("DISPLAY"),
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            "pid": os.getpid(),
        },
        "tools": {},
    }
    for tool in ("gst-launch-1.0", "pipewire", "tesseract", "spectacle"):
        report["tools"][tool] = subprocess.run(
            ["bash", "-lc", f"command -v {tool}"], capture_output=True, text=True
        ).stdout.strip() or None

    only = sys.argv[1] if len(sys.argv) > 1 else "all"

    if only in ("all", "x11"):
        report["x11"] = probe_x11()

    try:
        bus = Bus()
    except Exception as exc:  # noqa: BLE001
        report["bus_error"] = str(exc)
        print(json.dumps(report, indent=2, default=str))
        return 1

    try:
        report["portal_versions"] = {
            iface: portal_version(bus, f"org.freedesktop.portal.{iface}")
            for iface in ("ScreenCast", "Screenshot", "RemoteDesktop")
        }
        if only in ("all", "screencast"):
            report["screencast"] = probe_screencast(bus)
        if only in ("all", "screenshot"):
            report["screenshot"] = probe_screenshot(bus)
    finally:
        bus.close()

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
