# Phase 2 results — GTK4 GUI

Toolkit: GTK4 4.22 + PyGObject, already present on this system. PySide6 was
rejected to avoid a 200 MB dependency for two windows.

## What was built

| Piece | File | State |
|---|---|---|
| Region picker | `tlkun/picker.py` | verified by offscreen render |
| Translation panel | `tlkun/panel.py` | verified by offscreen render |
| App entry point | `tlkun/gui.py` | both modes smoke-tested |
| Picker geometry | `tlkun/selection.py` | 15 unit tests |
| Pipeline loop | `tests/test_pipeline.py` | 4 end-to-end tests, capture+OCR stubbed |
| CLI | `tlkun/cli.py` (`gui` subcommand) | verified |

Rendered output (the only practical way to verify UI on Wayland, since a client
cannot screenshot its own window from outside):

* panel: frameless card, dimmed source text above, translation below, status
  line, copy/pause/quit — `data/panel_render.png`
* picker: screenshot canvas with the selection rectangle over the dialogue text,
  sidebar with OCR preview at 94% confidence — `data/picker_render.png`

## Design decisions

**Picker = frozen screenshot in a normal window, not a transparent overlay.**
Capturing the mouse over a live fullscreen game on Wayland needs an
override-redirect surface or a compositor grab, both restricted. A frozen
screenshot sidesteps that, works on every compositor, and is genuinely better
here: the image cannot move while you drag.

**Capture on demand, not at launch.** A countdown races the user. A
"Capture again" button lets them alt-tab to the game and grab deliberately.

**Panel is a normal keep-above window.** Wayland forbids client-side positioning
and `gtk4-layer-shell` is not installable, so the panel cannot pin itself under
the dialogue box. `--position x,y` works under `GDK_BACKEND=x11`.

**Pipeline runs in a worker thread**, communicating with GTK over a queue drained
by a `GLib.timeout_add`. GTK is not thread-safe and OCR/translation take seconds.

## Bugs found and fixed

### 1. The picker canvas rendered nothing (three separate causes)

This took the longest and was worth it — the symptom was a uniformly blank grey
rectangle with no error anywhere.

* **Canvas allocated 0px wide.** The sidebar's fixed 340px request plus margins
  consumed the entire 1180px default window, leaving the canvas exactly nothing.
  A `DrawingArea` with no size request has no minimum, so it silently got 0.
* **Root box never expanded.** Without `hexpand`, the root `Gtk.Box` kept its
  natural size and GTK centred the whole 840px layout inside a 1500px window.
  The canvas looked starved but was actually sized correctly for the box it was in.
* **Sidebar requested 960px.** A wrapping `Gtk.Label` reports its *unwrapped*
  width as its natural size, so the sidebar demanded 960px and starved the canvas
  even after the root was fixed. Fixed by capping each label with
  `set_max_width_chars(34)` and wrapping the sidebar in a `ScrolledWindow` with
  `set_propagate_natural_width(False)`. Both are needed; removing the scroller
  drops the canvas straight back to 520px.

Verified final layout at 1500x980: `canvas=921x980`, image scaled to `921x523`,
selection maps to widget `(161,667)-(889,710)` inside the image, not the letterbox.

### 2. `Gtk.Picture.set_paintable` rejected the preview image

`GdkPixbuf` is not a `Gdk.Paintable`; the preview needs a `Gdk.Texture`. The
cairo canvas and the `Gtk.Picture` preview want different types, so both
converters now exist with a comment explaining why.

### 3. Selection could run away from the pointer

`Gtk.GestureDrag` reports **cumulative** offsets from the drag start. Feeding that
to a `move` on every event shifts the selection by the total drag distance each
time, so it accelerates away. Moves now track the previous event position and
apply per-event deltas, while edge resizes keep using the cumulative offset (which
is what they want). Pinned by `test_move_is_incremental_not_cumulative`.

### 4. Screenshot drawn far smaller than the selection rectangle

Reported from a real run on a 2560x1440 screen: the image occupied a small patch
in the canvas corner while the yellow selection box sat correctly over it, so the
saved region did not match what looked selected.

Cause: the screenshot was pre-scaled into a buffer sized by the computed draw
rect, and then also scaled again by a cairo transform. Two places computed the
displayed size, and they disagreed - the image rendered at a small fraction of
the rect the selection maths used. The selection maths was correct all along;
only the painting was wrong.

Fix: paint the screenshot at full resolution and let a single cairo transform
scale it to the draw rect. There is now no second size to get out of sync.

Verified invariant, checked at three window sizes: the painted image width always
equals the canvas width, and the selection always maps inside it.

```
default  canvas=921x980   scale=0.6136  image=921x523   sel_inside=True
small    canvas=520x700   scale=0.3464  image=520x295   sel_inside=True
large    canvas=1121x1000 scale=0.7468  image=1121x636  sel_inside=True
```

### 5. `_on_draw`'s width/height are not the allocation

The callback's parameters can be the widget's natural size rather than the space
it was given; the draw code now reads `area.get_width()` directly.

## Verification limits

* GTK window rendering needs a GPU renderer for texture nodes; this environment
  falls back to `Gsk.CairoRenderer`, so the canvas is drawn via a cairo image
  surface instead of a `GdkTexture` node. That is a reasonable choice regardless
  and made the offscreen render work.
* Drag interactions were not exercised with a real mouse. The geometry they call
  into is unit-tested (15 tests in `tests/test_selection.py`).
* The `GtkGizmo ... reported min height -2` warning seen during picker rendering
  is GTK-internal: it reproduces in a minimal `Gtk.ScrolledWindow` app with no
  tl-kun code involved.

## Reproduce

```bash
.venv-gi/bin/python -m pytest tests/ -q                       # 41 tests
.venv-gi/bin/python -m tlkun gui --demo --no-start --screenshot /tmp/panel.png
.venv-gi/bin/python -m tlkun gui --pick --from-file probe/shot.webp --screenshot /tmp/picker.png
.venv-gi/bin/python -m tlkun gui --pick --no-capture          # opens, no screen grab
```

## Follow-up found while finishing

The model cache had grown back to 4.7 GB because NLLB ships both safetensors and
`.bin` weights. Forcing `use_safetensors=True` to avoid the duplicate fails on
this stack:

    RuntimeError: Tensor on device cpu is not on the expected device meta!

That is a torch 2.14 meta-device regression, not a tl-kun bug, so the default
loader is kept and both weight formats stay on disk. Phase 3's CTranslate2 int8
conversion is the real fix (~600 MB, several times faster inference).
