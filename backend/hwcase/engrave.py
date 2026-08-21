"""Decoration for the front panel, as a separate pass the cutter understands.

A laser does two different jobs and it is worth being clear which is which.
A **cut** goes through the sheet and changes the shape of the part. An
**engrave** is a raster or a low-power vector pass that marks the surface and
changes nothing structural. Mixing them up is how a panel ends up with a
speaker grill sawn clean out of it.

So engraving lives in its own layer of the export, never merged with the
outline, and the geometry here is generated with that in mind: a grill is a
field of marks with a real gap between them, sized so that if somebody *does*
decide to cut it through, the panel still holds together.

The patterns are the ones that actually look like something on a machine
front: the heat-sink fins you get on an amplifier, a ventilation slot field, a
hairline groove that separates one control group from the next, and plain
text for labelling.
"""

from __future__ import annotations

import math
from typing import Optional

from shapely.affinity import rotate, translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from .schema import Engraving, Pattern

__all__ = ["build_engraving", "engraving_area"]


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------

def _cap(line: LineString, width: float, round_ends: bool) -> Polygon:
    return line.buffer(width / 2.0, cap_style=1 if round_ends else 2,
                       join_style=1, quad_segs=8)


def _fins(w: float, h: float, e: Engraving) -> list[Polygon]:
    """Parallel fins running the long way.

    Along the long axis on purpose: fins that run the short way across a wide
    panel read as a barcode rather than as a heatsink.
    """
    out = []
    horizontal = w >= h
    span = h if horizontal else w
    n = max(1, int(span // max(e.pitch, 1e-6)))
    # centre the field rather than starting at one edge, so a fin field always
    # looks deliberate whatever size the region is
    used = (n - 1) * e.pitch
    start = -used / 2.0
    for i in range(n):
        t = start + i * e.pitch
        if horizontal:
            line = LineString([(-w / 2, t), (w / 2, t)])
        else:
            line = LineString([(t, -h / 2), (t, h / 2)])
        out.append(_cap(line, e.stroke, e.round_ends))
    return out


def _slots(w: float, h: float, e: Engraving) -> list[Polygon]:
    """Rows of short slots, offset row to row like a real grill."""
    out = []
    slot = max(e.pitch * 2.0, e.stroke * 3.0)
    row_pitch = e.pitch * 2.0
    col_pitch = slot + e.pitch

    rows = max(1, int(h // row_pitch))
    cols = max(1, int(w // col_pitch))
    y0 = -(rows - 1) * row_pitch / 2.0
    for r in range(rows):
        y = y0 + r * row_pitch
        # half-step every other row; a square grid looks like a spreadsheet
        offset = (col_pitch / 2.0) if r % 2 else 0.0
        n = cols if not offset else cols - 1
        if n < 1:
            continue
        x0 = -(n - 1) * col_pitch / 2.0
        for c in range(n):
            x = x0 + c * col_pitch
            line = LineString([(x - slot / 2 + e.stroke / 2, y),
                               (x + slot / 2 - e.stroke / 2, y)])
            out.append(_cap(line, e.stroke, e.round_ends))
    return out


def _rings(w: float, h: float, e: Engraving) -> list[Polygon]:
    outer = min(w, h) / 2.0
    out = []
    r = outer
    while r > e.stroke:
        ring = Point(0, 0).buffer(r, quad_segs=48).exterior
        out.append(_cap(LineString(ring), e.stroke, True))
        r -= e.pitch
    return out


def _hex(w: float, h: float, e: Engraving) -> list[Polygon]:
    """A hex mesh: the cell walls, not the cells."""
    out = []
    r = max(e.pitch, e.stroke * 2)
    dx = r * 1.5
    dy = r * math.sqrt(3)
    cols = max(1, int(w / dx) + 2)
    rows = max(1, int(h / dy) + 2)
    for i in range(-cols // 2, cols // 2 + 1):
        for j in range(-rows // 2, rows // 2 + 1):
            cx = i * dx
            cy = j * dy + (dy / 2 if i % 2 else 0)
            pts = [(cx + r * math.cos(math.radians(60 * k)),
                    cy + r * math.sin(math.radians(60 * k))) for k in range(7)]
            out.append(_cap(LineString(pts), e.stroke, False))
    return out


def _rule(w: float, h: float, e: Engraving) -> list[Polygon]:
    if w >= h:
        line = LineString([(-w / 2, 0), (w / 2, 0)])
    else:
        line = LineString([(0, -h / 2), (0, h / 2)])
    return [_cap(line, e.stroke, e.round_ends)]


def _frame(w: float, h: float, e: Engraving) -> list[Polygon]:
    rect = box(-w / 2, -h / 2, w / 2, h / 2)
    return [_cap(LineString(rect.exterior), e.stroke, False)]


_BUILDERS = {
    Pattern.fins: _fins,
    Pattern.slots: _slots,
    Pattern.rings: _rings,
    Pattern.hex: _hex,
    Pattern.rule: _rule,
    Pattern.frame: _frame,
}


# ---------------------------------------------------------------------------
# assembling
# ---------------------------------------------------------------------------

def build_engraving(e: Engraving, clip: Optional[Polygon] = None):
    """The marks for one engraving, in world coordinates.

    Clipped to `clip` -- normally the panel it sits on -- because a grill that
    runs off the edge of the plate is not a grill, it is a row of nicks in the
    outline.
    """
    w, h = float(e.size[0]), float(e.size[1])
    if w <= 0 or h <= 0:
        return Polygon()

    marks = _BUILDERS[Pattern(e.pattern)](w, h, e)
    if not marks:
        return Polygon()

    geom = unary_union(marks)
    # keep the pattern inside its own box, so `size` means what it says
    geom = geom.intersection(box(-w / 2, -h / 2, w / 2, h / 2))

    if e.rotation:
        geom = rotate(geom, e.rotation, origin=(0, 0), use_radians=False)
    geom = translate(geom, e.at[0], e.at[1])

    if clip is not None and not clip.is_empty:
        geom = geom.intersection(clip)
    return geom


def engraving_area(e: Engraving, clip: Optional[Polygon] = None) -> float:
    g = build_engraving(e, clip)
    return 0.0 if g.is_empty else g.area
