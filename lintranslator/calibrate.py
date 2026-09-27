"""Automatic dialogue-box detection (library helper, not used by the GUI)."""
from __future__ import annotations

from dataclasses import dataclass

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - depends on the install
    raise ImportError(
        "lintranslator.calibrate needs numpy:\n"
        "  pip install 'lintranslator[calibrate]'"
    ) from exc

from PIL import Image

from .config import Region

EDGE_DELTA = 25  # grayscale delta that counts as a "hard" horizontal edge


@dataclass
class Calibration:
    region: Region
    screen: tuple[int, int]
    text_rows: list[tuple[int, int]]
    polarity: str
    confidence: float

    def describe(self) -> str:
        x, y, w, h = self.region.to_pixels(*self.screen)
        rows = ", ".join(f"y{a}-{b}" for a, b in self.text_rows)
        return (
            f"{self.polarity} text on {self.screen[0]}x{self.screen[1]}\n"
            f"  text rows : {rows}\n"
            f"  region    : x={x} y={y} w={w} h={h}  "
            f"(fractions {self.region.x:.4f}, {self.region.y:.4f}, "
            f"{self.region.w:.4f}, {self.region.h:.4f})\n"
            f"  confidence: {self.confidence:.0%}"
        )


@dataclass
class _Block:
    """A candidate text block with its measured statistics."""

    y0: int
    y1: int
    ink: float
    edge: float
    x0: int
    x1: int
    polarity: str
    # rows that actually carried ink; filtering must use this, not the bounding box
    text_y0: int = 0
    text_y1: int = 0

    @property
    def extent(self) -> int:
        return self.x1 - self.x0


def _row_runs(mask: np.ndarray, offset: int, min_pixels: int) -> list[tuple[int, int]]:
    """Group consecutive rows carrying at least `min_pixels` of ink."""
    counts = mask.sum(axis=1)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, count in enumerate(counts):
        if count >= min_pixels and start is None:
            start = i
        elif count < min_pixels and start is not None:
            runs.append((start + offset, i - 1 + offset))
            start = None
    if start is not None:
        runs.append((start + offset, len(counts) - 1 + offset))
    return runs


def _merge(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """Join runs separated by no more than `max_gap` blank rows."""
    if not runs:
        return []
    merged = [list(runs[0])]
    for y0, y1 in runs[1:]:
        if y0 - merged[-1][1] <= max_gap:
            merged[-1][1] = y1
        else:
            merged.append([y0, y1])
    return [(a, b) for a, b in merged]


def _widest_run(cols: np.ndarray, min_count: int, width: int) -> tuple[int | None, int | None]:
    """Widest contiguous span of columns whose count reaches `min_count`."""
    active = cols >= min_count
    best: tuple[int | None, int | None] = (None, None)
    best_len = 0
    start: int | None = None
    for i, ok in enumerate(active):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start > best_len:
                best, best_len = (start, i - 1), i - start
            start = None
    if start is not None and len(active) - start > best_len:
        best, best_len = (start, len(active) - 1), len(active) - start
    if best[0] is None or best_len < width * 0.15:
        return None, None
    return best


def _bridge(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """Join column runs separated by no more than `max_gap` columns."""
    if not runs:
        return []
    bridged = [list(runs[0])]
    for x0, x1 in runs[1:]:
        if x0 - bridged[-1][1] <= max_gap:
            bridged[-1][1] = x1
        else:
            bridged.append([x0, x1])
    return [(a, b) for a, b in bridged]


def _column_runs(cols: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, count in enumerate(cols):
        if count > 0 and start is None:
            start = i
        elif count == 0 and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(cols) - 1))
    return runs


def _auto_threshold(gray: np.ndarray, bottom_fraction: float = 0.45) -> int:
    """Pick an ink threshold from the image instead of hard-coding one."""
    region = gray[int(gray.shape[0] * (1.0 - bottom_fraction)) :, :]
    if region.size == 0:
        region = gray
    mean = float(region.mean())
    # robust max, not a percentile: dialogue ink is sparse, so a percentile
    # describes background and lands too high to find anything
    bright = float(np.percentile(region, 99.99))
    if bright - mean < 12:
        # no usable contrast: a value that finds nothing rather than inventing text
        return 250
    return int(max(70, min(220, mean + (bright - mean) * 0.55)))


def _measure(
    mask: np.ndarray,
    edges: np.ndarray,
    gray: np.ndarray,
    block: tuple[int, int],
    top: int,
    width: int,
    polarity: str,
) -> _Block | None:
    """Compute ink/edge/extent statistics for one candidate row range."""
    y0, y1 = block
    rows = mask[y0 - top : y1 - top + 1, :]
    if rows.size == 0:
        return None

    row_edges = edges[y0 : y1 + 1, :]
    edge_fraction = float(row_edges.mean())
    if edge_fraction <= 0.0:
        return None

    # lower intensity threshold than row detection: glyph edges are dimmer than
    # glyph cores, and dropping them splits one line into several column runs
    ink_cols = (gray[block[0] : block[1] + 1, :] > 110) if polarity == "light-on-dark" \
        else (gray[block[0] : block[1] + 1, :] < 145)
    cols = ink_cols.sum(axis=0)
    if cols.max() <= 0:
        return None
    col_min = max(2, int(cols.max() * 0.15))
    bridged = _bridge(_column_runs(np.where(cols >= col_min, cols, 0)), max_gap=28)
    if not bridged:
        return None
    x0, x1 = max(bridged, key=lambda r: r[1] - r[0])

    # rows that actually carry ink: `_merge` can join sparse rows across a large
    # area, so filtering on the bounding box would be wrong
    row_ink = rows.sum(axis=1)
    ink_rows = np.nonzero(row_ink >= max(1, int(width * 0.01)))[0]
    text_y0 = int(ink_rows.min()) + y0 if ink_rows.size else y0
    text_y1 = int(ink_rows.max()) + y0 if ink_rows.size else y1

    return _Block(
        y0=y0,
        y1=y1,
        ink=float(rows.mean()),
        edge=edge_fraction,
        x0=int(x0),
        x1=int(x1),
        polarity=polarity,
        text_y0=text_y0,
        text_y1=text_y1,
    )


def calibrate(
    image: Image.Image,
    *,
    bottom_fraction: float = 0.45,
    # None picks a threshold from the image
    threshold: int | None = None,
    min_row_ink: float = 0.03,
    line_gap: int = 26,
    pad_x: int = 14,
    pad_top: int = 6,
    pad_bottom: int = 6,
    min_region_height: int = 28,
    min_score: float = 0.45,
    near: tuple[int, int, int, int] | None = None,
    near_margin: float = 0.35,
) -> Calibration | None:
    """Detect the dialogue text block in a full-screen image."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    height, width = gray.shape
    if threshold is None:
        threshold = _auto_threshold(gray, bottom_fraction)
    top = int(height * (1.0 - bottom_fraction))
    if near is not None:
        # the band must start above the caller's region, or its rows are
        # excluded before the filter ever runs
        top = min(top, max(0, int(near[1]) - 40))
    band = gray[top:, :]
    row_min = max(4, int(width * min_row_ink))
    edges = np.abs(np.diff(gray.astype(np.int16), axis=1)) > EDGE_DELTA
    REF_INK, REF_EDGE = 0.06, 0.18

    allowed: tuple[int, int, int, int] | None = None
    if near is not None:
        nx, ny, nw, nh = near
        margin = min(120.0, max(24.0, min(nw, nh) * near_margin))
        allowed = (
            max(0, int(nx - margin)),
            max(0, int(ny - margin)),
            min(width, int(nx + nw + margin)),
            min(height, int(ny + nh + margin)),
        )

    scored: list[tuple[float, _Block]] = []
    for polarity, mask in (
        ("light-on-dark", band > threshold),
        ("dark-on-light", band < (255 - threshold)),
    ):
        for block in _merge(_row_runs(mask, top, row_min), line_gap):
            measured = _measure(mask, edges, gray, block, top, width, polarity)
            if measured is None:
                continue

            if allowed is not None:
                ax0, ay0, ax1, ay1 = allowed
                # overlap of the *measured text*, not the block's bounding box:
                # `_merge` can join sparse rows across the whole screen
                v_overlap = min(measured.text_y1, ay1) - max(measured.text_y0, ay0)
                h_overlap = min(measured.x1, ax1) - max(measured.x0, ax0)
                if v_overlap <= 0 or h_overlap <= 0:
                    continue

            if measured.ink <= 0:
                continue
            ink_term = min(1.0, REF_INK / measured.ink)

            edge_term = min(1.0, measured.edge / REF_EDGE)

            width_term = min(1.0, measured.extent / (width * 0.30))

            position_term = min(1.0, measured.y1 / height)

            score = (
                0.30 * ink_term
                + 0.30 * edge_term
                + 0.25 * width_term
                + 0.15 * position_term
            )
            scored.append((score, measured))

    if not scored:
        return None

    score, best = max(scored, key=lambda s: s[0])
    if score < min_score:
        return None

    y0 = max(0, best.y0 - pad_top)
    y1 = min(height, best.y1 + pad_bottom)
    if y1 - y0 < min_region_height:
        y1 = min(height, y0 + min_region_height)
    x0 = max(0, best.x0 - pad_x)
    x1 = min(width, best.x1 + pad_x)

    region = Region(
        x=x0 / width,
        y=y0 / height,
        w=(x1 - x0) / width,
        h=(y1 - y0) / height,
        mode="fraction",
    )

    mask = band > threshold if best.polarity == "light-on-dark" else band < (255 - threshold)
    text_rows = [
        r
        for r in _merge(_row_runs(mask, top, row_min), line_gap)
        if r[0] >= best.y0 and r[1] <= best.y1
    ] or [(best.y0, best.y1)]

    return Calibration(
        region=region,
        screen=(width, height),
        text_rows=text_rows,
        polarity=best.polarity,
        confidence=min(1.0, score),
    )
