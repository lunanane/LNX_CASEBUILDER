"""Rack bays in a box case: Eurorack modules and Adafruit boards in one case.

A bay is a stretch of Eurorack rails set into a box case's deck. The module
faces are the case's upper surface (D-3): a module's panel rests on the
rails' front face, so the rails sit one module-panel thickness below the deck
top, and the deck around them is flush with the modules. Everything else on
the deck -- keypads, screens, encoders -- is the box case as M9 builds it.

What a bay adds to the box:

* an **aperture** in the deck, the rail span wide and the rail stack tall,
  for the modules to drop into;
* two **cheeks**, cut from the box's own sheet and standing on the floor,
  that carry the rails with an M5 into each rail end -- the same cheek, M5s
  and rail arithmetic as a laser-cut frame (`hwcase.rack`). A cheek is
  T-slotted into the floor (an interior joint needs a slot, so a finger box
  bolts its cheeks too). Its inner face is flush with the aperture's edge,
  so along the rails' stack it stands up through the deck and the two are
  finger-jointed (glued) like a box corner; elsewhere the deck rests on it;
* the **rails**, bought by length, in the cut list;
* a **module space**, the rail span by the rail stack, from the deck top
  down to the module depth behind the rails, that nothing but the bay's own
  modules may enter.

Module depth means what it means on a frame: clear room behind the module
panel, measured from the rails' front face -- the back of the panel -- to the
floor (a frame measures it to the inside of its body's web, `rack.case_depth`).
So the floor's top sits the deepest bay's module depth below its rail face,
and a module that fits a 40 mm frame fits a 40 mm bay.

Frames: a bay's rack frame (x along the rails, y up the rows, z back from
the rail face) is laid flat in the box's world -- rack x is world x, rack y
is world y, and rack depth runs down, world -z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from shapely.affinity import translate
from shapely.geometry import Point, Polygon
from shapely.geometry import box as rect
from shapely.ops import unary_union

from . import joinery as jn
from . import rack
from .eurorack import HP, ROW_FORMATS, frame_width
from .schema import Material, VolumeKind

#: a cheek's place in the world: u is its depth (world down), v its height
#: up the rows (world y), w its thickness across the rails (world x)
CHEEK_AXES = ((0, 0, -1), (0, 1, 0), (1, 0, 0))
#: a rail's: its section in (depth, height), swept along world x
RAIL_AXES = CHEEK_AXES

def wall_gap(t: float, clearance: float) -> float:
    """Air between a bay's cheek and the box's wall. Both stand in slots in
    the floor, so the gap has to leave a web of floor -- half a sheet, the
    joints' own rule -- between the two rows of slots, plus their fit, plus
    a millimetre to assemble by."""
    return t / 2 + clearance + 1.0

#: overlap below this is a graze, as in the collision check
GRAZE = 0.2


@dataclass
class Bay:
    """One bay, placed in the box's world."""

    spec: object                    # schema.RackBay
    rows: list
    placed: list                    # rack.Row
    width: float                    # rail span
    bx: float                       # world x of the rails' start
    y_off: float                    # world y = rack y + y_off
    zr: float                       # world z of the rails' front face
    plan: object                    # rack.PanelPlan
    cheek_base: float               # rack y of a cheek's bottom edge
    ph: float                       # cheek height
    rail_lo: float                  # rack y of the bottom rail's outer edge
    rail_hi: float                  # rack y of the top rail's outer edge

    def wy(self, y_r: float) -> float:
        return y_r + self.y_off

    @property
    def aperture(self) -> Polygon:
        """The deck opening: the rail span by the rail stack, world XY."""
        return rect(self.bx, self.wy(self.rail_lo),
                    self.bx + self.width, self.wy(self.rail_hi))

    def cheek_footprints(self, t: float) -> list[Polygon]:
        y0, y1 = self.wy(self.cheek_base), self.wy(self.cheek_base) + self.ph
        return [rect(self.bx - t, y0, self.bx, y1),
                rect(self.bx + self.width, y0, self.bx + self.width + t, y1)]

    def footprint(self, t: float) -> Polygon:
        """Cheeks and aperture together, world XY: what another bay must
        keep clear of."""
        y0 = self.wy(self.cheek_base)
        return rect(self.bx - t, y0, self.bx + self.width + t, y0 + self.ph)

    def rail_boxes(self) -> list[tuple[Polygon, tuple[float, float]]]:
        """(world XY footprint, world z range) of every rail."""
        out = []
        for i, row in enumerate(self.placed):
            for y in (row.slots[0] - rack.RAIL_SLOT_C,
                      row.slots[1] - (rack.RAIL_H - rack.RAIL_SLOT_C)):
                out.append((rect(self.bx, self.wy(y), self.bx + self.width,
                                 self.wy(y) + rack.RAIL_H),
                            (self.zr - rack.RAIL_D, self.zr)))
        return out


def place_bay(b, deck_top: float) -> Bay:
    rows = []
    for r in b.rows:
        fmt = ROW_FORMATS.get(r.format)
        if fmt is None:
            raise ValueError(f"bay {b.id}: unknown row format {r.format!r}; "
                             f"known: {', '.join(sorted(ROW_FORMATS))}")
        rows.append(fmt)
    if not rows:
        raise ValueError(f"bay {b.id} has no rows")
    overhang = rack.RAIL_OVERHANG
    placed = rack.layout_rows(rows, overhang)
    plan = rack.PanelPlan().resolved(overhang)
    lo = placed[0].slots[0] - rack.RAIL_SLOT_C
    hi = placed[-1].slots[1] + (rack.RAIL_H - rack.RAIL_SLOT_C)
    return Bay(spec=b, rows=rows, placed=placed, width=frame_width(b.hp),
               bx=b.at[0], y_off=b.at[1] - lo,
               zr=deck_top - b.module_panel_t, plan=plan,
               cheek_base=rack.panel_base(rows, overhang, plan),
               ph=rack.panel_height(rows, overhang, plan),
               rail_lo=lo, rail_hi=hi)


def cheek_outline(b: Bay, t: float, z1: float, z0: float):
    """A cheek in its own frame -- u down from the deck top, v up the rows --
    and its M5 holes, for a box whose deck top is `z1` and whose floor's
    underside is `z0`.

    The cheek is a frame's side panel (`rack.side_panel_section`) run down
    to the floor's underside, where its T-slot tabs end flush. Under the
    solid deck it stops at the deck's underside, which rests on it. Beside
    the aperture -- the rails' stack -- it stands on up to the deck's top
    face, so its fingers and the deck's interlock along the aperture's edge.
    The rails' front face is `module_panel_t` below the deck top wherever that
    puts it: above the deck's underside the cheek is cut back to it; below,
    the cheek is carried up to meet the deck, so the deck never floats.
    """
    pt = b.spec.module_panel_t
    section, holes = rack.side_panel_section(
        b.rows, b.spec.hp, 0, 4.0, rack.RAIL_OVERHANG, b.plan,
        b.spec.module_depth, depth=b.zr - z0)
    body = translate(section, pt, 0.0)          # rail face at u = pt
    if pt > t:
        body = body.union(rect(t, 0.0, pt + 0.5, b.ph))
    body = body.intersection(rect(t, -1.0, z1 - z0 + 1.0, b.ph + 1.0))
    v_lo, v_hi = b.rail_lo - b.cheek_base, b.rail_hi - b.cheek_base
    outline = body.union(rect(0.0, v_lo, t + 0.5, v_hi))
    m5 = tuple(Point(h.at[0] + pt, h.at[1]).buffer(h.diameter / 2, quad_segs=12)
               for h in holes)
    return outline, m5


#: M5 lengths a hardware shop sells (ISO 4762 / 7380)
M5_LENGTHS = (8.0, 10.0, 12.0, 16.0, 20.0, 25.0, 30.0)
#: thread an M5 needs in aluminium: one and a half diameters, rounded up
M5_ENGAGE = 8.0
#: M5 tap drill
M5_TAP_DRILL = 4.2
#: the screw that takes a module panel to a sliding nut in the rail
MODULE_SCREW = "M3x6"


def m5_length(t: float) -> float:
    """The shortest stock M5 that passes a `t` cheek and engages the rail."""
    need = t + M5_ENGAGE
    return next((L for L in M5_LENGTHS if L >= need - 1e-9), M5_LENGTHS[-1])


def bay_bom(res, bays, rails_of, t: float) -> dict[str, int]:
    """What the bays add to the box's bill of materials.

    Rails by cut length; an M5 into each rail end, long enough to pass the
    cheek and hold in the rail; and for every listed module an M3 screw and
    a sliding nut per rail hole, by Doepfer's hole rule
    (`panel.hole_columns`): two for a narrow panel, four from 12 HP. The
    module count is an estimate -- it knows only the modules the scene lists.
    """
    from .panel import hole_columns

    out: dict[str, int] = {}
    for b, _r in rails_of:
        key = f"Eurorack rail, {b.width:.2f} mm ({b.spec.hp:g} HP)"
        out[key] = out.get(key, 0) + 1
    if rails_of:
        out[f"M5x{m5_length(t):g} screw"] = 2 * len(rails_of)
    holes = 0
    for b in bays:
        for *_m, hp, _p in module_panels(res, None, b.spec):
            holes += 2 * len(hole_columns(hp))
    if holes:
        out[f"{MODULE_SCREW} module screw (estimate)"] = holes
        out["M3 sliding nut (estimate)"] = holes
    return out


def build_hybrid(res, spec):
    """A box case with every bay of the scene set into it."""
    from .box import BoxPlate, build_box, panel_plane
    from .case import outer_shape

    t = spec.materials[0].thickness
    i = t if spec.box_joint == "tslot" else 0.0
    z1 = panel_plane(res, spec)
    bays = [place_bay(b, z1) for b in res.scene.racks]

    # the floor: under the hardware as a box puts it, and no higher than the
    # deepest bay's module depth below its rails' front face
    zmin = res.bounds()[2]
    pcbs = [s.z[0] for s in res.solids if s.name == "pcb"]
    floor_top = min(zmin, (min(pcbs) if pcbs else zmin) - spec.floor_gap,
                    min(b.zr - b.spec.module_depth for b in bays))

    # the footprint: the hardware's, grown to hold every bay with its cheeks
    # inside the walls
    pad = i + t + wall_gap(t, spec.materials[0].joint_clearance)
    xs0, ys0, xs1, ys1 = outer_shape(res, spec).bounds
    for b in bays:
        for c in b.cheek_footprints(t):
            cx0, cy0, cx1, cy1 = c.bounds
            xs0, ys0 = min(xs0, cx0 - pad), min(ys0, cy0 - pad)
            xs1, ys1 = max(xs1, cx1 + pad), max(ys1, cy1 + pad)
    outer = rect(xs0, ys0, xs1, ys1)

    rails_of: list = []

    def extra(parts, joints, ctx):
        z0 = ctx["z0"]
        mat = ctx["mat"]
        keep = t / 2
        for b in bays:
            outline, m5 = cheek_outline(b, t, ctx["z1"], z0)
            depth = ctx["z1"] - z0                 # deck top down to the floor
            y0w = b.wy(b.cheek_base)
            v_lo, v_hi = b.rail_lo - b.cheek_base, b.rail_hi - b.cheek_base
            for side, xc, x_in, x_out, a_in in (
                    ("left", b.bx - t, b.bx, b.bx - t, "right"),
                    ("right", b.bx + b.width, b.bx + b.width,
                     b.bx + b.width + t, "left")):
                name = f"{b.spec.id}-cheek-{side}"
                cheek = jn.Plate(outline, mat, m5, keep)
                # into the floor: T-slots, whatever the box's own joint -- an
                # interior joint needs a slot, and a slot needs a bolt
                fu = x_out - ctx["x0"]
                a_seg = jn.Segment((fu, y0w - ctx["y0"]),
                                   (fu, y0w + b.ph - ctx["y0"]), a_in)
                b_seg = jn.Segment((depth, 0.0), (depth, b.ph), "left")
                try:
                    parts["floor"], cheek, j = jn.tslot_joint(
                        jn.Edge(a_seg, b_seg), parts["floor"], cheek,
                        spec.box_fastener)
                except jn.JointError as exc:
                    raise ValueError(f"bay {b.spec.id}'s {side} cheek cannot "
                                     f"be jointed to the floor: {exc}") from None
                joints.append(("floor", name, j))
                # into the deck: the aperture's edge is the deck's own edge,
                # and the cheek's inner face is flush with it, so the two
                # meet as a box corner does -- glued fingers along the rails'
                # stack, where the cheek stands up to the deck's top face
                du = x_in - ctx["x0"]
                # the aperture is the deck's edge here, not a hole to keep
                # clear of: the fingers are cut into it
                ap = translate(b.aperture, -ctx["x0"], -ctx["y0"]).buffer(0.01)
                deck = parts["deck"]
                parts["deck"] = jn.Plate(
                    deck.geom, deck.material,
                    tuple(h for h in deck.holes if not h.intersects(ap)),
                    deck.keep_clear)
                d_seg = jn.Segment.on(parts["deck"].geom,
                                      (du, b.wy(b.rail_lo) - ctx["y0"]),
                                      (du, b.wy(b.rail_hi) - ctx["y0"]))
                c_seg = jn.Segment.on(cheek.geom, (0.0, v_lo), (0.0, v_hi))
                try:
                    parts["deck"], cheek, j = jn.finger_joint(
                        jn.Edge(d_seg, c_seg), parts["deck"], cheek)
                except jn.JointError as exc:
                    raise ValueError(f"bay {b.spec.id}'s {side} cheek cannot "
                                     f"be jointed to the deck: {exc}") from None
                joints.append(("deck", name, j))
                parts[name] = cheek
                ctx["place"][name] = ("cheek", (xc, y0w, ctx["z1"]), CHEEK_AXES)
            ctx["notes"].append(
                f"bay {b.spec.id}: {b.spec.hp:g} HP, "
                f"{' + '.join(r.name for r in b.rows)}, module depth "
                f"{b.spec.module_depth:g} mm, rails {b.spec.module_panel_t:g} "
                f"mm under the deck top")
            for r in rack._rails(b.placed, b.width, rack.RAIL_OVERHANG):
                rails_of.append((b, r))

    modules = bay_module_ids(res.scene)
    model = build_box(res, spec, deck_top=z1, floor_top=floor_top,
                      outer=outer, deck_cuts=[b.aperture for b in bays],
                      extra=extra, inside=modules,
                      deck_fill=[b.footprint(t) for b in bays])

    rail_mat = Material(name="aluminium-rail", thickness=1.0, kerf=0.0,
                        color="#d8dade")
    for b, r in rails_of:
        model.members.append(BoxPlate(
            f"{b.spec.id}-{r.name}", "rail", r.section,
            (b.bx, b.wy(r.origin[1]), b.zr), RAIL_AXES,
            rail_mat.model_copy(update={"thickness": b.width})))
    model.extras["bom"] = bay_bom(res, bays, rails_of, t)
    if bays:
        model.notes.append(
            f"rail ends: tap each rail's {rack.RAIL_END_BORE:g} mm end bore "
            f"M5 (M5 tap drill {M5_TAP_DRILL:g} mm) for the cheek screws")
        if any(not b.spec.modules for b in bays):
            model.notes.append(
                "module screws and sliding nuts are counted for the listed "
                "modules only; add one pair per further rail hole")
    model.extras["bays"] = [{
        "id": b.spec.id, "hp": b.spec.hp, "width": b.width,
        "aperture": [list(c) for c in b.aperture.exterior.coords],
        "rail_face_z": b.zr, "module_depth": b.spec.module_depth,
        "modules": b.spec.module_ids(),
    } for b in bays]
    return model


# --------------------------------------------------------------------------
# modules: placed by slot, checked against the grid
# --------------------------------------------------------------------------

#: a module's panel has to sit on the HP grid and its row's rails to this
ALIGN = 0.05


def assembly(placements, pid: str) -> tuple[str, set[str]]:
    """The root of `pid`'s mate tree, and every placement in that tree: a
    module is its board and whatever is mated to it, panel included."""
    by_id = {p.id: p for p in placements}
    root, seen = pid, set()
    while by_id[root].parent in by_id and root not in seen:
        seen.add(root)
        root = by_id[root].parent
    members, grew = {root}, True
    while grew:
        grew = False
        for p in placements:
            if p.parent in members and p.id not in members:
                members.add(p.id)
                grew = True
    return root, members


def bay_module_ids(scene) -> set[str]:
    """Every placement that belongs to some bay's module."""
    ids = {p.id for p in scene.placements}
    out: set[str] = set()
    for bay in scene.racks:
        for m in bay.module_ids():
            if m in ids:
                out |= assembly(scene.placements, m)[1]
    return out


def _panel_of(polys_by_part, lib):
    """A module's panel: its parts of category `panel`, or -- a module with
    no panel part of its own -- every board in it."""
    panel = [p for part, p in polys_by_part if lib[part].category == "panel"]
    return panel or [p for _part, p in polys_by_part]


def _slot_target(b: Bay, hp_slot: float, row: int) -> tuple[float, float]:
    """World x of a slot's left edge, world y of a row's centre line."""
    lo, hi = b.placed[row].slots
    return b.bx + hp_slot * HP, b.wy((lo + hi) / 2)


def seat_modules(scene, lib, frames) -> None:
    """Move every module listed by slot onto its slot, in place in `frames`.

    Called by `scene.resolve` once the mates are solved and before the
    panels are: only XY moves, so the panel fit that follows is unaffected.
    A module whose id, row or bay is wrong is left where it is, for
    `bay_issues` to report.
    """
    from .geom import outline_polygon

    by_id = {p.id: p for p in scene.placements}
    for spec_bay in scene.racks:
        try:
            b = place_bay(spec_bay, 0.0)
        except ValueError:
            continue
        for m in spec_bay.modules:
            if isinstance(m, str) or m.id not in by_id \
                    or m.row >= len(b.placed):
                continue
            _root, members = assembly(scene.placements, m.id)
            polys = []
            for pid in members:
                if pid not in frames:
                    continue
                part = lib[by_id[pid].part]
                poly, _z = frames[pid].place(outline_polygon(part.outline),
                                             (0.0, part.pcb_thickness))
                polys.append((part.id, poly))
            if not polys:
                continue
            x0, y0, _x1, y1 = unary_union(_panel_of(polys, lib)).bounds
            tx, ty = _slot_target(b, m.hp_slot, m.row)
            dx, dy = tx - x0, ty - (y0 + y1) / 2
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue
            for pid in members:
                f = frames[pid]
                frames[pid] = replace(f, pos=(f.pos[0] + dx, f.pos[1] + dy,
                                              f.pos[2]))


def _module_hp(width: float) -> int:
    """How many HP a panel this wide takes: a panel is a little under its
    grid width (Doepfer's table), never over it."""
    return max(1, math.ceil(width / HP - 0.01))


def module_panels(res, lib, spec_bay):
    """(id, slot or None, row or None, panel bounds, hp, panel solids) for
    every listed module that is a placement."""
    ids = {p.id for p in res.scene.placements}
    out = []
    roots: set[str] = set()
    for m in spec_bay.modules:
        mid, slot, row = ((m, None, None) if isinstance(m, str)
                          else (m.id, m.hp_slot, m.row))
        if mid not in ids:
            continue
        root, members = assembly(res.scene.placements, mid)
        if root in roots:
            continue            # the old form lists a board and its panel
        roots.add(root)
        pcbs = [s for s in res.solids
                if s.placement in members and s.name == "pcb"]
        if not pcbs:
            continue
        if lib is not None:
            panels = [s for s in pcbs if lib[s.part].category == "panel"]
        else:               # no library to ask: the widest board is the panel
            panels = [max(pcbs, key=lambda s: s.poly.bounds[2]
                          - s.poly.bounds[0])]
        bounds = unary_union([s.poly for s in (panels or pcbs)]).bounds
        out.append((mid, slot, row, bounds,
                    _module_hp(bounds[2] - bounds[0]), panels))
    return out


def _module_issues(res, lib, spec_bay, b: Bay, z1: float):
    """Each listed module inside the aperture, on the HP grid, on a row's
    rails, clear of the others, and each row's HP within the bay's."""
    out = []
    taken: dict[int, list[tuple[float, float, str, int]]] = {}
    for mid, slot, row, (x0, y0, x1, y1), hp, panels in module_panels(
            res, lib, spec_bay):
        refs = [mid, spec_bay.id]
        if slot is not None:
            if row >= len(b.placed):
                n = len(b.placed)
                out.append(("error", "rack_bay",
                            f"module {mid} asks for row {row} of bay "
                            f"{spec_bay.id}, which has {n} "
                            f"row{'s' if n != 1 else ''} (counted from 0, "
                            f"bottom up)", refs))
                continue
            if slot + hp > spec_bay.hp + 1e-9:
                out.append(("error", "rack_bay",
                            f"module {mid} is {hp} HP: from slot {slot} it "
                            f"runs to {slot + hp} HP, past the end of bay "
                            f"{spec_bay.id}'s {spec_bay.hp:g} HP", refs))
                continue
        else:
            cy = (y0 + y1) / 2
            row = min(range(len(b.placed)),
                      key=lambda i: abs(_slot_target(b, 0, i)[1] - cy))
        ax0, ay0, ax1, ay1 = b.aperture.bounds
        if (x0 < ax0 - ALIGN or x1 > ax1 + ALIGN or y0 < ay0 - ALIGN
                or y1 > ay1 + ALIGN):
            out.append(("error", "rack_bay",
                        f"module {mid} is not inside bay {spec_bay.id}'s "
                        f"aperture: list it as {{id: {mid}, hp_slot: <n>}} "
                        f"and it is put on the rails for you", refs))
            continue
        if slot is None:
            off = (x0 - b.bx) / HP
            near = round(off)
            miss = (off - near) * HP
            if abs(miss) > ALIGN:
                out.append(("error", "rack_bay",
                            f"module {mid} is {abs(miss):.2f} mm off bay "
                            f"{spec_bay.id}'s HP grid (nearest slot "
                            f"{near}): list it as {{id: {mid}, hp_slot: "
                            f"{near}}} to place it by slot", refs))
            ty = _slot_target(b, 0, row)[1]
            if abs((y0 + y1) / 2 - ty) > ALIGN:
                out.append(("error", "rack_bay",
                            f"module {mid} is {abs((y0 + y1) / 2 - ty):.2f} "
                            f"mm off row {row}'s rails in bay {spec_bay.id}",
                            refs))
        for s in panels:
            # the face rides the deck top (D-3); the panel's back has to
            # land on the rails' front face, or the rails are at the wrong
            # height for it
            gap = s.z[0] - b.zr
            if abs(gap) > ALIGN:
                out.append(("warning", "rack_bay",
                            f"module {mid}'s panel is {s.z[1] - s.z[0]:.2f} "
                            f"mm thick but bay {spec_bay.id} expects "
                            f"{spec_bay.module_panel_t:g} mm: with its face "
                            f"flush its back is {abs(gap):.2f} mm "
                            f"{'above' if gap > 0 else 'into'} the rails. "
                            f"Set the bay's module_panel_t to its panel's "
                            f"thickness", refs))
                break
        taken.setdefault(row, []).append((x0, x1, mid, hp))
    for row, items in sorted(taken.items()):
        items.sort()
        for i, (a0, a1, a_id, _h) in enumerate(items):
            for b0, b1, b_id, _h2 in items[i + 1:]:
                if min(a1, b1) - max(a0, b0) > ALIGN:
                    out.append(("error", "rack_bay",
                                f"modules {a_id} and {b_id} overlap in row "
                                f"{row} of bay {spec_bay.id}",
                                [a_id, b_id, spec_bay.id]))
        total = sum(h for *_r, h in items)
        if total > spec_bay.hp + 1e-9:
            out.append(("error", "rack_bay",
                        f"row {row} of bay {spec_bay.id} holds {total} HP of "
                        f"modules; the bay is {spec_bay.hp:g} HP",
                        [spec_bay.id]))
    return out


def bays_json(res, lib) -> list[dict]:
    """Each bay as the editor draws and drags it, before any case is built:
    aperture and cheeks (world XY), the deck top, rail face and floor (world
    z), each row's centre line, and each listed module's slot, HP and panel
    box. A box scene only; a bay that cannot be placed is left out."""
    from .box import panel_plane

    spec = res.scene.case
    if spec.style != "box" or not res.scene.racks:
        return []
    t = spec.materials[0].thickness
    z1 = panel_plane(res, spec)
    out = []
    for spec_bay in res.scene.racks:
        try:
            b = place_bay(spec_bay, z1)
        except ValueError:
            continue
        out.append({
            "id": spec_bay.id, "hp": spec_bay.hp, "x0": b.bx,
            "width": b.width,
            "aperture": list(b.aperture.bounds),
            "cheeks": [list(c.bounds) for c in b.cheek_footprints(t)],
            "z_top": z1, "z_rail": b.zr,
            "z_floor": b.zr - spec_bay.module_depth,
            "rows": [{"format": r.name, "y_mid": _slot_target(b, 0, i)[1]}
                     for i, r in enumerate(b.rows)],
            "modules": [{"id": mid, "slot": slot, "row": row, "hp": hp,
                         "box": list(bounds)}
                        for mid, slot, row, bounds, hp, _p
                        in module_panels(res, lib, spec_bay)],
        })
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def bay_issues(res, spec, lib=None) -> list[tuple[str, str, str, list[str]]]:
    """(level, code, message, refs) for a scene with rack bays."""
    from .box import panel_plane

    out = []
    if spec.style != "box":
        out.append(("error", "rack_bay",
                     "rack bays are built into a box case: set the case's "
                     "style to box", [b.id for b in res.scene.racks]))
        return out
    t = spec.materials[0].thickness
    z1 = panel_plane(res, spec)
    ids = {p.id for p in res.scene.placements}
    placed, out_geom = _bay_geometry_issues(res, spec, z1)
    out += out_geom
    claimed: set[str] = set()
    for spec_bay, b in placed:
        listed = spec_bay.module_ids()
        modules: set[str] = set()
        for m in sorted(set(listed)):
            if m not in ids:
                out.append(("error", "rack_bay",
                            f"bay {spec_bay.id} lists module {m!r}, which is "
                            f"not a placement", [spec_bay.id]))
                continue
            modules |= assembly(res.scene.placements, m)[1]
            if listed.count(m) > 1:
                out.append(("error", "rack_bay",
                            f"bay {spec_bay.id} lists module {m} twice",
                            [m, spec_bay.id]))
        for m in sorted(modules & claimed):
            out.append(("error", "rack_bay",
                        f"{m} is listed as a module in more than one bay",
                        [m, spec_bay.id]))
        claimed |= modules
        if lib is not None:
            out += _module_issues(res, lib, spec_bay, b, z1)
        zlo = b.zr - spec_bay.module_depth
        space = b.aperture
        cheeks = b.cheek_footprints(t)
        reported: set[tuple[str, str]] = set()
        for s in res.solids:
            if s.kind not in (VolumeKind.body, VolumeKind.keepout,
                              VolumeKind.cable):
                continue
            if s.placement in modules:
                if s.z[0] < zlo - 1e-6 and (s.placement, "deep") not in reported:
                    reported.add((s.placement, "deep"))
                    out.append(("error", "module_depth",
                                f"module {s.placement} reaches "
                                f"{zlo - s.z[0]:.1f} mm deeper than bay "
                                f"{spec_bay.id}'s {spec_bay.module_depth:g} mm "
                                f"module depth", [s.placement, spec_bay.id]))
                if s.kind is VolumeKind.body:
                    for rail, zr in b.rail_boxes():
                        if (s.poly.intersection(rail).area > 0.01
                                and _z_overlap(s.z, zr) > GRAZE
                                and (s.placement, "rail") not in reported):
                            reported.add((s.placement, "rail"))
                            out.append(("error", "rack_bay",
                                        f"module {s.placement} runs into a "
                                        f"rail of bay {spec_bay.id}",
                                        [s.placement, spec_bay.id]))
                continue
            inside = (s.poly.intersection(space).area > 0.01
                      and _z_overlap(s.z, (zlo, z1)) > GRAZE)
            hits_cheek = any(s.poly.intersection(c).area > 0.01 for c in cheeks)
            if (inside or hits_cheek) and (s.placement, "bay") not in reported:
                reported.add((s.placement, "bay"))
                where = "module space" if inside else "cheeks"
                out.append(("error", "rack_bay",
                            f"{s.ref} sits in bay {spec_bay.id}'s {where}",
                            [s.placement, spec_bay.id]))
    return out


def _bay_geometry_issues(res, spec, z1):
    """Each bay placed, and what is wrong with where the bays are: a row
    format that does not exist, two bays on top of each other or too close
    for their cheeks' floor slots, panels thicker than the deck, a bay that
    pushes the case past the outline that was set. Returns ([(spec, Bay)],
    issues); a bay that cannot be placed at all is left out of the list."""
    out = []
    t = spec.materials[0].thickness
    clearance = spec.materials[0].joint_clearance
    placed = []
    seen: set[str] = set()
    for spec_bay in res.scene.racks:
        if spec_bay.id in seen:
            out.append(("error", "rack_bay",
                        f"two bays are called {spec_bay.id}: give each its "
                        f"own id", [spec_bay.id]))
        seen.add(spec_bay.id)
        try:
            b = place_bay(spec_bay, z1)
        except ValueError as exc:
            out.append(("error", "rack_bay", str(exc), [spec_bay.id]))
            continue
        placed.append((spec_bay, b))
        if spec_bay.module_panel_t > t + 1e-9:
            out.append((
                "warning", "rack_bay",
                f"bay {spec_bay.id}: its {spec_bay.module_panel_t:g} mm module "
                f"panels are thicker than the {t:g} mm deck, so the rails sit "
                f"{spec_bay.module_panel_t - t:.1f} mm below the deck's "
                f"underside; the cheeks are carried up to meet the deck",
                [spec_bay.id]))
    # Two bays' cheeks both stand in slots in the floor, so between them
    # they need the web a wall and a cheek need: `wall_gap`.
    need = wall_gap(t, clearance)
    for i, (sa, a) in enumerate(placed):
        for sb, b in placed[i + 1:]:
            fa, fb = a.footprint(t), b.footprint(t)
            if fa.intersection(fb).area > 0.01:
                out.append(("error", "rack_bay",
                            f"bays {sa.id} and {sb.id} overlap: move one so "
                            f"their cheeks are {need:.1f} mm apart",
                            [sa.id, sb.id]))
            elif fa.distance(fb) < need - 1e-6:
                out.append(("error", "rack_bay",
                            f"bays {sa.id} and {sb.id} are "
                            f"{fa.distance(fb):.1f} mm apart: their cheeks "
                            f"need {need:.1f} mm of floor between their "
                            f"slots", [sa.id, sb.id]))
    if spec.outline is not None and placed:
        from .case import outer_shape

        x0, y0, x1, y1 = outer_shape(res, spec).bounds
        pad = (t if spec.box_joint == "tslot" else 0.0) + t + need
        for sa, a in placed:
            fx0, fy0, fx1, fy1 = a.footprint(t).bounds
            if (fx0 - pad < x0 - 1e-6 or fy0 - pad < y0 - 1e-6
                    or fx1 + pad > x1 + 1e-6 or fy1 + pad > y1 + 1e-6):
                out.append(("warning", "rack_bay",
                            f"bay {sa.id} with its cheeks reaches past the "
                            f"case outline, so the box grows past it to hold "
                            f"the bay inside its walls", [sa.id]))
    return placed, out


def _z_overlap(a, b) -> float:
    return min(a[1], b[1]) - max(a[0], b[0])
