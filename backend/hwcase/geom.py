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

    `flip` means the part was turned over: a 180 deg rotation about its local X
    axis, so local +Z points down. Applied before `rot_z`.
    """

    pos: Vec3 = (0.0, 0.0, 0.0)
    rot_z: float = 0.0
    flip: bool = False

    def point(self, p: Vec3) -> Vec3:
        x, y, z = p
        if self.flip:
            y, z = -y, -z
        a = math.radians(self.rot_z)
        ca, sa = math.cos(a), math.sin(a)
        return (
            self.pos[0] + x * ca - y * sa,
            self.pos[1] + x * sa + y * ca,
            self.pos[2] + z,
        )

    def direction(self, d: Vec3) -> Vec3:
        """Rotate a direction vector; no translation."""
        x, y, z = d
        if self.flip:
            y, z = -y, -z
        a = math.radians(self.rot_z)
        ca, sa = math.cos(a), math.sin(a)
        return (x * ca - y * sa, x * sa + y * ca, z)

    def polygon(self, poly: Polygon) -> Polygon:
        """Map a part-local footprint into world XY."""
        if self.flip:
            poly = Polygon([(x, -y) for x, y in poly.exterior.coords])
        poly = rotate(poly, self.rot_z, origin=(0, 0), use_radians=False)
        return translate(poly, self.pos[0], self.pos[1])

    def z_interval(self, z: Vec2) -> Vec2:
        lo, hi = min(z), max(z)
        if self.flip:
            lo, hi = -hi, -lo
        return (lo + self.pos[2], hi + self.pos[2])


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
             ) -> tuple[Polygon, Vec2]:
    """The volume a plug + its cable bend needs, in part-local coordinates.

    Returns (footprint, z interval). For a connector on a side face the
    corridor runs horizontally; for +z/-z it is a vertical column, in which
    case the footprint is just the opening and the z interval carries the run.
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
        return poly, (z - height / 2, z + height / 2)
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
