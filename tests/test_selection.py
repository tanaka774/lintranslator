"""Tests for the region picker's coordinate mapping and drag handling.

These matter because a mapping bug produces a saved region that does not match
what was dragged. The symptom is bad OCR later, far from the cause.
"""
from __future__ import annotations

import pytest

from lintranslator.selection import HANDLE_WIDGET_MIN, MIN_SIZE, SelectionMath

SCREEN = (2560, 1440)


def test_scale_fits_inside_the_widget():
    m = SelectionMath(*SCREEN, widget_w=1280, widget_h=720)
    assert m.scale == pytest.approx(0.5)
    assert (m.draw_w, m.draw_h) == (1280, 720)
    assert m.offset == (0.0, 0.0)


def test_aspect_ratio_is_preserved_with_letterboxing():
    # A widget wider than the screen aspect puts bars on the left and right.
    m = SelectionMath(1000, 1000, widget_w=1000, widget_h=400)
    assert m.scale == pytest.approx(0.4)
    assert (m.draw_w, m.draw_h) == (400.0, 400.0)
    assert m.offset == (300.0, 0.0)


def test_round_trip_widget_and_screen_coordinates():
    m = SelectionMath(*SCREEN, widget_w=900, widget_h=500)
    for point in ((0, 0), (100, 50), (1280, 720), (2559, 1439)):
        wx, wy = m.to_widget(*point)
        back = m.to_screen(wx, wy)
        # int() truncation allows a 1px difference.
        assert abs(back[0] - point[0]) <= 1, point
        assert abs(back[1] - point[1]) <= 1, point


def test_points_outside_the_image_are_clamped():
    m = SelectionMath(*SCREEN, widget_w=1000, widget_h=400)  # letterboxed
    # Clicking in the left letterbox bar must not yield a negative coordinate.
    assert m.to_screen(10, 200) == (0, m.to_screen(10, 200)[1])
    assert m.to_screen(995, 200)[0] == SCREEN[0] - 1
    assert m.to_screen(500, -50)[1] == 0
    assert m.to_screen(500, 9999)[1] == SCREEN[1] - 1


def test_zero_size_widget_does_not_divide_by_zero():
    m = SelectionMath(*SCREEN, widget_w=0, widget_h=0)
    assert m.scale == 0.0
    assert m.to_screen(10, 10) == (0, 0)


# --------------------------------------------------------------------------- #
# Hit testing
# --------------------------------------------------------------------------- #
def _centered_math() -> SelectionMath:
    # 1:1 mapping keeps the expectations readable.
    return SelectionMath(*SCREEN, widget_w=2560, widget_h=1440)


def test_hit_test_without_a_selection_is_new():
    assert _centered_math().hit_test(100, 100, None) == "new"


def test_hit_test_inside_is_move_and_outside_is_new():
    m = _centered_math()
    sel = (1000, 700, 400, 100)  # x 1000..1400, y 700..800
    assert m.hit_test(1200, 750, sel) == "move"
    assert m.hit_test(100, 100, sel) == "new"


def test_hit_test_edges_and_corners():
    m = _centered_math()
    sel = (1000, 700, 400, 100)
    assert m.hit_test(1000, 750, sel) == "w"
    assert m.hit_test(1400, 750, sel) == "e"
    assert m.hit_test(1200, 700, sel) == "n"
    assert m.hit_test(1200, 800, sel) == "s"
    assert m.hit_test(1000, 700, sel) == "nw"
    assert m.hit_test(1400, 700, sel) == "ne"
    assert m.hit_test(1000, 800, sel) == "sw"
    assert m.hit_test(1400, 800, sel) == "se"


def test_handle_tolerance_scales_with_zoom_but_never_becomes_a_pixel_hunt():
    """Zoomed in, an 8px screenshot handle is generous on screen and must be
    converted rather than applied raw.

    Zoomed out it goes the other way: 8 screenshot px of a 2560-wide shot is
    under 2 widget px once the shot is letterboxed into the canvas, which is a
    pixel hunt - so the converted tolerance is floored at what a pointer can
    reasonably be asked to hit.
    """
    zoomed_in = SelectionMath(100, 100, widget_w=1000, widget_h=1000)
    tol_x_in, _ = zoomed_in.handle_tolerance(8)
    assert tol_x_in == pytest.approx(80)  # 8 screen px, converted

    zoomed_out = SelectionMath(*SCREEN, widget_w=640, widget_h=360)
    tol_x, _ = zoomed_out.handle_tolerance(8)
    assert tol_x == pytest.approx(HANDLE_WIDGET_MIN)
    assert tol_x > 8 * zoomed_out.scale, "the raw conversion is not a usable target"


def test_hit_test_reports_a_new_box_without_a_usable_canvas():
    """With no canvas size every point maps to (0, 0), which is within tolerance
    of every edge at once - so a press anywhere would claim to be a corner grab
    and quietly resize instead of starting the box the user is drawing."""
    m = SelectionMath(*SCREEN, widget_w=0, widget_h=0)
    assert m.hit_test(10, 10, (1000, 700, 400, 100)) == "new"


# --------------------------------------------------------------------------- #
# Drag application
# --------------------------------------------------------------------------- #
def test_new_drag_normalises_a_reversed_drag():
    # Dragging up-and-left must still produce a positive width/height.
    got = SelectionMath.apply_drag("new", None, (500, 400), (300, 200), SCREEN)
    assert got == (300, 200, 200, 200)


def test_new_drag_is_clamped_to_the_screen():
    got = SelectionMath.apply_drag("new", None, (2500, 1400), (9999, 9999), SCREEN)
    assert got[0] + got[2] <= SCREEN[0]
    assert got[1] + got[3] <= SCREEN[1]


def test_resize_east_moves_only_the_right_edge():
    sel = (100, 100, 200, 100)
    got = SelectionMath.apply_drag("e", sel, (300, 150), (400, 150), SCREEN)
    assert got == (100, 100, 300, 100)


def test_resize_west_moves_only_the_left_edge():
    sel = (100, 100, 200, 100)
    got = SelectionMath.apply_drag("w", sel, (100, 150), (50, 150), SCREEN)
    assert got == (50, 100, 250, 100)


# The box and travel these cases use, so the whole table reads as one gesture
# being repeated against each edge: press on the edge, move 120px along it.
BOX = (1000, 700, 400, 200)
STEP = 120


@pytest.mark.parametrize(
    "mode,press,release,expected",
    [
        # towards the opposite edge (the case that used to do nothing) ...
        ("e", (1400, 800), (1400 - STEP, 800), (1000, 700, 400 - STEP, 200)),
        ("w", (1000, 800), (1000 + STEP, 800), (1000 + STEP, 700, 400 - STEP, 200)),
        ("n", (1200, 700), (1200, 700 + STEP), (1000, 700 + STEP, 400, 200 - STEP)),
        ("s", (1200, 900), (1200, 900 - STEP), (1000, 700, 400, 200 - STEP)),
        ("nw", (1000, 700), (1000 + STEP, 700 + STEP), (1000 + STEP, 700 + STEP, 400 - STEP, 200 - STEP)),
        ("ne", (1400, 700), (1400 - STEP, 700 + STEP), (1000, 700 + STEP, 400 - STEP, 200 - STEP)),
        ("sw", (1000, 900), (1000 + STEP, 900 - STEP), (1000 + STEP, 700, 400 - STEP, 200 - STEP)),
        ("se", (1400, 900), (1400 - STEP, 900 - STEP), (1000, 700, 400 - STEP, 200 - STEP)),
        # ... and away from it
        ("e", (1400, 800), (1400 + STEP, 800), (1000, 700, 400 + STEP, 200)),
        ("w", (1000, 800), (1000 - STEP, 800), (1000 - STEP, 700, 400 + STEP, 200)),
        ("n", (1200, 700), (1200, 700 - STEP), (1000, 700 - STEP, 400, 200 + STEP)),
        ("s", (1200, 900), (1200, 900 + STEP), (1000, 700, 400, 200 + STEP)),
        ("nw", (1000, 700), (1000 - STEP, 700 - STEP), (1000 - STEP, 700 - STEP, 400 + STEP, 200 + STEP)),
        ("ne", (1400, 700), (1400 + STEP, 700 - STEP), (1000, 700 - STEP, 400 + STEP, 200 + STEP)),
        ("sw", (1000, 900), (1000 - STEP, 900 + STEP), (1000 - STEP, 700, 400 + STEP, 200 + STEP)),
        ("se", (1400, 900), (1400 + STEP, 900 + STEP), (1000, 700, 400 + STEP, 200 + STEP)),
    ],
)
def test_every_edge_and_corner_resizes_in_both_directions(mode, press, release, expected):
    """Regression: dragging an edge *towards* the one opposite it did nothing.

    `apply_drag` rebuilt the moving edge from min()/max() of the two pointer
    positions, so the edge was pinned to where the pointer landed whenever the
    drag headed back across the box: the box could be grown but never shrunk, on
    any edge or corner. Both directions are the same gesture and must work.
    """
    assert SelectionMath.apply_drag(mode, BOX, press, release, SCREEN) == expected


def test_resize_keeps_the_offset_the_edge_was_grabbed_with():
    """The pointer may land up to a handle's tolerance inside the edge. The edge
    must then travel with the pointer rather than jump under it on the way down -
    a jump makes the first pixel of every resize a surprise."""
    inside = 5
    got = SelectionMath.apply_drag(
        "e", BOX, (1400 - inside, 800), (1400 - inside + STEP, 800), SCREEN
    )
    assert got == (1000, 700, 400 + STEP, 200), "the edge did not keep its grab offset"


def test_resize_leaves_the_opposite_edge_exactly_where_it_was():
    for mode, press, release in (
        ("n", (1200, 700), (1200, 760)),
        ("s", (1200, 900), (1200, 840)),
        ("e", (1400, 800), (1340, 800)),
        ("w", (1000, 800), (1060, 800)),
    ):
        x0, y0, w, h = SelectionMath.apply_drag(mode, BOX, press, release, SCREEN)
        if mode in ("n", "s"):
            assert (x0, w) == (1000, 400), f"{mode} moved a vertical edge sideways"
        else:
            assert (y0, h) == (700, 200), f"{mode} moved a horizontal edge"


def test_a_resize_cannot_invert_or_degenerate_the_box():
    """Dragging an edge past the one opposite has to stop at the minimum size:
    an inverted or empty box crops to nothing and the crop is what OCR is handed."""
    for mode, press, release in (
        ("e", (1400, 800), (900, 800)),
        ("w", (1000, 800), (1600, 800)),
        ("n", (1200, 700), (1200, 1200)),
        ("s", (1200, 900), (1200, 400)),
        ("se", (1400, 900), (900, 400)),
        ("nw", (1000, 700), (1600, 1200)),
    ):
        x0, y0, w, h = SelectionMath.apply_drag(mode, BOX, press, release, SCREEN)
        assert w >= MIN_SIZE and h >= MIN_SIZE, mode
        assert x0 + w <= SCREEN[0] and y0 + h <= SCREEN[1], mode
        if mode in ("e",):
            assert x0 == 1000, "the fixed edge moved while the box was clamped"
        if mode in ("n",):
            assert y0 + h == 900, "the fixed edge moved while the box was clamped"


def test_a_resize_depends_only_on_the_drag_not_on_how_it_was_reported():
    """GtkGestureDrag reports cumulative offsets, one event per mouse-move, and
    the count differs with mouse speed. Splitting the same drag into one event or
    ten must land on the same box - feeding each event the box as it changes
    instead makes the edge accelerate away from the pointer."""
    press, release = (1400, 800), (1180, 860)
    once = SelectionMath.apply_drag("se", BOX, press, release, SCREEN)

    box = BOX
    steps = 10
    for i in range(1, steps + 1):
        box = SelectionMath.apply_drag(
            "se",
            BOX,  # the box the drag started from, as the picker passes it
            press,
            (
                press[0] + (release[0] - press[0]) * i // steps,
                press[1] + (release[1] - press[1]) * i // steps,
            ),
            SCREEN,
        )
    assert box == once, "the same drag gave two different boxes"


def test_move_preserves_size_and_clamps_to_screen():
    sel = (100, 100, 200, 100)
    got = SelectionMath.apply_drag("move", sel, (150, 150), (250, 250), SCREEN)
    assert got[2:] == (200, 100)  # size unchanged
    assert got[:2] == (200, 200)

    # Pushing far past the right edge pins it against the edge.
    got = SelectionMath.apply_drag("move", sel, (0, 0), (99999, 0), SCREEN)
    assert got[0] + got[2] <= SCREEN[0]


def test_drag_can_never_produce_a_degenerate_selection():
    """A zero-width selection would crop to an empty image and crash OCR."""
    for mode in ("new", "n", "s", "e", "w", "move"):
        got = SelectionMath.apply_drag(mode, (100, 100, 200, 100), (150, 150), (150, 150), SCREEN)
        assert got[2] >= 1 and got[3] >= 1, mode


def test_move_is_incremental_not_cumulative():
    """Regression: GestureDrag reports cumulative offsets. Applying `move` with
    the cumulative offset on every event makes the selection accelerate away
    from the pointer, so the caller must feed per-event deltas.

    Feeding deltas keeps the selection exactly under the pointer.
    """
    sel = (1000, 700, 200, 100)
    # Pointer moves 10px right, three events in a row.
    for _ in range(3):
        sel = SelectionMath.apply_drag("move", sel, (0, 0), (10, 0), SCREEN)
    assert sel[:2] == (1030, 700), "three 10px moves should shift exactly 30px"

    # The buggy cumulative form: each event shifts by the whole offset again.
    cumulative = (1000, 700, 200, 100)
    for offset in (10, 20, 30):
        cumulative = SelectionMath.apply_drag(
            "move", cumulative, (0, 0), (offset, 0), SCREEN
        )
    assert cumulative[0] == 1060, "cumulative application over-shoots"
