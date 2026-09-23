"""Coordinate mapping for the region picker.

Pure geometry, deliberately kept out of the widget code so it can be tested
without a display. Getting this wrong means saving a region that does not match
what the user dragged - a bug that only shows up as bad OCR later, which is a
miserable thing to debug.
"""
from __future__ import annotations

from dataclasses import dataclass

# The smallest box a resize may leave, in screen pixels. A zero-width selection
# would crop to an empty image and crash OCR.
MIN_SIZE = 4
# How close the pointer may land to an edge and still grab it, in *widget*
# pixels. See `handle_tolerance`.
HANDLE_WIDGET_MIN = 8.0


@dataclass
class SelectionMath:
    """Maps between screen pixels and widget space for a letterboxed screenshot.

    The screenshot is scaled to fit the widget while preserving aspect ratio and
    centred, so widget space has margins on one axis (letterboxing).
    """

    screen_w: int
    screen_h: int
    widget_w: float
    widget_h: float

    # -- image placement --------------------------------------------------- #
    @property
    def scale(self) -> float:
        if self.screen_w <= 0 or self.screen_h <= 0:
            return 0.0
        return min(self.widget_w / self.screen_w, self.widget_h / self.screen_h)

    @property
    def draw_w(self) -> float:
        return self.screen_w * self.scale

    @property
    def draw_h(self) -> float:
        return self.screen_h * self.scale

    @property
    def offset(self) -> tuple[float, float]:
        return ((self.widget_w - self.draw_w) / 2, (self.widget_h - self.draw_h) / 2)

    # -- conversions ------------------------------------------------------- #
    def to_screen(self, wx: float, wy: float) -> tuple[int, int]:
        """Widget point -> screen pixel, clamped to the screenshot."""
        if self.scale <= 0:
            return (0, 0)
        ox, oy = self.offset
        sx = (wx - ox) / self.scale
        sy = (wy - oy) / self.scale
        return (
            max(0, min(int(sx), self.screen_w - 1)),
            max(0, min(int(sy), self.screen_h - 1)),
        )

    def to_widget(self, sx: float, sy: float) -> tuple[float, float]:
        """Screen pixel -> widget point."""
        ox, oy = self.offset
        return (ox + sx * self.scale, oy + sy * self.scale)

    def handle_tolerance(self, handle_px: int = 8) -> tuple[float, float]:
        """Hit-test tolerance in widget units, derived from screen pixels.

        Floored, because a screenshot tolerance is not a *pointer* tolerance: on a
        2560-wide screen letterboxed into the picker's canvas, 8 screen px works
        out at under 4 px of mouse travel, which is a pixel hunt. Never harder
        than `handle_px` of the screenshot, never harder than HANDLE_WIDGET_MIN on
        screen.
        """
        if self.scale <= 0:
            return (float(handle_px), float(handle_px))
        tol = max(handle_px * self.scale, HANDLE_WIDGET_MIN)
        return (tol, tol)

    # -- hit testing ------------------------------------------------------- #
    def hit_test(
        self, wx: float, wy: float, sel: tuple[int, int, int, int] | None, handle_px: int = 8
    ) -> str:
        """Which part of the selection is under the pointer.

        Returns one of: new, move, n, s, e, w, ne, nw, se, sw.
        """
        # Without a usable mapping every point converts to (0, 0), which lands
        # within tolerance of every edge at once - a press anywhere would report
        # a corner. There is nothing to grab until the canvas has a size.
        if sel is None or self.scale <= 0:
            return "new"
        x0, y0, w, h = sel
        x1, y1 = x0 + w, y0 + h
        sx, sy = self.to_screen(wx, wy)
        tol_x, tol_y = self.handle_tolerance(handle_px)

        near_left = abs(sx - x0) * self.scale <= tol_x
        near_right = abs(sx - x1) * self.scale <= tol_x
        near_top = abs(sy - y0) * self.scale <= tol_y
        near_bottom = abs(sy - y1) * self.scale <= tol_y
        inside_x = x0 <= sx <= x1
        inside_y = y0 <= sy <= y1

        if near_left and near_top:
            return "nw"
        if near_right and near_top:
            return "ne"
        if near_left and near_bottom:
            return "sw"
        if near_right and near_bottom:
            return "se"
        if near_left and inside_y:
            return "w"
        if near_right and inside_y:
            return "e"
        if near_top and inside_x:
            return "n"
        if near_bottom and inside_x:
            return "s"
        if inside_x and inside_y:
            return "move"
        return "new"

    # -- drag application -------------------------------------------------- #
    @staticmethod
    def apply_drag(
        mode: str,
        sel: tuple[int, int, int, int] | None,
        start: tuple[int, int],
        end: tuple[int, int],
        bounds: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Apply a drag to a selection, returning a normalised (x, y, w, h).

        `sel` is the box the drag applies to, `start` and `end` are where the
        pointer went down and where it is now, and only their difference is used -
        so the caller may pass either the box from the start of the drag with the
        total offset, or the current box with the offset since the last event.

        A grabbed edge is moved by that offset; the one opposite it does not move
        at all. This used to rebuild the edge from min()/max() of the two pointer
        positions, which pins it whenever the drag heads towards the opposite
        edge: only outward drags moved, and shrinking the box did nothing.
        """
        left, right = sorted((start[0], end[0]))
        top, bottom = sorted((start[1], end[1]))
        screen_w, screen_h = bounds

        if mode == "new" or sel is None:
            x0, y0 = left, top
            w, h = max(1, right - left), max(1, bottom - top)
        else:
            x0, y0, w, h = sel
            x1, y1 = x0 + w, y0 + h
            # The fixed edges, kept before either moving edge is written: every
            # clamp below has to measure against the edge that is not moving.
            fixed_x0, fixed_y0 = x0, y0
            dx, dy = end[0] - start[0], end[1] - start[1]
            if mode == "move":
                x0 = max(0, min(x0 + dx, screen_w - w))
                y0 = max(0, min(y0 + dy, screen_h - h))
                x1, y1 = x0 + w, y0 + h
            else:
                if "w" in mode:
                    x0 = min(x0 + dx, x1 - MIN_SIZE)
                if "e" in mode:
                    x1 = max(x1 + dx, fixed_x0 + MIN_SIZE)
                if "n" in mode:
                    y0 = min(y0 + dy, y1 - MIN_SIZE)
                if "s" in mode:
                    y1 = max(y1 + dy, fixed_y0 + MIN_SIZE)
            w, h = max(MIN_SIZE, x1 - x0), max(MIN_SIZE, y1 - y0)

        x0 = max(0, min(x0, screen_w - 1))
        y0 = max(0, min(y0, screen_h - 1))
        w = max(1, min(w, screen_w - x0))
        h = max(1, min(h, screen_h - y0))
        return (int(x0), int(y0), int(w), int(h))
