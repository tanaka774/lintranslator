"""Coordinate mapping for the region picker."""
from __future__ import annotations

from dataclasses import dataclass

# smallest box a resize may leave, in screen pixels; a zero-width selection
# would crop to an empty image and crash OCR
MIN_SIZE = 4
# how close the pointer may land to an edge and still grab it, in widget pixels
HANDLE_WIDGET_MIN = 8.0


@dataclass
class SelectionMath:
    """Maps between screen pixels and widget space for a letterboxed screenshot."""

    screen_w: int
    screen_h: int
    widget_w: float
    widget_h: float

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
        """Hit-test tolerance in widget units, derived from screen pixels."""
        if self.scale <= 0:
            return (float(handle_px), float(handle_px))
        tol = max(handle_px * self.scale, HANDLE_WIDGET_MIN)
        return (tol, tol)

    def hit_test(
        self, wx: float, wy: float, sel: tuple[int, int, int, int] | None, handle_px: int = 8
    ) -> str:
        """Which part of the selection is under the pointer: new, move, n, s, e, w, ne, nw, se, sw."""
        # without a usable mapping every point converts to (0, 0), which is within
        # tolerance of every edge at once, so nothing can be grabbed
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

    @staticmethod
    def apply_drag(
        mode: str,
        sel: tuple[int, int, int, int] | None,
        start: tuple[int, int],
        end: tuple[int, int],
        bounds: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Apply a drag to a selection, returning a normalised (x, y, w, h)."""
        left, right = sorted((start[0], end[0]))
        top, bottom = sorted((start[1], end[1]))
        screen_w, screen_h = bounds

        if mode == "new" or sel is None:
            x0, y0 = left, top
            w, h = max(1, right - left), max(1, bottom - top)
        else:
            x0, y0, w, h = sel
            x1, y1 = x0 + w, y0 + h
            # fixed edges, captured before either moving edge is written: every
            # clamp below measures against the edge that is not moving
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
