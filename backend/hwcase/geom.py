"""Transforms and 2D helpers.

Everything here is deliberately 2.5D: a part is a set of axis-aligned boxes,
each with a footprint polygon and a z interval. That is enough to answer every
question a layered case asks ("what material is at this height?", "does this
plug hit that board?") and it keeps the whole engine on shapely, with no CAD
kernel in the hot path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from .schema import Box, Face, Outline, PolyOutline, RectOutline, Vec2, Vec3


@dataclass(frozen=True)
class Frame:
    """Rigid placement of a part in world space.

    `tilt` turns the part about its own X axis, in quarter turns only:

        0    flat, component side up
        90   stood on its bottom edge, component side facing -Y
        180   turned over (what `flip` used to mean)
        270   stood on its top edge, component side facing +Y

    Quarter turns are the whole story on purpose. The engine is 2.5D -- a part
    is boxes with a footprint and a z interval -- and only multiples of 90
    map a box to another box. A board tilted 30 degrees has no single z
    interval, so allowing it would quietly wreck every collision test and every
    layer. Standing a board on edge to squeeze it between two others needs
    exactly this and nothing more.

    `rot_z` is applied after the tilt, about world Z.
    """

    pos: Vec3 = (0.0, 0.0, 0.0)
    rot_z: float = 0.0
    tilt: int = 0

    @property
    def flip(self) -> bool:
        """Backwards compatibility: `flip` was a 180 degree tilt."""
        return self.tilt == 180

    def _tilt_point(self, p: Vec3) -> Vec3:
        x, y, z = p
        t = self.tilt % 360
        if t == 90:
            return (x, -z, y)
        if t == 180:
            return (x, -y, -z)
        if t == 270:
            return (x, z, -y)
        return (x, y, z)

    def point(self, p: Vec3) -> Vec3:
        x, y, z = self._tilt_point(p)
        a = math.radians(self.rot_z)
        ca, sa = math.cos(a), math.sin(a)
        return (
            self.pos[0] + x * ca - y * sa,
            self.pos[1] + x * sa + y * ca,
            self.pos[2] + z,
        )

    def direction(self, d: Vec3) -> Vec3:
        """Rotate a direction vector; no translation."""
        x, y, z = self._tilt_point(d)
        a = math.radians(self.rot_z)
        ca, sa = math.cos(a), math.sin(a)
        return (x * ca - y * sa, x * sa + y * ca, z)

    def polygon(self, poly: Polygon) -> Polygon:
        """Map a part-local footprint into world XY.

        Only meaningful for an untilted part: once a board is on edge its
        footprint and its height are entangled, so use `place` instead.
        """
        if self.tilt == 180:
            poly = Polygon([(x, -y) for x, y in poly.exterior.coords])
        poly = rotate(poly, self.rot_z, origin=(0, 0), use_radians=False)
        return translate(poly, self.pos[0], self.pos[1])

    def z_interval(self, z: Vec2) -> Vec2:
        lo, hi = min(z), max(z)
        if self.tilt == 180:
            lo, hi = -hi, -lo
        return (lo + self.pos[2], hi + self.pos[2])

    def place(self, poly: Polygon, z: Vec2) -> tuple[Polygon, Vec2]:
        """Put one local feature into the world as (footprint, z interval).

        Flat and upside-down keep the exact outline -- a round hole stays round.
        On edge, the outline and the height swap roles, so the feature is taken
        as its bounding box: a cylinder lying down is treated as the box around
        it. That is conservative for collision and slightly generous for a
        panel cutout, which is the safe direction to be wrong in.
        """
        t = self.tilt % 360
        if t in (0, 180):
            return self.polygon(poly), self.z_interval(z)

        x0, y0, x1, y1 = poly.bounds
        z0, z1 = min(z), max(z)
        if t == 90:
            ny0, ny1 = -z1, -z0
            nz0, nz1 = y0, y1
        else:                                  # 270
            ny0, ny1 = z0, z1
            nz0, nz1 = -y1, -y0
        flat = Polygon([(x0, ny0), (x1, ny0), (x1, ny1), (x0, ny1)])
        flat = rotate(flat, self.rot_z, origin=(0, 0), use_radians=False)
        flat = translate(flat, self.pos[0], self.pos[1])
        return flat, (nz0 + self.pos[2], nz1 + self.pos[2])


def rounded_rect(size: Vec2, corner_radius: float = 0.0, origin: str = "center",
                 origin_offset: Vec2 = (0.0, 0.0)) -> Polygon:
    w, h = size
    if origin == "center":
        x0, y0 = -w / 2.0, -h / 2.0
    elif origin == "min":
        x0, y0 = 0.0, 0.0
    else:
        x0, y0 = -origin_offset[0], -origin_offset[1]
    box = Polygon([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])
    r = min(corner_radius, w / 2.0, h / 2.0)
    if r > 1e-9:
        box = box.buffer(-r, join_style=2).buffer(r, join_style=1, quad_segs=16)
    return box


def outline_polygon(outline: Outline) -> Polygon:
    if isinstance(outline, RectOutline):
        return rounded_rect(outline.size, outline.corner_radius,
                            outline.origin, outline.origin_offset)
    if isinstance(outline, PolyOutline):
        return Polygon(outline.points)
    raise TypeError(f"unsupported outline {outline!r}")


def box_polygon(b: Box, at: Vec2 | None = None) -> Polygon:
    """Footprint of one feature, optionally re-centred (for repeat grids)."""
    cx, cy = at if at is not None else b.at
    w, h = b.size
    if b.shape == "circle":
        return Point(cx, cy).buffer(max(w, h) / 2.0, quad_segs=24)
    poly = Polygon([
        (cx - w / 2, cy - h / 2),
        (cx + w / 2, cy - h / 2),
        (cx + w / 2, cy + h / 2),
        (cx - w / 2, cy + h / 2),
    ])
    r = min(b.corner_radius, w / 2.0, h / 2.0)
    if r > 1e-9:
        poly = poly.buffer(-r, join_style=2).buffer(r, join_style=1, quad_segs=12)
    return poly


def corridor(at: Vec3, face: Face, length: float, width: float, height: float,
             z_range: Vec2 | None = None) -> tuple[Polygon, Vec2]:
    """The volume a plug + its cable bend needs, in part-local coordinates.

    Returns (footprint, z interval). For a connector on a side face the
    corridor runs horizontally; for +z/-z it is a vertical column, in which
    case the footprint is just the opening and the z interval carries the run.

    `z_range` pins the vertical extent to the socket body when the part
    declares one. Without it the corridor is centred on `at`, and a connector
    whose `at` sits on the board surface rather than at the mouth centre then
    reaches *below the board* -- which is how USB ports came to be cut out of
    the floor.
    """
    x, y, z = at
    nx, ny, nz = face.normal
    if nz == 0:
        # horizontal corridor: extend from the mouth along the normal
        if nx:
            x0, x1 = (x, x + nx * length)
            poly = Polygon([
                (min(x0, x1), y - width / 2), (max(x0, x1), y - width / 2),
                (max(x0, x1), y + width / 2), (min(x0, x1), y + width / 2),
            ])
        else:
            y0, y1 = (y, y + ny * length)
            poly = Polygon([
                (x - width / 2, min(y0, y1)), (x + width / 2, min(y0, y1)),
                (x + width / 2, max(y0, y1)), (x - width / 2, max(y0, y1)),
            ])
        return poly, z_range if z_range is not None else (z - height / 2, z + height / 2)
    poly = Polygon([
        (x - width / 2, y - width / 2), (x + width / 2, y - width / 2),
        (x + width / 2, y + width / 2), (x - width / 2, y + width / 2),
    ])
    z0, z1 = sorted((z, z + nz * length))
    return poly, (z0, z1)


def z_overlap(a: Vec2, b: Vec2, tol: float = 1e-6) -> float:
    """Length of the overlap of two z intervals; <= 0 means disjoint."""
    return min(a[1], b[1]) - max(a[0], b[0]) - tol


def area(geom: BaseGeometry) -> float:
    return float(getattr(geom, "area", 0.0))
