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
  bolts its cheeks too) and its top is trimmed to sit just under the deck,
  which rests on it;
* the **rails**, bought by length, in the cut list;
* a **module space**, the rail span by the rail stack by the module depth,
  that nothing but the bay's own modules may enter.

The floor drops to the deepest bay's module depth below the deck, so module
depth means what it means on a frame: clear room behind the module face.

Frames: a bay's rack frame (x along the rails, y up the rows, z back from
the rail face) is laid flat in the box's world -- rack x is world x, rack y
is world y, and rack depth runs down, world -z.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point, Polygon
from shapely.geometry import box as rect

from . import joinery as jn
from . import rack
from .eurorack import ROW_FORMATS, frame_width
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


def build_hybrid(res, spec):
    """A box case with every bay of the scene set into it."""
    from .box import BoxPlate, build_box, panel_plane
    from .case import outer_shape

    t = spec.materials[0].thickness
    i = t if spec.box_joint == "tslot" else 0.0
    z1 = panel_plane(res, spec)
    bays = [place_bay(b, z1) for b in res.scene.racks]

    # the floor: under the hardware as a box puts it, and no higher than the
    # deepest bay's module depth below the module faces
    zmin = res.bounds()[2]
    pcbs = [s.z[0] for s in res.solids if s.name == "pcb"]
    floor_top = min(zmin, (min(pcbs) if pcbs else zmin) - spec.floor_gap,
                    min(z1 - b.spec.module_depth for b in bays))

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
            depth = b.zr - z0                      # rail face down to the floor
            outline, holes = rack.side_panel_section(
                b.rows, b.spec.hp, 0, 4.0, rack.RAIL_OVERHANG, b.plan,
                b.spec.module_depth, depth=depth)
            # the cheek stops at the deck's underside, which rests on it
            cut = max(0.0, t - b.spec.module_panel_t)
            if cut:
                outline = outline.difference(rect(-1.0, -1.0, cut, b.ph + 1.0))
            m5 = tuple(Point(*h.at).buffer(h.diameter / 2, quad_segs=12)
                       for h in holes)
            y0w = b.wy(b.cheek_base)
            for side, xc, x_out, a_in in (
                    ("left", b.bx - t, b.bx - t, "right"),
                    ("right", b.bx + b.width, b.bx + b.width + t, "left")):
                name = f"{b.spec.id}-cheek-{side}"
                cheek = jn.Plate(outline, mat, m5, keep)
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
                parts[name] = cheek
                ctx["place"][name] = ("cheek", (xc, y0w, b.zr), CHEEK_AXES)
            ctx["notes"].append(
                f"bay {b.spec.id}: {b.spec.hp:g} HP, "
                f"{' + '.join(r.name for r in b.rows)}, module depth "
                f"{b.spec.module_depth:g} mm, rails {b.spec.module_panel_t:g} "
                f"mm under the deck top")
            for r in rack._rails(b.placed, b.width, rack.RAIL_OVERHANG):
                rails_of.append((b, r))

    modules = {m for b in bays for m in b.spec.modules}
    model = build_box(res, spec, deck_top=z1, floor_top=floor_top,
                      outer=outer, deck_cuts=[b.aperture for b in bays],
                      extra=extra, inside=modules)

    rail_mat = Material(name="aluminium-rail", thickness=1.0, kerf=0.0,
                        color="#d8dade")
    for b, r in rails_of:
        model.members.append(BoxPlate(
            f"{b.spec.id}-{r.name}", "rail", r.section,
            (b.bx, b.wy(r.origin[1]), b.zr), RAIL_AXES,
            rail_mat.model_copy(update={"thickness": b.width})))
    n = len(rails_of)
    model.extras["bom"] = {
        "rails": n,
        "rail mm": round(sum(b.width for b, _r in rails_of), 2),
        "M5 screw": 2 * n,
    }
    model.extras["bays"] = [{
        "id": b.spec.id, "hp": b.spec.hp, "width": b.width,
        "aperture": [list(c) for c in b.aperture.exterior.coords],
        "rail_face_z": b.zr, "module_depth": b.spec.module_depth,
        "modules": list(b.spec.modules),
    } for b in bays]
    return model


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def bay_issues(res, spec) -> list[tuple[str, str, str, list[str]]]:
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
    for spec_bay in res.scene.racks:
        b = place_bay(spec_bay, z1)
        modules = set(spec_bay.modules)
        for m in sorted(modules - ids):
            out.append(("error", "rack_bay",
                        f"bay {spec_bay.id} lists module {m!r}, which is not "
                        f"a placement", [spec_bay.id]))
        zlo = z1 - spec_bay.module_depth
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


def _z_overlap(a, b) -> float:
    return min(a[1], b[1]) - max(a[0], b[0])
