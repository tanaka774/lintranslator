"""GTK4 application entry point for the lintranslator GUI."""
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
        # None means "use the config"
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

        # not Gtk.Application's own uniqueness: the picker and the panel must be
        # able to run side by side
        app = Gtk.Application(
            application_id="dev.lintranslator.translator",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )

        def on_activate(_app: Gtk.Application) -> None:
            # before any window is built
            from . import theme

            theme.install_for(self.config)

            if self.mode == MODE_PICK:
                from .picker import RegionPicker

                window = RegionPicker(app, self.config, from_file=self.from_file)
                window.present()
                self._window = window
                # grab after the window maps; it hides itself for the shot so the
                # picker does not capture itself
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
                else:
                    window.show_idle()
                self._window = window

            if self.screenshot_to:
                # Wayland gives no way to screenshot one's own window from
                # outside, so this is the only way to verify the UI
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
                "[The committee has resolved that this entry warrants retention as a "
                "standing record. The material below is the file concerning today's "
                "submission.]"
            ),
            target=(
                "[委員会は、この記録を保存する価値があると判断しました。"
                "以下の資料は本日の申請に関するファイルです。]"
            ),
            confidence=94.0,
            translate_elapsed=1.69,
            total_elapsed=1.9,
            cached=False,
            backend="local",
        )

    def _dump_and_quit(self, app, window) -> bool:
        """Render the window to a PNG and exit."""
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
                # no GPU renderer here: fall back to cairo
                from gi.repository import Gsk

                renderer = Gsk.CairoRenderer.new()
            texture = renderer.render_texture(node, None)
            texture.save_to_png(self.screenshot_to)
            print(
                f"window rendered to {self.screenshot_to} "
                f"({texture.get_width()}x{texture.get_height()})"
            )
        except Exception as exc:  # noqa: BLE001
            # rendering needs a working GPU renderer; this is a verification aid
            # only, so it must never take the app down
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
