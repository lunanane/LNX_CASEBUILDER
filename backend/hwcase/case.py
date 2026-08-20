"""Turning a resolved scene into a case.

The case is a **stack of slabs**. Each slab is one sheet of material with a
2D polygon: the outer shape minus the pockets the hardware needs at that
height, minus the corridors connectors need through the walls.

That representation is the whole point of the project:

* cut it on a laser and the stack *is* the case;
* mill it and each slab is one pocket depth on a 2.5D toolpath;
* extrude each slab and you have the solid model for a preview or a STEP.

Nothing here needs a CAD kernel, so it stays fast enough to re-run on every
drag in the editor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .geom import outline_polygon, rounded_rect, z_overlap
from .schema import CaseSpec, Material, VolumeKind
from .scene import Resolved

Role = Literal["floor", "body", "lid"]


@dataclass
class Layer:
    index: int
    z0: float
    z1: float
    role: Role
    material: Material
    geom: Polygon | MultiPolygon
    notes: list[str] = field(default_factory=list)

    @property
    def thickness(self) -> float:
        return self.z1 - self.z0


@dataclass
class CaseModel:
    spec: CaseSpec
    outer: Polygon
    layers: list[Layer]
    z0: float
    z1: float

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    def bill_of_materials(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for layer in self.layers:
            out[layer.material.name] = out.get(layer.material.name, 0) + 1
        return out


def _materials_for(spec: CaseSpec, total: float) -> list[Material]:
    """Fill `total` mm of height with the material stack, repeating the last."""
    if not spec.materials:
        raise ValueError("case spec has no materials")
    out: list[Material] = []
    used = 0.0
    i = 0
    while used < total - 1e-6:
        mat = spec.materials[i] if i < len(spec.materials) else spec.materials[-1]
        out.append(mat)
        used += mat.thickness
        i += 1
        if len(out) > 400:
            raise ValueError("material stack would need more than 400 layers")
    return out


def outer_shape(res: Resolved, spec: CaseSpec) -> Polygon:
    if spec.outline is not None:
        return outline_polygon(spec.outline)
    x0, y0, _, x1, y1, _ = res.bounds()
    w = (x1 - x0) + 2 * (spec.wall + spec.part_clearance)
    h = (y1 - y0) + 2 * (spec.wall + spec.part_clearance)
    shape = rounded_rect((w, h), spec.corner_radius, origin="min")
    from shapely.affinity import translate
    return translate(shape, x0 - spec.wall - spec.part_clearance,
                     y0 - spec.wall - spec.part_clearance)


def build(res: Resolved, spec: Optional[CaseSpec] = None) -> CaseModel:
    spec = spec or res.scene.case
    outer = outer_shape(res, spec)

    _, _, zmin, _, _, zmax = res.bounds()
    z0 = zmin - spec.floor_gap
    z1 = zmax + spec.ceiling_gap
    materials = _materials_for(spec, z1 - z0)

    layers: list[Layer] = []
    cursor = z0
    for i, mat in enumerate(materials):
        top = cursor + mat.thickness
        role: Role = "floor" if i == 0 else ("lid" if i == len(materials) - 1 else "body")
        geom, notes = _layer_geometry(res, spec, outer, (cursor, top), role)
        layers.append(Layer(i, cursor, top, role, mat, geom, notes))
        cursor = top

    return CaseModel(spec=spec, outer=outer, layers=layers, z0=z0, z1=cursor)


def _layer_geometry(res: Resolved, spec: CaseSpec, outer: Polygon,
                    slab: tuple[float, float], role: Role):
    """outer shape minus whatever occupies this slab."""
    notes: list[str] = []
    cuts: list[Polygon] = []
    under_panel = {p.id for p in res.scene.placements if p.under_panel}

    for s in res.solids:
        if z_overlap(s.z, slab) <= 0:
            continue
        if s.kind is VolumeKind.body and role in ("floor", "lid"):
            # the floor stays solid under the hardware, and only things the
            # user must see or touch pierce the lid
            continue
        if s.kind in (VolumeKind.display, VolumeKind.actuator):
            if s.placement in under_panel:
                continue          # the faceplate runs over this one unbroken
            notes.append(f"opening for {s.ref}")
        cuts.append(s.poly.buffer(spec.part_clearance, join_style=2))

    for wc in res.connectors:
        if not wc.cuts_the_wall:
            continue
        if z_overlap(wc.corridor_z, slab) <= 0:
            continue
        cuts.append(wc.corridor_poly.buffer(spec.part_clearance, join_style=2))
        notes.append(f"cutout for {wc.ref} ({wc.conn.type})")

    # a side the user asked to leave open: everything beyond that edge goes,
    # from the floor up to just above the cable
    for so in res.side_openings:
        if z_overlap(so.z, slab) <= 0:
            continue
        cuts.append(so.poly)
        notes.append(so.reason)

    geom: Polygon | MultiPolygon = outer
    if cuts:
        geom = outer.difference(unary_union(cuts))
    if geom.is_empty:
        notes.append("nothing left of this layer -- it is pure air")
    return geom, notes


def kerf_compensated(geom, kerf: float):
    """Offset a layer outward by half the kerf so the cut part lands on size."""
    if kerf <= 0:
        return geom
    return geom.buffer(kerf / 2.0, join_style=2)
