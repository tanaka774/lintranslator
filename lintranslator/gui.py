"""GTK4 application entry point for the lintranslator GUI.

Two windows, one process:

    lintranslator gui --pick     region picker (capture, drag a box, preview OCR)
    lintranslator gui            translation panel (always-on-top, runs the pipeline)

The panel is a normal keep-above window rather than a layer-shell surface: the
`gtk4-layer-shell` binding is not installable here, and Wayland forbids clients
from positioning themselves. See `panel.py` for the consequences and the
`GDK_BACKEND=x11` escape hatch.
"""
from __future__ import annotations

from .config import Config

MODE_PANEL = "panel"
MODE_PICK = "pick"


class LinTranslatorApp:
    """Builds and runs the right window for the requested mode."""

    def __init__(
        self,
        config: Config,
        mode: str = MODE_PANEL,
        *,
        position: tuple[int, int] | None = None,
        autostart: bool | None = None,
        capture_delay: float = 1.2,
        screenshot_to: str | None = None,
        demo: bool = False,
        initial_capture: bool = True,
        from_file: str | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.position = position
        # None means "use the config" - the default is to start translating
        # immediately, because an idle window reads as a broken one.
        self.autostart = config.gui.autostart if autostart is None else autostart
        self.capture_delay = capture_delay
        self.screenshot_to = screenshot_to
        self.demo = demo
        self.initial_capture = initial_capture
        self.from_file = from_file
        self._window = None

    def run(self, argv: list[str] | None = None) -> int:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib, Gtk

        # NOT using Gtk.Application's own uniqueness: the picker and the panel
        # must be able to run side by side.
        app = Gtk.Application(
            application_id="dev.lintranslator.translator",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )

        def on_activate(_app: Gtk.Application) -> None:
            # Before any window is built. Installing the stylesheet used to be a
            # side effect of the panel's constructor, so anything that opened
            # without a panel (the picker on its own, the settings dialog in a
            # probe) came out in the desktop theme instead - light, on an app
            # whose panel is a dark card.
            from . import theme

            theme.install_for(self.config)

            if self.mode == MODE_PICK:
                from .picker import RegionPicker

                window = RegionPicker(app, self.config, from_file=self.from_file)
                window.present()
                self._window = window
                # Grab after the window maps, and hide it for the shot so the
                # picker does not capture itself.
                if self.initial_capture and not self.from_file:
                    GLib.timeout_add(
                        int(self.capture_delay * 1000), self._initial_capture, window
                    )
            else:
                from .panel import TranslatorPanel

                window = TranslatorPanel(
                    app, self.config, self.position, demo_event=self._demo_event() if self.demo else None
                )
                window.present()
                if self.autostart:
                    window.start_pipeline()
                self._window = window

            if self.screenshot_to:
                # Render the window to a PNG and exit. Wayland gives no way to
                # screenshot one's own window from outside, so this is the only
                # practical way to verify the UI in a headless-ish check.
                GLib.timeout_add(2500, self._dump_and_quit, app, window)

        def on_shutdown(_app: Gtk.Application) -> None:
            closer = getattr(self._window, "close_pipeline", None)
            if callable(closer):
                closer()

        app.connect("activate", on_activate)
        app.connect("shutdown", on_shutdown)
        return app.run(argv or [])

    @staticmethod
    def _initial_capture(window) -> bool:
        """First picker capture, once the window has mapped."""
        if getattr(window, "screen_image", None) is None:
            window._on_capture(window.capture_btn)
        return False

    @staticmethod
    def _demo_event():
        """A representative translation, for checking the panel layout."""
        from .pipeline import Event

        return Event(
            source=(
                "[It has been determined that this case merits preservation as a "
                "record. The following is the case record pertaining to today's request.]"
            ),
            target=(
                "[この事件は記録として保存されるべきであると決定された."
                "今日の要請に関する事件記録は以下のとおりです.]"
            ),
            confidence=94.0,
            translate_elapsed=1.69,
            total_elapsed=1.9,
            cached=False,
            backend="local",
        )

    def _dump_and_quit(self, app, window) -> bool:
        """Render the window to a PNG and exit.

        Wayland offers no way to screenshot one's own window from outside, so
        this is the practical way to verify the UI renders correctly.
        """
        from gi.repository import Gtk

        try:
            paintable = Gtk.WidgetPaintable.new(window)
            width = window.get_width()
            height = window.get_height()
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()

            renderer = None
            native = window.get_native()
            if native is not None:
                renderer = native.get_renderer()
            if renderer is None:
                # No GPU in this environment: use the cairo renderer instead.
                from gi.repository import Gsk

                renderer = Gsk.CairoRenderer.new()
            texture = renderer.render_texture(node, None)
            texture.save_to_png(self.screenshot_to)
            print(
                f"window rendered to {self.screenshot_to} "
                f"({texture.get_width()}x{texture.get_height()})"
            )
        except Exception as exc:  # noqa: BLE001
            # Rendering needs a working GPU renderer; without one this is a
            # verification aid only, so it must never take the app down.
            print(f"could not render window here: {type(exc).__name__}: {exc}")
        finally:
            closer = getattr(window, "close_pipeline", None)
            if callable(closer):
                closer()
            app.quit()
        return False


def run_gui(
    config: Config,
    mode: str = MODE_PANEL,
    position: tuple[int, int] | None = None,
    autostart: bool = True,
    screenshot_to: str | None = None,
    demo: bool = False,
    initial_capture: bool = True,
    from_file: str | None = None,
) -> int:
    return LinTranslatorApp(
        config,
        mode,
        position=position,
        autostart=autostart,
        screenshot_to=screenshot_to,
        demo=demo,
        initial_capture=initial_capture,
        from_file=from_file,
    ).run()
