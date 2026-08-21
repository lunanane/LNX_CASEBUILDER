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

from shapely.geometry import LineString, Point
from shapely.geometry import box as shapely_box
from shapely.ops import nearest_points

from .geom import outline_polygon, rounded_rect, z_overlap
from .schema import (CaseScrews, CaseSpec, Interior, Material, Support,
                     VolumeKind)
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

    _report_orphan_supports(res, layers, notes_to=layers)
    return CaseModel(spec=spec, outer=outer, layers=layers, z0=z0, z1=cursor)


def _report_orphan_supports(res: Resolved, layers: list[Layer], notes_to) -> None:
    """Say when a board asked to be screwed down and no layer could do it.

    A slab is material or void at a given XY -- it cannot be 3 mm thick above
    a board and hollow below it. So if a board's PCB ends up *inside* the lid
    slab rather than under it, there is no lid material over the screw holes to
    bear on, and screwing down from above is not a thing that can happen. That
    is worth a sentence; doing nothing quietly is how you discover it with a
    drill in your hand.
    """
    served = set()
    for layer in layers:
        for note in layer.notes:
            for word in ("countersink for ", "boss for "):
                if note.startswith(word):
                    served.add(note[len(word):])

    for sp in res.supports:
        if sp.mode == Support.none or sp.ref in served:
            continue
        target = next((layer for layer in layers
                       if layer.role == ("lid" if sp.mode == Support.from_lid
                                         else "floor")), None)
        if target is None:
            continue
        where = "lid" if sp.mode == Support.from_lid else "floor"
        edge = sp.board_top if sp.mode == Support.from_lid else sp.board_bottom
        target.notes.append(
            f"cannot support {sp.ref} from the {where}: the board face at "
            f"z {edge:.1f} is inside the {where} layer "
            f"({target.z0:.1f}..{target.z1:.1f}), not clear of it")


def _hardware_in(res: Resolved, spec: CaseSpec, slab: tuple[float, float],
                 pad: float = 0.0, outer: Polygon | None = None) -> list[Polygon]:
    """Everything physical in this slab -- boards, plugs, cable runs alike.

    Used to keep ribs and walls off the hardware. `pad` widens it, which is how
    `grown` reserves the room a wire actually needs rather than the room its
    connector body occupies.
    """
    out: list[Polygon] = []
    for s in res.solids:
        if z_overlap(s.z, slab) > 0:
            out.append(s.poly.buffer(spec.part_clearance + pad, join_style=2))
    # Every connector, not just the ones that pierce a wall: an I2C lead never
    # leaves the box and still has to go somewhere. But a lead that stays
    # inside must reserve its room INSIDE -- clipped to the inner face, or the
    # eight I2C ports on a hub sitting near the edge would each punch a hole
    # straight through the outside of the case.
    inner = outer.buffer(-spec.wall, join_style=2) if outer is not None else None
    for wc in res.connectors:
        if z_overlap(wc.corridor_z, slab) <= 0:
            continue
        room = wc.corridor_poly.buffer(spec.part_clearance, join_style=2)
        if not wc.cuts_the_wall and inner is not None and not inner.is_empty:
            room = room.intersection(inner)
        if not room.is_empty:
            out.append(room)
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


def _pieces(geom) -> list[Polygon]:
    """The separate polygons of a geometry, ignoring empties."""
    if geom is None or geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def _tie_loose_pieces(geom, outer: Polygon, spec: CaseSpec, notes: list[str],
                      must_keep: list[Polygon] | None = None):
    """Nothing may come off the cutting bed as a separate part.

    Pruning ribs when they are generated is not enough: a rib can be severed
    later by a part pocket, and a boss can attach to a fragment that is itself
    adrift. So this runs last, on the finished layer, where the truth is
    finally known.

    What happens to an island depends on what it is. One holding a board up --
    a boss, whose DISC is listed in `must_keep` -- has to stay and is tied
    back. The disc, not its centre: drilling the screw hole turns a boss into a
    ring, and a ring does not contain its own centroid. Anything
    else is a severed stiffener or boolean debris, and gets dropped: bridging
    it back would run a strip of plywood straight across the hardware, which is
    worse than losing a fragment of stiffener.
    """
    pieces = _pieces(geom)
    if len(pieces) <= 1:
        return geom

    # A tolerance, not an exact touch: the boolean chain leaves the wall a
    # hair inside `outer`, and at 1e-6 nothing counted as anchored at all, so
    # this bailed out and tied nothing.
    edge = outer.boundary.buffer(0.05)
    anchored = [p for p in pieces if p.intersects(edge)]
    adrift = [p for p in pieces if not p.intersects(edge)]
    if not adrift:
        return geom
    if not anchored:
        # nothing reaches the wall: anchor everything to the biggest piece
        anchored = [max(pieces, key=lambda p: p.area)]
        adrift = [p for p in pieces if p is not anchored[0]]

    needed = must_keep or []
    keep = list(anchored)
    ribs: list[Polygon] = []
    for piece in sorted(adrift, key=lambda p: -p.area):
        if piece.area < 1.0:
            continue                       # numerical crumbs, not material
        if not any(piece.intersects(disc) for disc in needed):
            notes.append(f"dropped a loose {piece.area:.0f} mm2 offcut")
            continue
        here, there = nearest_points(piece, unary_union(keep))
        if here.distance(there) > 1e-9:
            # Round caps, deliberately: a flat cap ends exactly on the two
            # boundaries, and a zero-area touch is a hairline gap once floating
            # point is involved -- the union then leaves both pieces separate,
            # which is the very thing this is here to prevent.
            ribs.append(LineString([here, there]).buffer(
                spec.support_rib / 2.0, cap_style=1))
            notes.append(f"rib tying a {piece.area:.0f} mm2 island back to the wall")
        keep.append(piece)
    return unary_union(keep + ribs).intersection(outer)


def case_screw_points(spec: CaseSpec, outer: Polygon) -> list[tuple[float, float]]:
    """Where the bolts through the stack go.

    Inset from the outline's bounding corners so they land in the wall rather
    than in fresh air, and -- in `perimeter` mode -- spread along each edge at
    roughly `case_screw_spacing`, evenly, so a long case is held all the way
    along rather than only at its ends.
    """
    # `==` not `is`: these arrive as raw strings from JSON and from
    # model_copy, where identity against the enum member silently fails
    if spec.case_screws == CaseScrews.none:
        return []
    x0, y0, x1, y1 = outer.bounds
    i = spec.case_screw_inset
    ax0, ay0, ax1, ay1 = x0 + i, y0 + i, x1 - i, y1 - i
    if ax1 <= ax0 or ay1 <= ay0:
        return []

    pts = [(ax0, ay0), (ax1, ay0), (ax0, ay1), (ax1, ay1)]
    if spec.case_screws == CaseScrews.perimeter and spec.case_screw_spacing > 0:
        for lo, hi, horizontal in ((ax0, ax1, True), (ay0, ay1, False)):
            n = int((hi - lo) // spec.case_screw_spacing)
            for k in range(1, n + 1):
                v = lo + k * (hi - lo) / (n + 1)
                if horizontal:
                    pts += [(v, ay0), (v, ay1)]
                else:
                    pts += [(ax0, v), (ax1, v)]

    if spec.case_screw_center:
        c = outer.representative_point()
        pts.append((c.x, c.y))
    return pts


def _case_screws(spec: CaseSpec, outer: Polygon, role: Role):
    """Bolt holes for one layer: a countersink at the outer faces, a shank
    everywhere in between."""
    d = spec.case_screw_head if role in ("floor", "lid") else spec.case_screw_d
    holes, notes = [], []
    for cx, cy in case_screw_points(spec, outer):
        p = Point(cx, cy)
        if not outer.contains(p):
            continue
        holes.append(p.buffer(d / 2.0, quad_segs=24))
        notes.append(f"case bolt at ({cx:.1f}, {cy:.1f})")
    return holes, notes


def _link_cable_void(res: Resolved, spec: CaseSpec, outer: Polygon, geom,
                     slab: tuple[float, float], notes: list[str]):
    """Make sure every internal lead can reach every other one.

    The mirror image of `_tie_loose_pieces`. That one guarantees the MATERIAL
    is all one piece so nothing falls off the cutting bed; this guarantees the
    EMPTY SPACE is all one piece so no board ends up walled into its own pocket
    with an I2C lead and nowhere to run it.

    Which matters because the interior strategies disagree about this by
    nature: `hollow` connects everything trivially, while `pocketed` cuts each
    board its own recess and would happily leave them isolated. Rather than
    special-casing each strategy, the void is checked after the fact and a
    channel is cut wherever it is broken.
    """
    if not spec.link_cables or spec.cable_channel <= 0:
        return geom

    mouths = [(c.ref, Point(c.at[0], c.at[1])) for c in res.connectors
              if not c.conn.external and c.included is not None
              and z_overlap(c.corridor_z, slab) > 0]
    # one board's worth of leads cannot be isolated from itself
    if len({ref.split(".")[0] for ref, _ in mouths}) < 2:
        return geom

    void = outer.difference(geom)
    parts = _pieces(void)
    if not parts:
        parts = []

    reachable = None
    channels: list[Polygon] = []
    linked: list[str] = []
    for ref, pt in mouths:
        here = next((p for p in parts if p.intersects(pt.buffer(0.05))), None)
        if here is None:
            # The mouth is buried in solid material -- which happens whenever a
            # strategy only opens up for external ports. Inventing a region to
            # route from is not enough: it has to be cut as well, or the lead
            # still has nowhere to emerge.
            here = pt.buffer(spec.cable_channel / 2.0, quad_segs=16)
            channels.append(here)
            linked.append(ref)
        if reachable is None:
            reachable = here
            continue
        if here.intersects(reachable):
            reachable = unary_union([reachable, here])
            continue
        a, b = nearest_points(here, reachable)
        run = LineString([a, b]).buffer(spec.cable_channel / 2.0, cap_style=1)
        channels.append(run)
        reachable = unary_union([reachable, here, run])
        linked.append(ref)

    if not channels:
        return geom
    notes.append(f"cable channel to reach {', '.join(sorted(set(linked)))}")
    return geom.difference(unary_union(channels))


def _keep_a_wall(res: Resolved, spec: CaseSpec, outer: Polygon, geom,
                 slab: tuple[float, float], notes: list[str]):
    """Put back any outer wall that pocketing ate into.

    The outline is what makes this a case rather than a tray, and no pocket,
    hollow or grown cable route has any business thinning it. Ports and side
    openings are meant to breach it and are applied after this.

    The one thing that may stand inside the wall band is hardware: if a board
    genuinely sits within `min_segment` of the outline then the wall cannot be
    there, and saying so is more useful than pressing plywood into the board.
    """
    w = spec.min_segment
    if w <= 0 or outer.is_empty:
        return geom
    band = outer.difference(outer.buffer(-w, join_style=2))
    if band.is_empty:
        return geom

    blocking = [s.poly for s in res.solids
                if z_overlap(s.z, slab) > 0 and s.kind is VolumeKind.body]
    if blocking:
        band = band.difference(unary_union(blocking))
    if band.is_empty:
        return geom

    missing = band.difference(geom).area
    if missing > 1.0:
        notes.append(f"restored {missing:.0f} mm2 of {w:.1f} mm wall")
    return geom.union(band)


def _open_out_slivers(geom, spec: CaseSpec, notes: list[str],
                      cuts: list[Polygon] | None = None):
    """Remove material too narrow to survive -- but never by reshaping a hole.

    A morphological opening finds anything thinner than `min_segment`. What is
    done with it depends on where it is:

    * a spur, a tongue or a sliver simply goes;
    * material lying BETWEEN two specified openings is kept, and reported.

    That second case is the important one. The keypad's buttons are 11.2 mm on
    a 15 mm pitch, so the web between them is 3.8 mm -- just under a 4 mm
    minimum. Letting the opening take it merges thirty-two button holes into
    one slot and the pad has nothing to sit on. A cutout is a specified
    dimension; if two of them are too close, that is a layout problem to be
    told about, not something to quietly dissolve.
    """
    w = spec.min_segment
    if w <= 0 or geom.is_empty:
        return geom

    # round joins: mitred ones leave notches where the erosion and the dilation
    # disagree, which is what made the button holes look gnawed
    opened = geom.buffer(-w / 2.0, join_style=1).buffer(w / 2.0, join_style=1)
    if opened.is_empty:
        notes.append(f"every part of this layer is thinner than {w:.1f} mm")
        return geom
    opened = opened.intersection(geom)          # only ever take material away

    lost = geom.difference(opened)
    if lost.is_empty:
        return opened

    openings = _pieces(unary_union(cuts)) if cuts else []
    keep: list[Polygon] = []
    removed = 0.0
    for piece in _pieces(lost):
        if piece.area < 1e-9:
            continue
        between = [o for o in openings if piece.buffer(0.05).intersects(o)]
        if len(between) >= 2:
            width = 2.0 * piece.area / piece.length if piece.length else 0.0
            keep.append(piece)
            notes.append(
                f"kept a {width:.1f} mm web between two openings -- thinner than "
                f"the {w:.1f} mm minimum, so move them apart or lower it")
        else:
            removed += piece.area

    if removed > 0.5:
        notes.append(f"opened out {removed:.0f} mm2 thinner than {w:.1f} mm")
    if keep:
        opened = unary_union([opened] + keep)
    return opened


def _support_features(res: Resolved, spec: CaseSpec, slab: tuple[float, float],
                      role: Role, zspan: tuple[float, float],
                      main: Polygon | MultiPolygon):
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
        # A boss may only occupy layers that are ENTIRELY clear of the board.
        # A layer straddling the board's underside contains the board itself, so
        # a post there would be driven straight through it.
        if sp.mode == Support.from_floor:
            involved = slab[1] <= sp.board_bottom + 1e-6 and slab[1] > case_z0 - 1e-6
            countersunk = role == "floor"
        elif sp.mode == Support.from_lid:
            involved = slab[0] >= sp.board_top - 1e-6 and slab[0] < case_z1 + 1e-6
            countersunk = role == "lid"
        else:
            continue
        if not involved:
            continue

        centre = Point(sp.at)
        if countersunk:
            holes.append(centre.buffer(spec.screw_head / 2.0, quad_segs=24))
            notes.append(f"countersink for {sp.ref}")
            continue

        disc = centre.buffer(spec.support_boss / 2.0, quad_segs=24)
        bosses.append(disc)
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
        if s.kind is VolumeKind.body:
            if role == "floor":
                continue      # hardware rests ON the floor; it does not cut it
            if role == "lid" and s.placement in under_panel:
                continue      # this one asked for the plate to pass over it
            # Otherwise the lid is cut like any other layer. It used to skip
            # bodies outright, which was harmless while the top layer floated
            # above everything -- but the faceplate IS the top layer now, and
            # a board sitting flush against it had plywood driven through it:
            # 1698 mm2 inside the screen, 881 inside the OLED glass.
        if s.kind in (VolumeKind.display, VolumeKind.actuator):
            if s.placement in under_panel:
                continue          # the faceplate runs over this one unbroken
            notes.append(f"opening for {s.ref}")
        cuts.append(s.poly.buffer(spec.part_clearance, join_style=2))

    # These are deliberate holes in the outside, so they are kept apart from the
    # pockets and re-applied after the minimum wall has been restored -- a port
    # is meant to breach the wall, a pocket is not.
    breaches: list[Polygon] = []
    for wc in res.connectors:
        if not wc.cuts_the_wall:
            continue
        if z_overlap(wc.corridor_z, slab) <= 0:
            continue
        breaches.append(wc.corridor_poly.buffer(spec.part_clearance, join_style=2))
        notes.append(f"cutout for {wc.ref} ({wc.conn.type})")

    for so in res.side_openings:
        if z_overlap(so.z, slab) <= 0:
            continue
        breaches.append(so.poly)
        notes.append(so.reason)

    # How much of the inside to take out. The floor and the lid are structural
    # faces and keep their own rules; only the layers in between are hollowed.
    if role == "body" and spec.interior is not Interior.pocketed:
        void = outer.buffer(-spec.wall, join_style=2)
        if not void.is_empty:
            if spec.interior == Interior.grown:
                # the void is only the hardware and the room its cables need
                grown = _hardware_in(res, spec, slab, pad=spec.cable_clearance,
                                     outer=outer)
                if grown:
                    cuts.extend(grown)
                    notes.append("pockets grown to clear the cable runs")
            elif spec.interior == Interior.hollow:
                cuts.append(void)
                notes.append(f"hollow, {spec.wall:.1f} mm wall")
            elif spec.interior == Interior.ribs:
                blocked = _hardware_in(res, spec, slab, outer=outer)
                rib = _ribs(outer, void, spec, blocked)
                cuts.append(void.difference(rib) if not rib.is_empty else void)
                notes.append(f"hollow with stiffeners, {spec.wall:.1f} mm wall")

    geom: Polygon | MultiPolygon = outer
    if cuts:
        geom = outer.difference(unary_union(cuts))

    geom = _open_out_slivers(geom, spec, notes, cuts)
    geom = _keep_a_wall(res, spec, outer, geom, slab, notes)
    if breaches:
        geom = geom.difference(unary_union(breaches))

    # Bosses go on last but one: after the pockets and the hollowing, because
    # both would otherwise remove the post, and before the screw holes, which
    # have to be drilled through it. `geom` at this point is what the boss has
    # to reach to stay attached.
    bosses, screw_holes, support_notes = _support_features(
        res, spec, slab, role, zspan, geom)
    boss_discs = list(bosses)
    if bosses:
        geom = geom.union(unary_union(bosses).intersection(outer))
    if screw_holes:
        geom = geom.difference(unary_union(screw_holes))
    notes += support_notes

    bolt_holes, bolt_notes = _case_screws(spec, outer, role)
    if bolt_holes:
        # A bolt that lands on a board is not a bolt, it is a hole through the
        # HyperPixel. Say so rather than quietly drilling it -- the fix is the
        # user's (move the board, or the screw), not ours to guess.
        for hole, note in zip(bolt_holes, bolt_notes):
            hit = [s.ref for s in res.solids
                   if s.kind is VolumeKind.body and z_overlap(s.z, slab) > 0
                   and s.poly.intersects(hole)]
            if hit:
                notes.append(f"{note} runs into {', '.join(sorted(set(hit)))}")
        geom = geom.difference(unary_union(bolt_holes))
        notes += bolt_notes

    # Cable routes are carved before the material check, so anything the
    # carving strands can still be tied back.
    geom = _link_cable_void(res, spec, outer, geom, slab, notes)
    geom = _tie_loose_pieces(geom, outer, spec, notes, boss_discs)

    if geom.is_empty:
        notes.append("nothing left of this layer -- it is pure air")
    return geom, notes


def kerf_compensated(geom, kerf: float):
    """Offset a layer outward by half the kerf so the cut part lands on size."""
    if kerf <= 0:
        return geom
    return geom.buffer(kerf / 2.0, join_style=2)
