"""One stylesheet for the whole app.

Before this module the panel was the only window with any CSS at all: the picker
and the settings dialog were stock Adwaita next to a custom dark card, so the app
had two visual languages. Everything visual now comes from the tokens below.

Two rules keep this safe to apply globally:

* **Nothing is styled by bare element name.** Every rule is scoped to a
  `.lintranslator-*` class or to a window class, so the picker's cairo drawing area and
  any widget nobody has classified keep the toolkit's own look. A broad
  `button { ... }` would repaint controls the picker draws by hand.
* **The panel's card is the one translucent surface.** It sits over a game, so
  the game must show through it; every other surface is opaque.

Sizes for the panel's two text lines are substituted in, because they follow
`display.base_font_size * display.font_scale` and change live from Settings.
"""
from __future__ import annotations

# -- tokens ---------------------------------------------------------------- #
# Surfaces
SURFACE = "rgba(16, 17, 20, 0.94)"  # the panel card: the game shows through
SURFACE_SOLID = "#101114"  # windows that should not be see-through
SURFACE_RAISED = "rgba(255, 255, 255, 0.055)"
SURFACE_SUNKEN = "rgba(0, 0, 0, 0.28)"
BORDER = "rgba(255, 255, 255, 0.14)"
BORDER_SOFT = "rgba(255, 255, 255, 0.07)"

# Text
TEXT = "#f2f3f5"
TEXT_DIM = "rgba(255, 255, 255, 0.64)"
TEXT_FAINT = "rgba(255, 255, 255, 0.42)"

# Meaning
ACCENT = "#8cc8ff"
ACCENT_TEXT = "#08121c"
WARN = "#ffd479"
ERROR = "#ff9c8a"
OK = "#8fe0a8"

# Spacing scale, in px. Used through CSS `padding`/`margin` on classes rather
# than through Gtk.Box spacing, so one number changes both windows.
SPACE_XS = 4
SPACE_SM = 6
SPACE_MD = 10
SPACE_LG = 14

RADIUS_CARD = 10
RADIUS_CONTROL = 6

# Type scale for everything that is not the panel's two text lines.
FONT_CAPTION = 10
FONT_SMALL = 11
FONT_BODY = 13

# The panel's control row is a fixed height by design (see display lines below),
# so the buttons in it are sized to sit inside it rather than to pad it out.
CONTROL_H = 24


def build_css(*, target_px: int, source_px: int) -> str:
    """The whole app's stylesheet.

    `target_px` / `source_px` are the panel's translation and original-text font
    sizes, already scaled. Everything else is a fixed token.
    """
    return f"""
/* ===================== shared ===================== */
.lintranslator-card {{
  background-color: {SURFACE};
  border: 1px solid {BORDER};
  border-radius: {RADIUS_CARD}px;
  padding: {SPACE_MD}px {SPACE_MD + 2}px;
}}
.lintranslator-section  {{ color: {TEXT};       font-weight: 700; font-size: {FONT_BODY}px; }}
.lintranslator-body     {{ color: {TEXT};       font-size: {FONT_BODY}px; }}
.lintranslator-dim      {{ color: {TEXT_DIM};   font-size: {FONT_SMALL}px; }}
.lintranslator-hint     {{ color: {TEXT_DIM};   font-size: {FONT_SMALL}px; }}
.lintranslator-caption  {{ color: {TEXT_FAINT}; font-size: {FONT_CAPTION}px; }}
.lintranslator-status   {{ color: {TEXT_DIM};   font-size: {FONT_CAPTION}px; }}
.lintranslator-error    {{ color: {ERROR};      font-size: {FONT_SMALL}px; }}
.lintranslator-warn     {{ color: {WARN};       font-size: {FONT_SMALL}px; }}
.lintranslator-ok       {{ color: {OK};         font-size: {FONT_SMALL}px; }}
/* `lintranslator-warn` on the status row adds a colour, not a size. That row is one
   fixed-height line that changes text as the pipeline changes state, and a
   message that grows when it turns into "paused" reads as a rendering glitch. */
.lintranslator-status.lintranslator-warn {{ font-size: {FONT_CAPTION}px; }}

/* A 1px rule, used instead of a Gtk.Separator so its colour is one token. */
.lintranslator-rule {{
  background-color: {BORDER_SOFT};
  min-height: 1px;
  margin: {SPACE_SM}px 0;
}}
.lintranslator-rule-tight {{
  background-color: {BORDER_SOFT};
  min-height: 1px;
  margin: {SPACE_XS}px 0;
}}

/* ===================== buttons ===================== */
/* Colours are set explicitly rather than inherited. The desktop theme here is
   Breeze *light* (`gtk-application-prefer-dark-theme` is false by default), so
   an unstyled button is a white block with dark text - which is exactly how the
   control row first looked on the dark card, and why the overflow menu's labels
   were dark text on a dark popover and could not be read at all. */
button.lintranslator-btn {{
  background-color: rgba(255, 255, 255, 0.10);
  background-image: none;
  color: {TEXT};
  border: 1px solid rgba(255, 255, 255, 0.16);
  padding: 2px 10px;
  min-height: {CONTROL_H - 6}px;
  font-size: {FONT_SMALL}px;
  border-radius: {RADIUS_CONTROL}px;
}}
button.lintranslator-btn:hover {{ background-color: rgba(255, 255, 255, 0.17); }}
button.lintranslator-btn:active,
button.lintranslator-btn:checked {{ background-color: rgba(255, 255, 255, 0.24); }}
button.lintranslator-btn:focus {{ outline: none; }}
button.lintranslator-primary {{
  background-color: {ACCENT};
  color: {ACCENT_TEXT};
  border-color: {ACCENT};
  font-weight: 700;
}}
button.lintranslator-primary:hover {{ background-color: #a5d5ff; border-color: #a5d5ff; }}
button.lintranslator-primary:active {{ background-color: #74b6f0; border-color: #74b6f0; }}
/* The overflow trigger: square, so it does not shift the row's baseline. */
button.lintranslator-icon {{
  padding: 2px 6px;
  min-width: {CONTROL_H - 6}px;
  min-height: {CONTROL_H - 6}px;
  font-size: {FONT_BODY}px;
  border-radius: {RADIUS_CONTROL}px;
}}
/* Rows inside the overflow popover: full width, left aligned, like a menu. */
button.lintranslator-menu-item {{
  background: none;
  background-image: none;
  color: {TEXT};
  border: none;
  box-shadow: none;
  padding: {SPACE_SM}px {SPACE_MD}px;
  font-size: {FONT_SMALL}px;
  border-radius: {RADIUS_CONTROL}px;
}}
button.lintranslator-menu-item:hover {{ background-color: rgba(255, 255, 255, 0.10); }}
button.lintranslator-menu-item:active,
button.lintranslator-menu-item:checked {{ background-color: rgba(255, 255, 255, 0.16); }}
button.lintranslator-menu-item:focus {{ outline: none; }}

/* The overflow popover. Painted explicitly because the card is dark by design
   and the desktop theme it sits on is not necessarily dark. */
popover.lintranslator-menu > contents {{
  background-color: #16171b;
  border: 1px solid {BORDER};
  border-radius: {RADIUS_CONTROL + 2}px;
  padding: {SPACE_XS}px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
}}

/* ===================== panel ===================== */
window.lintranslator-panel {{ background: transparent; }}

/* The drag handle. The card is frameless, so this strip is the only place a
   drag is unambiguous: over the text a drag means "select", not "move". */
.lintranslator-grip {{ color: {TEXT_FAINT}; font-size: {FONT_SMALL}px; }}

/* The resize strips along the east, south and south-east edges. They paint
   almost nothing on purpose - the cursor is the affordance - but they must
   paint *something*, because GTK4 picks a widget through its render nodes and
   an entirely unpainted box is not hit-testable. 2% white is invisible on the
   card and still gives the node something to draw. */
/* The resize grips carry the class `.lintranslator-grip-area` (see panel.py) and
   deliberately get no declarations here. They must not paint: a strip along the
   card's edge shows as a faint inner band, and 2% white - the least that could
   be called visible - measured rgb(20,21,24) -> rgb(25,26,29) across the outer
   6px. Painting is not needed for the drag to arrive: GTK4 still hit-tests a
   widget with a size request but no background, which
   `probe/panel_resize_check.py` checks with `Gtk.Widget.pick()`. */

.lintranslator-target {{
  color: #ffffff;
  font-size: {target_px}px;
  font-weight: 600;
}}
.lintranslator-source {{
  color: {TEXT_DIM};
  font-size: {source_px}px;
}}
/* The gap above the original text. On the viewport, not on the label: a margin
   on the label is inside the scroller, so it would eat into the height reserved
   for the text and clip the last line. */
.lintranslator-source-area {{ margin-top: {SPACE_SM}px; }}

/* A setup notice borrows the translation area (see panel._show_notice) but must
   not look like a translation: smaller, not bold, and in a warning colour. Two
   classes deep so it wins over `.lintranslator-target` on specificity rather than on
   source order. */
.lintranslator-textarea .lintranslator-notice {{
  color: {WARN};
  font-size: {FONT_BODY}px;
  font-weight: 400;
}}
.lintranslator-textarea .lintranslator-notice-error {{
  color: {ERROR};
  font-size: {FONT_BODY}px;
  font-weight: 400;
}}

/* The text areas scroll rather than resize the card, so their viewports must
   not paint a background of their own or claim space they were not given. */
.lintranslator-textarea {{ background: transparent; border: none; }}
.lintranslator-textarea scrollbar {{ background: transparent; }}
.lintranslator-textarea scrollbar slider {{
  background-color: rgba(255, 255, 255, 0.22);
  border-radius: 3px;
  min-width: 5px;
  min-height: 24px;
}}
.lintranslator-textarea scrollbar slider:hover {{ background-color: rgba(255, 255, 255, 0.36); }}

/* ===================== picker and settings ===================== */
window.lintranslator-app {{ background-color: {SURFACE_SOLID}; }}

/* ---- the stock controls ---- */
/* Everything below is painted explicitly, because the toolkit would otherwise
   paint it from the desktop theme. On this machine that theme is Breeze
   *light*, so inside windows this stylesheet had already made dark, the row
   labels came out dark grey, the scale values were all but invisible, the
   buttons were light blocks and the checkboxes were empty white squares.
   `gtk-application-prefer-dark-theme` does not fix it - Breeze loads no dark
   variant here - so the app does not rely on it.

   Two specificity notes, both load-bearing:
     * `window.lintranslator-app label` is (0,1,2), which is *higher* than `.lintranslator-hint`
       (0,1,0). Every semantic class below is therefore re-stated scoped to
       `.lintranslator-app` (0,2,1) so the type hierarchy survives the reset.
     * `window.lintranslator-app button` (0,1,2) would likewise beat `button.lintranslator-primary`
       (0,1,1), so the button variants are re-stated as
       `window.lintranslator-app button.lintranslator-primary` (0,2,2) rather than relying on
       source order.

   The panel is `window.lintranslator-panel`, not `.lintranslator-app`, so none of this touches
   it; the picker's hand-drawn cairo area is not a widget and is not matched. */
window.lintranslator-app label {{ color: {TEXT}; }}
window.lintranslator-app .lintranslator-section {{ color: {TEXT}; font-weight: 700; }}
window.lintranslator-app .lintranslator-dim {{ color: {TEXT_DIM}; }}
window.lintranslator-app .lintranslator-hint {{ color: {TEXT_DIM}; }}
window.lintranslator-app .lintranslator-status {{ color: {TEXT_DIM}; }}
window.lintranslator-app .lintranslator-caption {{ color: {TEXT_FAINT}; }}
window.lintranslator-app .lintranslator-error {{ color: {ERROR}; }}
window.lintranslator-app .lintranslator-warn {{ color: {WARN}; }}
window.lintranslator-app .lintranslator-ok {{ color: {OK}; }}

window.lintranslator-app button {{
  background-color: rgba(255, 255, 255, 0.08);
  background-image: none;
  color: {TEXT};
  border: 1px solid {BORDER};
  border-radius: {RADIUS_CONTROL}px;
}}
window.lintranslator-app button:hover {{ background-color: rgba(255, 255, 255, 0.15); }}
window.lintranslator-app button:active,
window.lintranslator-app button:checked {{ background-color: rgba(255, 255, 255, 0.22); }}
window.lintranslator-app button:disabled {{ color: {TEXT_FAINT}; }}
window.lintranslator-app button:focus {{ outline: none; }}
window.lintranslator-app button.lintranslator-primary {{
  background-color: {ACCENT};
  color: {ACCENT_TEXT};
  border-color: {ACCENT};
}}
window.lintranslator-app button.lintranslator-primary:hover {{
  background-color: #a5d5ff;
  border-color: #a5d5ff;
}}
window.lintranslator-app button.lintranslator-tool {{ padding: 4px 14px; min-height: {CONTROL_H}px; }}

window.lintranslator-app entry,
window.lintranslator-app spinbutton {{
  background-color: {SURFACE_SUNKEN};
  background-image: none;
  color: {TEXT};
  border: 1px solid {BORDER};
  border-radius: {RADIUS_CONTROL}px;
}}
window.lintranslator-app dropdown > button {{
  background-color: rgba(255, 255, 255, 0.08);
  color: {TEXT};
}}
window.lintranslator-app checkbutton {{ color: {TEXT}; }}
/* Without this the indicator is drawn by the light theme: a filled white square
   that reads as "already ticked" whether or not it is. */
window.lintranslator-app checkbutton > check {{
  background-color: {SURFACE_SUNKEN};
  background-image: none;
  border: 1px solid {BORDER};
  border-radius: 3px;
  min-width: 14px;
  min-height: 14px;
}}
window.lintranslator-app checkbutton > check:checked {{
  background-color: {ACCENT};
  border-color: {ACCENT};
  color: {ACCENT_TEXT};
}}

window.lintranslator-app scale value {{ color: {TEXT_DIM}; }}
window.lintranslator-app scale trough {{
  background-color: {SURFACE_SUNKEN};
  border: none;
  border-radius: 3px;
  min-height: 6px;
}}
/* The theme's accent is a bright orange that belongs to no colour in this app. */
window.lintranslator-app scale highlight {{
  background-color: {ACCENT};
  border-radius: 3px;
}}
window.lintranslator-app scale slider {{
  background-color: {TEXT};
  background-image: none;
  border: none;
  min-width: 14px;
  min-height: 14px;
  border-radius: 7px;
}}
window.lintranslator-app separator {{ background-color: {BORDER_SOFT}; }}
window.lintranslator-app popover > contents {{
  background-color: #16171b;
  color: {TEXT};
  border: 1px solid {BORDER};
  border-radius: {RADIUS_CONTROL + 2}px;
}}

.lintranslator-side {{
  background-color: {SURFACE_SOLID};
  border-left: 1px solid {BORDER_SOFT};
  padding: {SPACE_LG}px;
}}
.lintranslator-toolbar {{
  background-color: {SURFACE_SOLID};
  border-top: 1px solid {BORDER_SOFT};
  padding: {SPACE_MD}px;
}}
button.lintranslator-tool {{
  padding: 4px 14px;
  min-height: {CONTROL_H}px;
  font-size: {FONT_BODY}px;
  border-radius: {RADIUS_CONTROL}px;
}}
/* Reading panes: the OCR text and the translation preview. Sunken so they read
   as output rather than as something to type into. */
.lintranslator-readout {{
  background-color: {SURFACE_SUNKEN};
  border: 1px solid {BORDER_SOFT};
  border-radius: {RADIUS_CONTROL}px;
  padding: {SPACE_SM}px {SPACE_MD}px;
}}
.lintranslator-frame {{
  border: 1px solid {BORDER_SOFT};
  border-radius: {RADIUS_CONTROL}px;
  padding: {SPACE_MD}px;
}}
"""


def install(css: str, display=None):
    """Load `css` as the application stylesheet, replacing any previous one.

    Adding a provider at the same priority replaces the one already there, so
    repeated calls (every Settings apply) do not stack stylesheets.

    The app asks for the dark variant of the desktop theme as well. Every window
    here is a dark tool that sits over a game, and the fallback is not neutral:
    on this machine the theme is Breeze *light*, so a dropdown or an entry that
    nobody has styled comes out white. Widgets this module does not classify
    still get a theme, and this makes that theme the one the rest of the UI was
    designed against.

    Returns the provider, which the caller must keep alive: GTK does not own it.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)

    provider = Gtk.CssProvider()
    # `load_from_data` is deprecated in GTK 4.12+ in favour of the bytes form.
    # Kept as a fallback rather than a hard requirement: the project supports
    # whatever GTK the machine has, and a warning is better than a crash.
    if hasattr(provider, "load_from_bytes"):
        provider.load_from_bytes(GLib.Bytes.new(css.encode()))
    else:  # pragma: no cover - only on GTK < 4.12
        provider.load_from_data(css.encode())
    Gtk.StyleContext.add_provider_for_display(
        display or Gdk.Display.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return provider


def install_for(config, display=None):
    """Build and install the stylesheet for a `DisplayConfig`."""
    display_cfg = config.display
    scale = max(0.5, min(3.0, display_cfg.font_scale))
    provider = install(
        build_css(
            target_px=round(display_cfg.base_font_size * scale),
            source_px=max(9, round(display_cfg.base_font_size * scale * 0.7)),
        ),
        display,
    )
    return provider
