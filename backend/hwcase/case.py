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

from shapely.geometry import Point
from shapely.geometry import box as shapely_box

from .geom import outline_polygon, rounded_rect, z_overlap
from .schema import CaseSpec, Interior, Material, Support, VolumeKind
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

    px0, py0, _, px1, py1, _ = res.bounds()
    pad = spec.wall + spec.part_clearance
    edges = {"x0": px0 - pad, "y0": py0 - pad, "x1": px1 + pad, "y1": py1 + pad}
    # the wall may never come inside the hardware, whatever margin was asked for
    limit = {"x0": px0 - spec.part_clearance, "y0": py0 - spec.part_clearance,
             "x1": px1 + spec.part_clearance, "y1": py1 + spec.part_clearance}

    chosen: dict[str, float] = {}
    for t in res.wall_targets:
        key = ("x" if t.axis == 0 else "y") + ("1" if t.sign > 0 else "0")
        want = max(t.value, limit[key]) if t.sign > 0 else min(t.value, limit[key])
        if key not in chosen:
            chosen[key] = want
        else:
            # two boards pulling the same edge: take the outermost, so nobody
            # gets walled in
            chosen[key] = max(chosen[key], want) if t.sign > 0 else min(chosen[key], want)
    edges.update(chosen)

    x0, y0, x1, y1 = edges["x0"], edges["y0"], edges["x1"], edges["y1"]
    shape = rounded_rect((x1 - x0, y1 - y0), spec.corner_radius, origin="min")
    from shapely.affinity import translate
    return translate(shape, x0, y0)


def build(res: Resolved, spec: Optional[CaseSpec] = None) -> CaseModel:
    spec = spec or res.scene.case
    outer = outer_shape(res, spec)

    _, _, zmin, _, _, zmax = res.bounds()

    # The top of the case is the faceplate, not the tallest thing in the scene.
    # Knob shafts, jack bushings and button caps deliberately stand proud of the
    # panel -- they are outside the box. Sizing the case to them grew it upwards
    # to "enclose" the knobs and produced phantom layers above the faceplate.
    if res.panels:
        z1 = max(res.panels.values())
    else:
        z1 = zmax + spec.ceiling_gap
    z0 = zmin - spec.floor_gap
    materials = _materials_for(spec, z1 - z0)

    # A sheet stack rarely divides the height exactly. Land the top face on the
    # panel plane and let the slack fall under the floor, where it is just extra
    # clearance rather than a lid floating above the surface.
    z0 = z1 - sum(m.thickness for m in materials)

    layers: list[Layer] = []
    cursor = z0
    for i, mat in enumerate(materials):
        top = cursor + mat.thickness
        role: Role = "floor" if i == 0 else ("lid" if i == len(materials) - 1 else "body")
        geom, notes = _layer_geometry(res, spec, outer, (cursor, top), role,
                                      (z0, z1))
        layers.append(Layer(i, cursor, top, role, mat, geom, notes))
        cursor = top

    return CaseModel(spec=spec, outer=outer, layers=layers, z0=z0, z1=cursor)


def _hardware_in(res: Resolved, spec: CaseSpec, slab: tuple[float, float],
                 pad: float = 0.0) -> list[Polygon]:
    """Everything physical in this slab -- boards, plugs, cable runs alike.

    Used to keep ribs and walls off the hardware. `pad` widens it, which is how
    `grown` reserves the room a wire actually needs rather than the room its
    connector body occupies.
    """
    out: list[Polygon] = []
    for s in res.solids:
        if z_overlap(s.z, slab) > 0:
            out.append(s.poly.buffer(spec.part_clearance + pad, join_style=2))
    # every connector, not just the ones that pierce a wall: an I2C lead never
    # leaves the box and still has to go somewhere
    for wc in res.connectors:
        if z_overlap(wc.corridor_z, slab) > 0:
            out.append(wc.corridor_poly.buffer(spec.part_clearance, join_style=2))
    return out


def _ribs(outer: Polygon, void: Polygon, spec: CaseSpec,
          blocked: list[Polygon]) -> Polygon:
    """Stiffeners across the void, on a grid, routed around the hardware.

    A rib that has been chopped up by cable runs can end up as an island in the
    middle of the void -- a loose offcut on the cutting bed and no help at all
    to the faceplate. So only the fragments still joined to the surrounding
    wall are kept.
    """
    x0, y0, x1, y1 = outer.bounds
    strips: list[Polygon] = []
    half = spec.rib_width / 2.0
    n = max(1, int((x1 - x0) // spec.rib_spacing))
    for i in range(1, n + 1):
        x = x0 + i * (x1 - x0) / (n + 1)
        strips.append(shapely_box(x - half, y0, x + half, y1))
    n = max(1, int((y1 - y0) // spec.rib_spacing))
    for i in range(1, n + 1):
        y = y0 + i * (y1 - y0) / (n + 1)
        strips.append(shapely_box(x0, y - half, x1, y + half))

    grid = unary_union(strips).intersection(outer)
    if blocked:
        grid = grid.difference(unary_union(blocked))
    if grid.is_empty:
        return grid

    wall = outer.difference(void)
    keep = [g for g in (grid.geoms if hasattr(grid, "geoms") else [grid])
            if not g.is_empty and g.buffer(1e-6).intersects(wall)]
    return unary_union(keep) if keep else grid.difference(grid)


def _support_features(res: Resolved, spec: CaseSpec, slab: tuple[float, float],
                      role: Role, zspan: tuple[float, float]):
    """Material to keep, and holes to punch, for the boards the case carries.

    A `from_floor` board gets a column of material from the bottom plate up to
    its underside -- that column has to be added back *after* the interior has
    been hollowed out, or the void would eat the very post that holds the board.
    The plate at the far end gets the bigger countersink so a screw head
    finishes flush with the outside.
    """
    bosses: list[Polygon] = []
    holes: list[Polygon] = []
    notes: list[str] = []
    case_z0, case_z1 = zspan

    for sp in res.supports:
        # Reach rather than overlap. A board flush with the faceplate has its
        # top exactly at the lid's top, so an overlap test measures zero and the
        # lid never gets drilled -- which is precisely the board you most want
        # to screw down from above.
        if sp.mode == Support.from_floor:
            involved = slab[0] < sp.board_bottom - 1e-6 and slab[1] > case_z0 - 1e-6
            countersunk = role == "floor"
        elif sp.mode == Support.from_lid:
            involved = slab[1] > sp.board_top - 1e-6 and slab[0] < case_z1 + 1e-6
            countersunk = role == "lid"
        else:
            continue
        if not involved:
            continue

        centre = Point(sp.at)
        if countersunk:
            holes.append(centre.buffer(spec.screw_head / 2.0, quad_segs=24))
            notes.append(f"countersink for {sp.ref}")
        else:
            bosses.append(centre.buffer(spec.support_boss / 2.0, quad_segs=24))
            holes.append(centre.buffer(
                (sp.screw_d + spec.screw_clearance) / 2.0, quad_segs=24))
            notes.append(f"boss for {sp.ref}")
    return bosses, holes, notes


def _layer_geometry(res: Resolved, spec: CaseSpec, outer: Polygon,
                    slab: tuple[float, float], role: Role,
                    zspan: tuple[float, float]):
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

    # How much of the inside to take out. The floor and the lid are structural
    # faces and keep their own rules; only the layers in between are hollowed.
    if role == "body" and spec.interior is not Interior.pocketed:
        void = outer.buffer(-spec.wall, join_style=2)
        if not void.is_empty:
            if spec.interior is Interior.grown:
                # the void is only the hardware and the room its cables need
                grown = _hardware_in(res, spec, slab, pad=spec.cable_clearance)
                if grown:
                    cuts.extend(grown)
                    notes.append("pockets grown to clear the cable runs")
            elif spec.interior is Interior.hollow:
                cuts.append(void)
                notes.append(f"hollow, {spec.wall:.1f} mm wall")
            elif spec.interior is Interior.ribs:
                blocked = _hardware_in(res, spec, slab)
                rib = _ribs(outer, void, spec, blocked)
                cuts.append(void.difference(rib) if not rib.is_empty else void)
                notes.append(f"hollow with stiffeners, {spec.wall:.1f} mm wall")

    geom: Polygon | MultiPolygon = outer
    if cuts:
        geom = outer.difference(unary_union(cuts))

    # Bosses go on last but one: after the pockets and the hollowing, because
    # both would otherwise remove the post, and before the screw holes, which
    # have to be drilled through it.
    bosses, screw_holes, support_notes = _support_features(
        res, spec, slab, role, zspan)
    if bosses:
        geom = geom.union(unary_union(bosses).intersection(outer))
    if screw_holes:
        geom = geom.difference(unary_union(screw_holes))
    notes += support_notes

    if geom.is_empty:
        notes.append("nothing left of this layer -- it is pure air")
    return geom, notes


def kerf_compensated(geom, kerf: float):
    """Offset a layer outward by half the kerf so the cut part lands on size."""
    if kerf <= 0:
        return geom
    return geom.buffer(kerf / 2.0, join_style=2)
