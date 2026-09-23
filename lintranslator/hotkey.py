"""A real global hotkey, granted by the compositor.

Wayland gives an application no way to read global keys, so a shortcut that works
while the game has focus has to come from the compositor. `org.freedesktop.portal.GlobalShortcuts`
does exactly that, and KDE implements it: the app asks for a shortcut, the
compositor shows its own binding dialog once, and from then on it sends
`Activated` when the key is pressed. Nothing to configure by hand.

Where the portal is missing, or the user declines the dialog, the fallback is the
control socket: bind `lintranslator reread` as a KDE custom shortcut (System Settings ->
Shortcuts -> Custom), which is also what makes the feature scriptable.

Deliberately GUI-only and best-effort: every failure is reported through
`on_status`, never raised into the app. A translator that will not start because a
hotkey could not be registered would be a worse trade than no hotkey.
"""
from __future__ import annotations

import os
from typing import Callable

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

# How long to wait for the compositor's binding dialog before giving up. The user
# may have to pick a key combination, so this is generous - but a dialog left
# unanswered must not leave the caller waiting forever.
BIND_TIMEOUT_SECONDS = 120.0

REREAD_ID = "reread"
REREAD_DESCRIPTION = "lintranslator: re-read the box and translate it"
# XDG shortcut syntax, which is what `preferred_trigger` takes.
REREAD_TRIGGER = "CTRL+ALT+R"


class HotkeyError(RuntimeError):
    """The compositor refused, or this session has no GlobalShortcuts portal."""


class GlobalHotkey:
    """One portal session with one or more bound shortcuts."""

    def __init__(
        self,
        on_activated: Callable[[str], None],
        on_status: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.on_activated = on_activated
        self.on_status = on_status
        self._conn = None
        self._proxy = None
        self._session: str | None = None
        self._subscriptions: list[int] = []
        self._timeout_id = 0
        self._bound = False

    # -- reporting --------------------------------------------------------- #
    def _status(self, ok: bool, detail: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(ok, detail)
            except Exception:  # noqa: BLE001 - a UI callback must not break binding
                pass

    # -- binding ----------------------------------------------------------- #
    def bind(
        self,
        shortcuts: list[tuple[str, str, str]] | None = None,
        parent_window: str = "",
    ) -> None:
        """Ask the compositor to bind shortcuts: (id, description, trigger)."""
        shortcuts = shortcuts or [(REREAD_ID, REREAD_DESCRIPTION, REREAD_TRIGGER)]
        try:
            from gi.repository import Gio, GLib
        except Exception as exc:  # noqa: BLE001
            self._status(False, f"no GLib for the shortcut portal ({exc})")
            return

        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._proxy = Gio.DBusProxy.new_sync(
                self._conn,
                Gio.DBusProxyFlags.NONE,
                None,
                PORTAL_BUS,
                PORTAL_PATH,
                SHORTCUTS_IFACE,
                None,
            )
        except Exception as exc:  # noqa: BLE001
            self._status(False, f"global shortcuts unavailable: {type(exc).__name__}")
            return

        token = f"lintranslator_gs_{os.getpid()}"
        session_token = f"{token}_session"
        create_request = self._request_path(f"{token}_create")
        self._subscribe(create_request, self._on_create_response)
        self._timeout_id = GLib.timeout_add_seconds(
            int(BIND_TIMEOUT_SECONDS), self._on_timeout
        )
        # The user may be looking at the compositor's dialog; nothing else here
        # blocks on it.
        self._call(
            "CreateSession",
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": GLib.Variant("s", f"{token}_create"),
                        "session_handle_token": GLib.Variant("s", session_token),
                    },
                ),
            ),
            self._on_call_failed,
        )
        self._shortcuts = shortcuts
        self._parent_window = parent_window

    def _bind_session(self, session: str) -> None:
        from gi.repository import GLib

        self._session = session
        # Activated arrives on the session object itself.
        self._subscribe(session, self._on_activated, member="Activated")
        request = self._request_path(f"lintranslator_gs_{os.getpid()}_bind")
        self._subscribe(request, self._on_bind_response)
        body = (
            session,
            [
                (
                    shortcut_id,
                    {
                        "description": GLib.Variant("s", description),
                        "preferred_trigger": GLib.Variant("s", trigger),
                    },
                )
                for shortcut_id, description, trigger in self._shortcuts
            ],
            self._parent_window,
            {"handle_token": GLib.Variant("s", f"lintranslator_gs_{os.getpid()}_bind")},
        )
        self._call("BindShortcuts", GLib.Variant("(oa(sa{sv})sa{sv})", body), self._on_call_failed)

    # -- portal plumbing --------------------------------------------------- #
    def _request_path(self, token: str) -> str:
        unique = (self._conn.get_unique_name() or ":0").lstrip(":").replace(".", "_")
        return f"{PORTAL_PATH}/request/{unique}/{token}"

    def _subscribe(self, path: str, callback, member: str = "Response") -> None:
        from gi.repository import Gio

        subscription = self._conn.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE if member == "Response" else SHORTCUTS_IFACE,
            member,
            path,
            None,
            Gio.DBusSignalFlags.NONE,
            callback,
        )
        self._subscriptions.append(subscription)

    def _call(self, method: str, parameters, on_failed) -> None:
        from gi.repository import Gio

        try:
            self._proxy.call(
                method,
                parameters,
                Gio.DBusCallFlags.NONE,
                -1,  # default timeout
                None,
                lambda proxy, result: self._on_call_done(proxy, result, method, on_failed),
            )
        except Exception as exc:  # noqa: BLE001 - never raise into the GUI
            on_failed(f"{method}: {type(exc).__name__}: {exc}")

    def _on_call_done(self, proxy, result, method: str, on_failed) -> None:
        try:
            proxy.call_finish(result)
        except Exception as exc:  # noqa: BLE001
            on_failed(f"{method}: {type(exc).__name__}: {exc}")

    def _on_call_failed(self, detail: str) -> None:
        # The compositor's own words are exact but unhelpful; the app-id refusal
        # is by far the most common one and has a specific fix.
        if "app id is required" in detail:
            detail = "this launch has no application id (start lintranslator from the menu)"
        self._status(False, detail)

    def _on_create_response(self, _conn, _sender, path, _iface, _signal, params) -> None:
        code, results = params.unpack()
        if code != 0:
            self._status(False, f"shortcut session refused by the compositor (code {code})")
            return
        session = results.get("session_handle")
        if not session:
            self._status(False, "shortcut session returned no handle")
            return
        self._bind_session(str(session))

    def _on_bind_response(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        code, results = params.unpack()
        self._clear_timeout()
        if code != 0:
            self._status(False, f"shortcut not bound (compositor code {code})")
            return
        self._bound = True
        described = ", ".join(
            str(item[1].get("trigger_description", item[1].get("preferred_trigger", "")))
            for item in results.get("shortcuts", [])
            if isinstance(item, (list, tuple)) and len(item) > 1
        )
        self._status(True, described or REREAD_TRIGGER.replace("+", "+"))

    def _on_activated(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        session, shortcut_id, _timestamp, _options = params.unpack()
        if self._session is not None and str(session) != self._session:
            return
        try:
            self.on_activated(str(shortcut_id))
        except Exception:  # noqa: BLE001 - a UI callback must not break the signal
            pass

    def _on_timeout(self) -> bool:
        self._timeout_id = 0
        if not self._bound:
            self._status(False, "no answer to the shortcut dialog")
        return False

    def _clear_timeout(self) -> None:
        if self._timeout_id:
            try:
                from gi.repository import GLib

                GLib.source_remove(self._timeout_id)
            except Exception:  # noqa: BLE001
                pass
            self._timeout_id = 0

    # -- lifecycle --------------------------------------------------------- #
    @property
    def bound(self) -> bool:
        return self._bound

    def close(self) -> None:
        """Drop the session and the signal subscriptions."""
        from gi.repository import GLib

        self._clear_timeout()
        if self._session is not None and self._conn is not None:
            try:
                self._conn.call_sync(
                    PORTAL_BUS,
                    self._session,
                    SESSION_IFACE,
                    "Close",
                    None,
                    None,
                    Gio_call_flags(),
                    1000,
                    None,
                )
            except Exception:  # noqa: BLE001
                pass
            self._session = None
        for subscription in self._subscriptions:
            try:
                self._conn.signal_unsubscribe(subscription)
            except Exception:  # noqa: BLE001
                pass
        self._subscriptions.clear()
        self._bound = False


def Gio_call_flags():
    from gi.repository import Gio

    return Gio.DBusCallFlags.NONE
