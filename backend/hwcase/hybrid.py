"""A hybrid case: a laser-cut Eurorack frame with parts sections above and below.

The rack is the laser-cut frame of `hwcase.rack`: rows of rails spanning the
case's whole width, an M5 into each rail end through a side panel, side
panels cut from the case's one sheet with their overhangs and corner radii.
What a hybrid adds is **sections** -- stretches of deck fixed to the rack's
lower and upper ends, each as wide as the rail span, each with its own length
and its own depth below the deck, that hold the user's own boards (Adafruit
breakouts, a Pi, a screen) exactly as a parts case does. There are no left
and right walls: the case's two ends across the width are the side panels.

The case lies flat. World x is the rails' axis (0 at the left side panel's
inner face, the rail span at the right one's), world y runs along the rows
(0 at the bottom rail's outer edge; sections below are at negative y), and
world z is up. The **deck top** is the panel plane: the faces of the modules
screwed in later and the flat upper surface over every section (D-3), so the
rails' front face sits `rack.module_panel_t` under it.

Seen from the side the case is a row of **zones** along y -- sections below,
the rack, sections above -- each with its floor at its own depth:

* every section has a **deck** plate, cut by exactly the code that cuts a
  layered lid (`case._layer_geometry`), so windows, shaft holes, port
  cutouts and screw clearances are the box's;
* every run of zones at one depth has one **floor** plate; the rack's floor
  is its module depth behind the rails' front face, as on a frame;
* a **step wall** stands where two neighbouring floors differ in depth, and
  an **end wall** closes each end;
* the **side panels** follow the whole stepped profile, grown by the
  overhangs (front past the deck, back past the floors, bottom and top past
  the end walls) and rounded at the four outer corners.

Every plate is jointed into both side panels -- T-slots, whose slots need a
sheet of panel outside the plate, or glued fingers, flush with its edge.
Where two plates meet they butt, as a laser frame's plates do: the walls run
the full height and the decks and floors stop at them; with T-slots a deck's
or floor's joint stops a sheet short of the wall's, and the plate is notched
there so it does not stand in the side panel. Between the rack and a section
there is no wall above the shallower floor: the deck over the section runs on
to the rails' outer edge, and the rack's space stays open only to the rails.

The rack keeps no modules. Its space -- the rail span by the rail stack and
the strips beside it, from the deck top down to its floor -- is kept clear for
whatever is screwed in later, and the checks hold every placed part to its
section: inside it, clear of the rack, and no deeper than its floor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry import box as rect
from shapely.ops import unary_union

from . import joinery as jn
from . import rack
from .eurorack import ROW_FORMATS, frame_width
from .schema import Material, VolumeKind

#: a side panel's place in the world: u is world y, v world z, w across x
CHEEK_AXES = ((0, 1, 0), (0, 0, 1), (1, 0, 0))
#: a deck or floor: u across x, v along y, w up
FLAT_AXES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
#: an end or step wall: u across x, v up, w towards -y
WALL_AXES = ((1, 0, 0), (0, 0, 1), (0, -1, 0))
#: a rail: its section in (depth, height) -- depth down, height along y --
#: swept along world x
RAIL_AXES = ((0, 0, -1), (0, 1, 0), (1, 0, 0))

#: overlap below this is a graze, as in the collision check
GRAZE = 0.2


def rows_of(spec) -> list:
    """The rack's row formats, bottom to top."""
    rows = []
    for r in spec.rows:
        fmt = ROW_FORMATS.get(r.format)
        if fmt is None:
            raise ValueError(f"unknown row format {r.format!r}; "
                             f"known: {', '.join(sorted(ROW_FORMATS))}")
        rows.append(fmt)
    if not rows:
        raise ValueError("a hybrid case needs at least one row")
    return rows


def sheet_of(spec) -> Material:
    """The sheet the whole case is cut from: the rack's, or the laser
    frame's default."""
    return spec.material or rack.LASER_MATERIAL


def section_ids(scene) -> list[str]:
    """Each section's id: its own, or `below1`, `above2`... counted out
    from the rack on its side."""
    out, count = [], {"above": 0, "below": 0}
    for s in scene.sections:
        count[s.side] += 1
        out.append(s.id or f"{s.side}{count[s.side]}")
    return out


def deck_top(res) -> float:
    """The deck's top face: the panel plane, or -- nothing on a panel --
    just over the tallest part; an empty scene's deck is at 0."""
    if res.panels:
        return max(res.panels.values())
    if res.solids:
        return res.bounds()[5] + res.scene.case.ceiling_gap
    return 0.0


# --------------------------------------------------------------------------
# the layout: zones along y, and everything measured from them
# --------------------------------------------------------------------------


@dataclass
class Zone:
    """One stretch of the case along y: a section, or the rack."""

    name: str
    ya: float                       # world y of its lower boundary
    yb: float                       # and of its upper one
    depth: float                    # deck top down to its floor's top
    section: object = None          # schema.HybridSection, None for the rack

    @property
    def is_rack(self) -> bool:
        return self.section is None


@dataclass
class Layout:
    t: float
    joint: str
    inset: float                    # a T-slot plate stands this far in
    width: float                    # rail span: the case's inner width
    z1: float                       # deck top
    zr: float                       # rails' front face
    rows: list
    placed: list                    # rack.Row, in rack y
    lo: float                       # rack y of the bottom rail's outer edge
    rail_hi: float                  # world y of the top rail's outer edge
    gap: float                      # rail edge to a wall's outer face
    zones: list[Zone]
    #: the side panels' growth past the plates: front, back, bottom, top
    grow: tuple[float, float, float, float]
    radii: tuple[float, float, float, float]   # bf, bb, tb, tf
    notes: list[str] = field(default_factory=list)

    def wy(self, y_rack: float) -> float:
        return y_rack - self.lo

    @property
    def rack(self) -> Zone:
        return next(z for z in self.zones if z.is_rack)

    @property
    def sections(self) -> list[Zone]:
        return [z for z in self.zones if not z.is_rack]

    def zf(self, zone: Zone) -> float:
        """World z of a zone's floor top."""
        return self.z1 - zone.depth

    @property
    def y_min(self) -> float:
        return self.zones[0].ya

    @property
    def y_max(self) -> float:
        return self.zones[-1].yb

    def rack_space(self) -> Polygon:
        """The rack's zone in plan: the rail span by the rails and the strips
        beside them, out to where a wall's outer face would stand."""
        z = self.rack
        return rect(0.0, z.ya, self.width, z.yb)

    def inner(self, zone: Zone) -> Polygon:
        """The room a zone holds in plan, wall faces and all excluded."""
        ya, yb = zone.ya, zone.yb
        i = self.zones.index(zone)
        t = self.t
        if i == 0:
            ya += t
        elif abs(self.zones[i - 1].depth - zone.depth) > 1e-6 and \
                self.zones[i - 1].depth < zone.depth:
            ya += t                 # its own step wall stands inside it
        if i == len(self.zones) - 1:
            yb -= t
        elif abs(self.zones[i + 1].depth - zone.depth) > 1e-6 and \
                self.zones[i + 1].depth < zone.depth:
            yb -= t
        return rect(0.0, ya, self.width, yb)


def layout(scene, z1: float) -> Layout:
    """Where every zone of a hybrid scene is, for a deck top at `z1`."""
    spec = scene.rack
    rows = rows_of(spec)
    mat = sheet_of(spec)
    t = mat.thickness
    joint = spec.joint
    if joint not in ("tslot", "finger"):
        raise ValueError(f"unknown joint {joint!r}: 'tslot' or 'finger'")
    inset = t if joint == "tslot" else 0.0
    overhang = rack.RAIL_OVERHANG
    placed = rack.layout_rows(rows, overhang)
    lo = placed[0].slots[0] - rack.RAIL_SLOT_C
    hi = placed[-1].slots[1] + (rack.RAIL_H - rack.RAIL_SLOT_C)
    width = frame_width(spec.hp)
    # A wall beside the rails stands where a laser frame's base plate does:
    # its inner face clear of the rail, its slots clear of the M5s.
    gap = (rack.laser_min_margin(t, joint, overhang, mat.joint_clearance)
           - overhang - inset)
    rack_depth = spec.module_panel_t + spec.module_depth
    rack_zone = Zone("rack", -gap, hi - lo + gap, rack_depth)

    ids = section_ids(scene)
    below, above = [], []
    y_lo, y_hi = rack_zone.ya, rack_zone.yb
    for sid, s in zip(ids, scene.sections):
        if s.side == "below":
            below.append(Zone(sid, y_lo - s.length, y_lo, s.depth, s))
            y_lo -= s.length
        else:
            above.append(Zone(sid, y_hi, y_hi + s.length, s.depth, s))
            y_hi += s.length
    zones = list(reversed(below)) + [rack_zone] + above

    p = spec.panel
    notes: list[str] = []
    asked = (p.front, p.back, p.bottom, p.top)
    radii = (p.r_bottom_front, p.r_bottom_back, p.r_top_back, p.r_top_front)
    if joint == "finger":
        # fingers are cut into the side panel's own edges: every plate is
        # flush with one, so there is nothing for an overhang to be
        if any(v > 0 for v in asked + radii):
            notes.append("finger joints are cut into the side panels' edges, "
                         "so their overhangs and corner radii are 0; use "
                         "T-slots for a lip or a rounded corner")
        grow = (0.0, 0.0, 0.0, 0.0)
        radii = (0.0, 0.0, 0.0, 0.0)
    else:
        grow = tuple(max(v, inset) for v in asked)
    return Layout(t=t, joint=joint, inset=inset, width=width, z1=z1,
                  zr=z1 - spec.module_panel_t, rows=rows, placed=placed,
                  lo=lo, rail_hi=hi - lo, gap=gap, zones=zones, grow=grow,
                  radii=radii, notes=notes)


# --------------------------------------------------------------------------
# the side panel: the stepped profile, grown and rounded
# --------------------------------------------------------------------------


def _round(poly, corner, inward, r, name):
    if r <= 0:
        return poly
    cy, cz = corner
    dy, dz = inward
    sq = rect(min(cy, cy + dy * r), min(cz, cz + dz * r),
              max(cy, cy + dy * r), max(cz, cz + dz * r))
    disc = Point(cy + dy * r, cz + dz * r).buffer(r, quad_segs=16)
    return poly.difference(sq).union(disc.intersection(sq))


def side_panel(L: Layout) -> tuple[Polygon, list[rack.Hole]]:
    """The side panel outline in world (y, z), and its M5 holes."""
    front, back, bottom, top = L.grow
    zt = L.z1 + front
    rects = []
    n = len(L.zones)
    for i, z in enumerate(L.zones):
        # a step wall is T-slotted whatever the case's joint -- above the
        # shallower floor it stands inside the panel -- so past a step the
        # panel always reaches a sheet beyond it
        e_lo = bottom if i == 0 else L.t
        e_hi = top if i == n - 1 else L.t
        zb = L.zf(z) - L.t - back
        rects.append(rect(z.ya - e_lo, zb, z.yb + e_hi, zt))
    poly = unary_union(rects)
    if isinstance(poly, MultiPolygon):
        raise ValueError("the side panel came apart: a section is too short")
    y0, y1 = L.y_min - bottom, L.y_max + top
    zb0 = L.zf(L.zones[0]) - L.t - back
    zb1 = L.zf(L.zones[-1]) - L.t - back
    r_bf, r_bb, r_tb, r_tf = L.radii
    corners = (
        ("bottom / front", (y0, zt), (1, -1), r_bf,
         (L.y_min, L.z1), ),
        ("bottom / back", (y0, zb0), (1, 1), r_bb,
         (L.y_min, zb0 + back)),
        ("top / back", (y1, zb1), (-1, 1), r_tb,
         (L.y_max, zb1 + back)),
        ("top / front", (y1, zt), (-1, -1), r_tf, (L.y_max, L.z1)),
    )
    for name, corner, inward, r, skin in corners:
        poly = _round(poly, corner, inward, r, name)
        # the joints' slots at the corner need a web of panel round them:
        # half a sheet past the corner of the plates' outer faces
        web = L.t / 2
        need = Point(skin[0] - inward[0] * web, skin[1] - inward[1] * web)
        if r > 0 and not poly.buffer(1e-6).covers(need):
            raise ValueError(
                f"the {name} corner radius of {r:g} mm cuts into the joints "
                f"there: make it smaller, or the overhangs beside it larger")
    holes = []
    zb = L.zr - rack.rail_bore_depth()
    for i, row in enumerate(L.placed):
        for end, y in zip(("lo", "hi"), row.slots):
            holes.append(rack.Hole(at=(L.wy(y), zb), diameter=rack.M5_CLEAR,
                                   note=f"M5 into row {i} {end} rail end"))
    return poly, holes


# --------------------------------------------------------------------------
# the plates
# --------------------------------------------------------------------------


@dataclass
class Flat:
    """A deck or a floor: horizontal, from `y0` to `y1`, its underside at
    `z`, jointed along its outer face (`top` for a deck)."""

    name: str
    role: str
    y0: float
    y1: float
    z: float
    top: bool
    trim0: float = 0.0
    trim1: float = 0.0
    zone: Optional[Zone] = None


@dataclass
class Wall:
    """An end or step wall: its outer face at `y`, standing towards +y
    (`up`) or -y from it, from `z0` to `z1`."""

    name: str
    role: str
    y: float
    up: bool
    z0: float
    z1: float


def _wall_y(w: Wall, t: float) -> tuple[float, float]:
    return (w.y, w.y + t) if w.up else (w.y - t, w.y)


def plates_of(L: Layout) -> tuple[list[Flat], list[Wall]]:
    """Every deck, floor and wall, placed."""
    t = L.t
    # A deck's or floor's joint stops a sheet short of the wall it meets:
    # a T-slot's slots want a web of panel between them, and beside a step
    # the panel reaches past the floor's outer face there.
    trim = t
    zones = L.zones
    n = len(zones)
    walls = [Wall("end-bottom", "wall", zones[0].ya, True,
                  L.zf(zones[0]) - t, L.z1),
             Wall("end-top", "wall", zones[-1].yb, False,
                  L.zf(zones[-1]) - t, L.z1)]
    #: per boundary i (between zone i and i+1): which side its step wall
    #: stands on, or None
    step: list[Optional[str]] = []
    for i in range(n - 1):
        a, b = zones[i], zones[i + 1]
        if abs(a.depth - b.depth) <= 1e-6:
            step.append(None)
            continue
        # The step wall stands in the deeper zone and runs from its floor up
        # to the deck: a step of a few millimetres is too short for any
        # joint, and the full height carries the decks' ends besides.
        yb = a.yb
        if a.depth > b.depth:
            walls.append(Wall(f"step-{a.name}-{b.name}", "wall", yb, False,
                              L.zf(a) - t, L.z1))
            step.append("below")
        else:
            walls.append(Wall(f"step-{a.name}-{b.name}", "wall", yb, True,
                              L.zf(b) - t, L.z1))
            step.append("above")

    flats: list[Flat] = []
    # floors: one per run of zones at one depth
    i = 0
    while i < n:
        j = i
        while j + 1 < n and step[j] is None:
            j += 1
        y0, y1 = zones[i].ya, zones[j].yb
        if i == 0:
            y0 += t
        elif step[i - 1] == "above":
            y0 += t                 # this run's own step wall stands in it
        if j == n - 1:
            y1 -= t
        elif step[j] == "below":
            y1 -= t
        names = "-".join(z.name for z in zones[i:j + 1])
        flats.append(Flat(f"floor-{names}", "floor", y0, y1,
                          L.zf(zones[i]) - t, False, trim, trim, zones[i]))
        i = j + 1
    # decks: one per section, stopping at a wall; beside the rack with no
    # wall between, run on to the rails' outer edge
    for k, z in enumerate(zones):
        if z.is_rack:
            continue
        y0, y1, t0, t1 = z.ya, z.yb, 0.0, 0.0
        if k == 0:
            y0, t0 = y0 + t, trim
        elif step[k - 1] is not None:
            y0, t0 = y0 + (t if step[k - 1] == "above" else 0.0), trim
        elif zones[k - 1].is_rack:
            y0 = L.rail_hi
        if k == n - 1:
            y1, t1 = y1 - t, trim
        elif step[k] is not None:
            y1, t1 = y1 - (t if step[k] == "below" else 0.0), trim
        elif zones[k + 1].is_rack:
            y1 = 0.0
        flats.append(Flat(f"deck-{z.name}", "deck", y0, y1, L.z1 - t, True,
                          t0, t1, z))
    return flats, walls


def build_hybrid(res, scene=None):
    """The hybrid case of a resolved `kind: hybrid` scene, as a box of
    plates (`box.BoxModel`) with the rails as bought members."""
    from .box import BoxModel, BoxPlate, Standoff
    from .case import _layer_geometry
    from .fasteners import TAP_DRILL, fastener

    scene = scene or res.scene
    spec = scene.case
    z1 = deck_top(res)
    L = layout(scene, z1)
    mat = sheet_of(scene.rack)
    t, W = L.t, L.width
    wt = W + 2 * t
    notes = list(L.notes)
    cheek_geom, holes = side_panel(L)
    flats, walls = plates_of(L)

    # -- the decks: one layered lid over every section, cut into plates ----
    pad = spec.part_clearance
    lid_outer = rect(-t, L.y_min, W + t, L.y_max)
    zb_all = min(L.zf(z) for z in L.zones) - t
    lid, lid_notes = _layer_geometry(res, spec, lid_outer, (z1 - t, z1),
                                     "lid", (zb_all, z1), [(z1 - t, z1)])
    standoffs: list[Standoff] = []
    floor_holes: dict[str, list[Polygon]] = {}
    lid_holes = []
    for s in res.supports:
        clear = (s.screw_d + spec.screw_clearance) / 2
        hole = Point(*s.at).buffer(clear, quad_segs=16)
        if s.mode.value == "from_lid":
            lid_holes.append(hole)
            standoffs.append(Standoff(s.placement, "deck",
                                      (z1 - t) - s.board_top, s.screw, s.at))
        elif s.mode.value == "from_floor":
            fl = next((f for f in flats if f.role == "floor"
                       and f.y0 <= s.at[1] <= f.y1), None)
            if fl is None:
                continue
            floor_holes.setdefault(fl.name, []).append(hole)
            standoffs.append(Standoff(s.placement, fl.name,
                                      s.board_bottom - (fl.z + t), s.screw,
                                      s.at))
    if lid_holes:
        lid = lid.difference(unary_union(lid_holes))

    def flat_geom(f: Flat):
        L_ = f.y1 - f.y0
        g = rect(0.0, 0.0, wt, L_)
        if f.role == "deck":
            world = lid.intersection(rect(-t, f.y0, W + t, f.y1))
            g = translate(world, t, -f.y0)
            g = _largest(g)
            # keep the joint edges whole: the lid's cuts stop short of them
        elif floor_holes.get(f.name):
            g = g.difference(translate(unary_union(floor_holes[f.name]),
                                       t, -f.y0))
        for u0 in (0.0, wt - t):
            if f.trim0:
                g = g.difference(rect(u0, 0.0, u0 + t, f.trim0))
            if f.trim1:
                g = g.difference(rect(u0, L_ - f.trim1, u0 + t, L_))
        return _largest(g)

    # -- port openings in the walls and the side panels ----------------------
    corridors = [(wc.corridor_poly, wc.corridor_z, pad, wc.ref)
                 for wc in res.connectors if wc.cuts_the_wall]
    corridors += [(so.poly, so.z, 0.0, so.reason) for so in res.side_openings]
    wall_cuts: dict[str, list] = {w.name: [] for w in walls}
    cheek_cuts: list = []
    for poly, zr, p, ref in corridors:
        for w in walls:
            wy0, wy1 = _wall_y(w, t)
            if (poly.intersects(rect(-t, wy0, W + t, wy1))
                    and min(zr[1], w.z1) - max(zr[0], w.z0) > 0):
                # wall frame: u = x + t, v = z up from the wall's bottom
                bx0, _by0, bx1, _by1 = poly.bounds
                wall_cuts[w.name].append(rect(
                    bx0 + t - p, zr[0] - w.z0 - p, bx1 + t + p,
                    zr[1] - w.z0 + p))
                notes.append(f"{w.name}: opening for {ref}")
        for x0, x1 in ((-t, 0.0), (W, W + t)):
            if poly.intersects(rect(x0, L.y_min - 1e3, x1, L.y_max + 1e3)):
                # the side panel is drawn in world (y, z)
                _bx0, by0, _bx1, by1 = poly.bounds
                cheek_cuts.append(rect(by0 - p, zr[0] - p, by1 + p, zr[1] + p))
                notes.append(f"side panel: opening for {ref}")
                break

    # -- joints: every plate into both side panels ---------------------------
    make = jn.tslot_joint if L.joint == "tslot" else jn.finger_joint
    kw = {"fastener": scene.rack.fastener} if L.joint == "tslot" else {}
    keep = rack.laser_keep_clear(t)
    m5 = tuple(Point(*h.at).buffer(h.diameter / 2, quad_segs=12)
               for h in holes)
    cheek = cheek_geom
    for h in m5:
        cheek = cheek.difference(h)
    if cheek_cuts:
        cheek = _largest(cheek.difference(unary_union(cheek_cuts)))
    cheeks = {s: jn.Plate(cheek, mat, m5 + tuple(cheek_cuts), keep)
              for s in ("left", "right")}
    joints: list = []
    out_plates: list = []

    def joint_both(name, plate, cheek_seg, length, t0=0.0, t1=0.0,
                   interior=False):
        # an interior joint needs a slot: T-slots, whatever the case's joint
        mk, kws = ((jn.tslot_joint, {"fastener": scene.rack.fastener})
                   if interior else (make, kw))
        for side, own in (
                ("left", jn.Segment((0.0, t0), (0.0, length - t1), "right")),
                ("right", jn.Segment((wt, t0), (wt, length - t1), "left"))):
            try:
                cheeks[side], plate, j = mk(jn.Edge(cheek_seg, own),
                                            cheeks[side], plate, **kws)
            except jn.JointError as exc:
                raise ValueError(
                    f"the {name} plate cannot be jointed to the {side} side "
                    f"panel: {exc}. A longer or deeper section, thinner "
                    f"sheet or finger joints give it more room.") from None
            joints.append((f"side-{side}", name, j))
        return plate

    for f in flats:
        g = flat_geom(f)
        length = f.y1 - f.y0
        zline = f.z + t if f.top else f.z
        seg = jn.Segment((f.y0 + f.trim0, zline), (f.y1 - f.trim1, zline),
                         "right" if f.top else "left")
        plate = jn.Plate(g, mat, tuple(Polygon(r) for r in g.interiors), keep)
        plate = joint_both(f.name, plate, seg, length, f.trim0, f.trim1)
        pnotes = []
        if f.role == "deck":
            pnotes = [n for n in lid_notes]
        out_plates.append(BoxPlate(f.name, f.role, plate.geom,
                                   (-t, f.y0, f.z), FLAT_AXES, mat, pnotes))
    for w in walls:
        H = w.z1 - w.z0
        g = rect(0.0, 0.0, wt, H)
        cuts = [c for c in wall_cuts[w.name] if not c.is_empty]
        if cuts:
            g = _largest(g.difference(unary_union(cuts)))
        seg = jn.Segment((w.y, w.z0), (w.y, w.z1), "right" if w.up else "left")
        plate = jn.Plate(g, mat, tuple(Polygon(r) for r in g.interiors), keep)
        plate = joint_both(w.name, plate, seg, H,
                           interior=w.name.startswith("step-"))
        y_origin = w.y + t if w.up else w.y
        out_plates.append(BoxPlate(w.name, w.role, plate.geom,
                                   (-t, y_origin, w.z0), WALL_AXES, mat))

    # the side panels, drawn in world (y, z) -- the frame their joints and
    # M5s were cut in
    sides = []
    for side, x in (("left", -t), ("right", W)):
        sides.append(BoxPlate(
            f"side-{side}", "side_panel", cheeks[side].geom, (x, 0.0, 0.0),
            CHEEK_AXES, mat,
            [f"{len(holes)} x M5 into rail ends",
             f"{L.joint} joints to every deck, floor and wall"]))
    plates = sides + out_plates

    # -- the rails: bought by length, drawn but never cut --------------------
    rail_mat = Material(name="aluminium-rail", thickness=W, kerf=0.0,
                        color="#d8dade")
    members = []
    rails = rack._rails(L.placed, W, rack.RAIL_OVERHANG)
    for r in rails:
        members.append(BoxPlate(r.name, "rail", r.section,
                                (0.0, L.wy(r.origin[1]), L.zr), RAIL_AXES,
                                rail_mat))
    m5_len = fastener("M5").bolt_length(t + rack.M5_ENGAGEMENT)
    bom = {f"Eurorack rail, {W:.2f} mm ({scene.rack.hp:g} HP)": len(rails),
           f"M5x{m5_len:g} screw": 2 * len(rails)}
    notes.append(
        f"rails: {len(rails)} x {W:.2f} mm; tap both ends of every rail M5 "
        f"(the {rack.RAIL_END_BORE:g} mm end bore takes the tap as it is, a "
        f"solid end is drilled {TAP_DRILL['M5']:g} mm first); an "
        f"M5x{m5_len:g} through each side panel into each rail end ({t:g} mm panel + {rack.M5_ENGAGEMENT:g} mm of thread)")

    zmin = min(L.zf(z) for z in L.zones) - t
    model = BoxModel(spec=spec, outer=rect(-t, L.y_min - L.grow[2],
                                           W + t, L.y_max + L.grow[3]),
                     plates=plates, joints=joints, standoffs=standoffs,
                     z0=zmin, z1=z1, joint=L.joint, notes=notes,
                     members=members, kind="hybrid")
    model.extras["bom"] = bom
    model.extras["hybrid"] = summary(L, scene)
    model.extras["sheet"] = {"name": mat.name, "thickness": t}
    return model


def summary(L: Layout, scene) -> dict:
    """What the editor and the cut list say about the case."""
    return {
        "hp": scene.rack.hp, "width": L.width,
        "rows": [r.name for r in L.rows],
        "module_depth": scene.rack.module_depth,
        "module_panel_t": scene.rack.module_panel_t,
        "z_top": L.z1, "z_rail": L.zr,
        "aperture": [0.0, 0.0, L.width, L.rail_hi],
        "length": L.y_max - L.y_min + L.grow[2] + L.grow[3],
        "zones": [{"name": z.name, "rack": z.is_rack, "y0": z.ya, "y1": z.yb,
                   "depth": z.depth, "z_floor": L.zf(z),
                   "side": None if z.is_rack else z.section.side}
                  for z in L.zones],
        "joint": L.joint, "sheet_t": L.t,
    }


def _largest(geom):
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type == "Polygon"]
        return max(polys, key=lambda g: g.area) if polys else Polygon()
    return geom


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def hybrid_issues(res) -> list[tuple[str, str, str, list[str]]]:
    """(level, code, message, refs) for a hybrid scene: the sections, and
    every placed part held to one -- inside it, clear of the rack's space,
    and no deeper than its floor."""
    scene = res.scene
    out = []
    ids = section_ids(scene)
    for sid in sorted({i for i in ids if ids.count(i) > 1}):
        out.append(("error", "hybrid",
                    f"two sections are called {sid}: give each its own id",
                    []))
    try:
        L = layout(scene, deck_top(res))
    except ValueError as exc:
        out.append(("error", "hybrid", str(exc), []))
        return out
    for n in L.notes:
        out.append(("info", "hybrid", n, []))
    if scene.rack.module_depth < rack.MIN_MODULE_DEPTH - 1e-6:
        out.append(("warning", "module_depth_shallow",
                    f"module depth {scene.rack.module_depth:g} mm is under "
                    f"{rack.MIN_MODULE_DEPTH:g} mm: not even a skiff-friendly "
                    f"module and its power cable fit behind the panel", []))
    if not L.sections:
        out.append(("info", "hybrid",
                    "no parts sections yet: add one above or below the rack "
                    "to place boards", []))
    space = L.rack_space()
    zr0 = L.zf(L.rack)
    inner = [(z, L.inner(z)) for z in L.sections]
    room = unary_union([p for _z, p in inner]) if inner else Polygon()
    reported: set[tuple[str, str]] = set()

    def say(pid, what, level, msg):
        if (pid, what) in reported:
            return
        reported.add((pid, what))
        out.append((level, what, msg, [pid]))

    for s in res.solids:
        if s.kind is not VolumeKind.body or s.z[0] >= L.z1 - 1e-6:
            continue                # above the deck: the deck's business
        pid = s.placement
        if (s.poly.intersection(space).area > 0.01
                and min(s.z[1], L.z1) - max(s.z[0], zr0) > GRAZE):
            say(pid, "in_rack", "error",
                f"{s.ref} sits in the rack's space: it is kept clear for the "
                f"modules fitted later -- move it into a section")
            continue
        outside = s.poly.difference(room).area if inner else s.poly.area
        if outside > 0.5:
            say(pid, "outside_section", "error",
                f"{s.ref} is not inside a section"
                + (f" ({outside:.0f} mm^2 outside)" if inner else
                   ": the case has no sections yet"))
            continue
        hit = [z for z, p in inner if s.poly.intersection(p).area > 0.01]
        if not hit:
            continue
        floor = max(L.zf(z) for z in hit)
        if s.z[0] < floor - 1e-6:
            z = max(hit, key=lambda z: L.zf(z))
            say(pid, "too_deep", "error",
                f"{s.ref} reaches {floor - s.z[0]:.1f} mm below section "
                f"{z.name}'s floor: it is {z.depth:g} mm deep")
    return out


def old_bays_issue(scene) -> list[tuple[str, str, str, list[str]]]:
    """An old scene's `racks:` (rack bays in a box) is ignored: say so."""
    if not scene.racks:
        return []
    return [("warning", "rack_bay",
             f"this scene lists {len(scene.racks)} rack bay"
             f"{'s' if len(scene.racks) != 1 else ''} set into a box. Rack "
             f"bays moved to the hybrid case (a Eurorack frame with parts "
             f"sections above and below): start a new hybrid case and set "
             f"its rows and HP there. The `racks:` list is ignored.", [])]
