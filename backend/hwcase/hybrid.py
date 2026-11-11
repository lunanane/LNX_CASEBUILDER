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
* a **step wall** -- a full-height divider, deck to floor -- stands where
  two neighbouring zones differ in depth, and an **end wall** closes each
  end;
* the **side panels** follow the whole stepped profile, grown by the
  overhangs (front past the deck, back past the floors, bottom and top past
  the end walls) and rounded at the four outer corners.

Every plate is jointed into both side panels -- T-slots, whose slots need a
sheet of panel outside the plate, or glued fingers, flush with its edge --
and to every plate it meets, with the case's joint (`plates_of` says who owns
which corner). A divider stands inside the side panel and on a floor that
may run on beneath it, so its joints there are always T-slots. Each divider
has rounded cable openings just above the floor (`cable_openings`), clear of
every joint feature.

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
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
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
    #: the floor's actual depth: deeper than `depth` where a step too small
    #: for two floors was merged into its neighbour's (`plates_of`)
    floor: float = 0.0

    def __post_init__(self):
        self.floor = self.floor or self.depth

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
    flats: list = field(default_factory=list)
    walls: list = field(default_factory=list)

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
        return self.z1 - zone.floor

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
        """The room a zone holds in plan, its walls excluded."""
        ya, yb = zone.ya, zone.yb
        mid = (ya + yb) / 2
        for w in self.walls:
            if w.y0 + self.t <= ya + 1e-6 or w.y0 >= yb - 1e-6:
                continue
            if w.y0 + self.t / 2 < mid:
                ya = max(ya, w.y0 + self.t)
            else:
                yb = min(yb, w.y0)
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
    # its inner face clear of the rail, its slots clear of the M5s. With
    # T-slots the deck over it runs on to the rail, and its slots there need
    # a sheet of deck beyond the wall.
    gap = (rack.laser_min_margin(t, joint, overhang, mat.joint_clearance)
           - overhang - inset) + (t if joint == "tslot" else 0.0)
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
    L = Layout(t=t, joint=joint, inset=inset, width=width, z1=z1,
               zr=z1 - spec.module_panel_t, rows=rows, placed=placed,
               lo=lo, rail_hi=hi - lo, gap=gap, zones=zones, grow=grow,
               radii=radii, notes=notes)
    L.flats, L.walls = plates_of(L)
    return L


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
    `z`, jointed into the side panels along its outer face (`top` for a
    deck) from `s0` to `s1`."""

    name: str
    role: str
    y0: float
    y1: float
    z: float
    top: bool
    s0: float = 0.0
    s1: float = 0.0
    zones: list = field(default_factory=list)


@dataclass
class Wall:
    """An end wall or a divider, `y0` to `y0 + t` thick, from its floor's
    underside `z0` to the deck top `z1`."""

    name: str
    role: str
    y0: float
    z0: float
    z1: float
    #: the floor plate it stands on (a divider in a merged floor) or ends
    floor: str = ""
    #: the deck plate that covers its top, if any
    deck: Optional[str] = None
    #: a divider's shallower floor, which runs into it (a T-junction)
    shallow: Optional[str] = None
    #: a divider standing on a floor that runs on beneath it
    merged: bool = False
    divider: bool = False
    #: where its side-panel joint runs, world z
    s0: float = 0.0
    s1: float = 0.0


def plates_of(L: Layout) -> tuple[list[Flat], list[Wall]]:
    """Every deck, floor and wall, placed.

    Decks and floors own the corners: they run past an end wall or a
    divider -- by a sheet with T-slots, whose slots in them need material
    outside the wall; to the outer corner with fingers -- and the walls
    stand between them, drawn to the outer faces so the joints can cut
    them. A divider's shallower floor runs into it (a T-junction); a step
    too small for two floors (under a sheet and a millimetre) is a single
    floor at the deeper depth, the divider standing on it.
    """
    t = L.t
    I = L.inset
    zones = L.zones
    n = len(zones)
    D = [z.depth for z in zones]
    # boundaries: a divider wherever the depths differ; merged when the
    # step is too small for one floor to pass under the other
    div = [abs(D[k] - D[k + 1]) > 1e-6 for k in range(n - 1)]
    merged = [div[k] and abs(D[k] - D[k + 1]) < t + 1.0 for k in range(n - 1)]
    cont = [not div[k] or merged[k] for k in range(n - 1)]
    deep = [k if D[k] > D[k + 1] else k + 1 for k in range(n - 1)]

    # floor runs, each at its deepest zone's depth
    runs = []
    i = 0
    while i < n:
        j = i
        while j < n - 1 and cont[j]:
            j += 1
        runs.append((i, j))
        i = j + 1
    run_of = {}
    for r, (i, j) in enumerate(runs):
        for k in range(i, j + 1):
            run_of[k] = r
    floor_depth = {r: max(D[i:j + 1]) for r, (i, j) in enumerate(runs)}
    for z_i, z in enumerate(zones):
        z.floor = floor_depth[run_of[z_i]]
    for k in range(n - 1):
        if merged[k]:
            a, b = zones[k], zones[k + 1]
            sh = a if D[k] < D[k + 1] else b
            L.notes.append(
                f"{sh.name}'s floor drops to {sh.floor:g} mm, its neighbour's: "
                f"a {abs(D[k] - D[k + 1]):g} mm step is too small for two "
                f"floors; the divider stands on the one")

    def zf(k):
        return L.z1 - zones[k].floor

    def run_name(r):
        i, j = runs[r]
        return "floor-" + "-".join(z.name for z in zones[i:j + 1])

    walls: list[Wall] = []
    walls.append(Wall("end-bottom", "wall", zones[0].ya + I, zf(0) - t, L.z1,
                      floor=run_name(run_of[0])))
    walls.append(Wall("end-top", "wall", zones[-1].yb - I - t, zf(n - 1) - t,
                      L.z1, floor=run_name(run_of[n - 1])))
    div_wall: dict[int, Wall] = {}
    for k in range(n - 1):
        if not div[k]:
            continue
        a, b = zones[k], zones[k + 1]
        yb = a.yb
        d = deep[k]
        w = Wall(f"step-{a.name}-{b.name}", "wall",
                 yb - t if d == k else yb, zf(d) - t, L.z1,
                 floor=run_name(run_of[d]), merged=merged[k], divider=True)
        if not merged[k]:
            w.shallow = run_name(run_of[k + 1 if d == k else k])
        walls.append(w)
        div_wall[k] = w

    flats: list[Flat] = []
    for r, (i, j) in enumerate(runs):
        y0, y1 = zones[i].ya, zones[j].yb
        if i > 0:                               # a divider at its low end
            w = div_wall[i - 1]
            y0 = (w.y0 - I) if deep[i - 1] == i else w.y0
        if j < n - 1:
            w = div_wall[j]
            y1 = (w.y0 + t + I) if deep[j] == j else w.y0 + t
        flats.append(Flat(run_name(r), "floor", y0, y1, zf(i) - t, False,
                          zones=zones[i:j + 1]))
    for k, z in enumerate(zones):
        if z.is_rack:
            continue
        y0, y1 = z.ya, z.yb
        deck = f"deck-{z.name}"
        if k > 0:
            nb, w = zones[k - 1], div_wall.get(k - 1)
            if w is None:
                y0 = L.rail_hi if nb.is_rack else y0
            elif nb.is_rack or deep[k - 1] == k:
                w.deck = deck
                y0 = (L.rail_hi if nb.is_rack else w.y0 - I) \
                    if L.joint == "tslot" else w.y0
            else:
                y0 = w.y0 + t + I
        if k < n - 1:
            nb, w = zones[k + 1], div_wall.get(k)
            if w is None:
                y1 = 0.0 if nb.is_rack else y1
            elif nb.is_rack or deep[k] == k:
                w.deck = deck
                y1 = (0.0 if nb.is_rack else w.y0 + t + I) \
                    if L.joint == "tslot" else w.y0 + t
            else:
                y1 = w.y0 - I
        flats.append(Flat(deck, "deck", y0, y1, L.z1 - t, True, zones=[z]))
    for w in walls:
        if w.divider:
            continue
        zone = zones[0] if w.name == "end-bottom" else zones[-1]
        if not zone.is_rack:
            w.deck = f"deck-{zone.name}"

    # where each plate runs into the side panels: a plate's joint stops at
    # the faces of the plates it meets, so no two lines of slots touch
    for f in flats:
        f.s0, f.s1 = f.y0, f.y1
        mid = (f.y0 + f.y1) / 2
        for w in walls:
            if w.y0 + t <= f.y0 + 1e-6 or w.y0 >= f.y1 - 1e-6:
                continue
            if f.role == "floor" and w.merged and w.floor == f.name:
                continue                        # stands on it, mid-floor
            web = t if w.shallow == f.name else 0.0
            if w.y0 + t / 2 < mid:
                f.s0 = max(f.s0, w.y0 + t + web)
            else:
                f.s1 = min(f.s1, w.y0 - web)
    for w in walls:
        # on the floor's top face; a T-slot's first tab stands a web in from
        # the end of its line, so the floor's slots and the wall's never meet
        w.s0 = w.z0 + t
        w.s1 = L.z1 - t if w.deck else L.z1
    return flats, walls


def _y_span(w: Wall, t: float) -> tuple[float, float]:
    return (w.y0, w.y0 + t)


def build_hybrid(res, scene=None):
    """The hybrid case of a resolved `kind: hybrid` scene, as a box of
    plates (`box.BoxModel`) with the rails as bought members."""
    from .box import BoxModel, BoxPlate, Standoff, _joint_band
    from .case import _layer_geometry
    from .fasteners import TAP_DRILL, fastener

    scene = scene or res.scene
    spec = scene.case
    z1 = deck_top(res)
    L = layout(scene, z1)
    mat = sheet_of(scene.rack)
    t, W = L.t, L.width
    wt = W + 2 * t
    flats, walls = L.flats, L.walls
    notes = list(L.notes)
    cheek_geom, holes = side_panel(L)
    flat_by = {f.name: f for f in flats}
    wall_by = {w.name: w for w in walls}

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

    def column_notches(g, length, s0, s1):
        """Clear the plate out of the side panels' thickness wherever its
        side-panel joint does not run."""
        for u0 in (0.0, wt - t):
            if s0 > 1e-9:
                g = g.difference(rect(u0, -1.0, u0 + t, s0))
            if s1 < length - 1e-9:
                g = g.difference(rect(u0, s1, u0 + t, length + 1.0))
        return g

    def flat_geom(f: Flat):
        L_ = f.y1 - f.y0
        g = rect(0.0, 0.0, wt, L_)
        if f.role == "deck":
            world = lid.intersection(rect(-t, f.y0, W + t, f.y1))
            g = _largest(translate(world, t, -f.y0))
        elif floor_holes.get(f.name):
            g = g.difference(translate(unary_union(floor_holes[f.name]),
                                       t, -f.y0))
        return _largest(column_notches(g, L_, f.s0 - f.y0, f.s1 - f.y0))

    # -- port openings in the walls and the side panels ----------------------
    corridors = [(wc.corridor_poly, wc.corridor_z, pad, wc.ref)
                 for wc in res.connectors if wc.cuts_the_wall]
    corridors += [(so.poly, so.z, 0.0, so.reason) for so in res.side_openings]
    wall_cuts: dict[str, list] = {w.name: [] for w in walls}
    cheek_cuts: list = []
    for poly, zr, p, ref in corridors:
        for w in walls:
            if (poly.intersects(rect(-t, w.y0, W + t, w.y0 + t))
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

    def wall_geom(w: Wall):
        H = w.z1 - w.z0
        g = rect(0.0, 0.0, wt, H)
        cuts = [c for c in wall_cuts[w.name] if not c.is_empty]
        if cuts:
            g = _largest(g.difference(unary_union(cuts)))
        return _largest(column_notches(g, H, w.s0 - w.z0, w.s1 - w.z0))

    # -- joints: every plate into both side panels ---------------------------
    fz = scene.rack.fastener or "M3"
    make = jn.tslot_joint if L.joint == "tslot" else jn.finger_joint
    kw = {"fastener": fz} if L.joint == "tslot" else {}
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
    parts: dict[str, jn.Plate] = {}

    def joint_both(name, plate, cheek_seg, v0, v1, interior=False):
        # an interior joint needs a slot: T-slots, whatever the case's joint
        mk, kws = ((jn.tslot_joint, {"fastener": fz})
                   if interior else (make, kw))
        for side, own in (
                ("left", jn.Segment((0.0, v0), (0.0, v1), "right")),
                ("right", jn.Segment((wt, v0), (wt, v1), "left"))):
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
        zline = f.z + t if f.top else f.z
        seg = jn.Segment((f.s0, zline), (f.s1, zline),
                         "right" if f.top else "left")
        plate = jn.Plate(g, mat, tuple(Polygon(r) for r in g.interiors), keep)
        parts[f.name] = joint_both(f.name, plate, seg, f.s0 - f.y0,
                                   f.s1 - f.y0)
    for w in walls:
        g = wall_geom(w)
        if w.name == "end-top":     # its outer face is the panel's edge
            seg = jn.Segment((w.y0 + t, w.s0), (w.y0 + t, w.s1), "left")
        else:
            seg = jn.Segment((w.y0, w.s0), (w.y0, w.s1), "right")
        plate = jn.Plate(g, mat, tuple(Polygon(r) for r in g.interiors), keep)
        parts[w.name] = joint_both(w.name, plate, seg, w.s0 - w.z0,
                                   w.s1 - w.z0, interior=w.divider)

    # -- joints: the plates to each other, between the side panels -----------
    # Each edge runs across the width short of both side panels by a joint's
    # reach, so its features never meet the side-panel joints' at a corner;
    # beyond that the plate that owns the corner keeps it and the other is
    # notched clear.
    band = _joint_band(L.joint, t, fz, mat.joint_clearance)
    u0, u1 = t + band, wt - t - band
    if u1 - u0 < 4 * t:
        raise ValueError(f"{scene.rack.hp:g} HP is too narrow to joint the "
                         f"plates to each other")

    def notch(name, v0, v1):
        p = parts[name]
        g = p.geom.difference(unary_union([rect(-1.0, v0, u0, v1),
                                           rect(u1, v0, wt + 1.0, v1)]))
        parts[name] = jn.Plate(_largest(g), p.material, p.holes, p.keep_clear)

    def pp(a, b, a_seg, b_seg, kind):
        mk, kws = ((jn.tslot_joint, {"fastener": fz}) if kind == "tslot"
                   else (jn.finger_joint, {}))
        try:
            parts[a], parts[b], j = mk(jn.Edge(a_seg, b_seg), parts[a],
                                       parts[b], **kws)
        except jn.JointError as exc:
            raise ValueError(f"the {b} plate cannot be jointed to the {a} "
                             f"plate: {exc}") from None
        joints.append((a, b, j))

    def face_on(f: Flat, w: Wall):
        """The wall's face a deck or floor meets it on, in the flat's frame,
        and the side the wall stands on."""
        if w.merged and w.floor == f.name:
            return w.y0 - f.y0, "left"
        if w.y0 + t / 2 > (f.y0 + f.y1) / 2:
            return w.y0 + t - f.y0, "right"
        return w.y0 - f.y0, "left"

    for w in walls:
        H = w.z1 - w.z0
        fl = flat_by[w.floor]
        v, side = face_on(fl, w)
        notch(w.name, -1.0, t)
        pp(fl.name, w.name, jn.Segment((u0, v), (u1, v), side),
           jn.Segment((u0, 0.0), (u1, 0.0), "left"),
           "tslot" if w.merged else L.joint)
        if w.deck:
            dk = flat_by[w.deck]
            v, side = face_on(dk, w)
            notch(w.name, H - t, H + 1.0)
            pp(dk.name, w.name, jn.Segment((u0, v), (u1, v), side),
               jn.Segment((u0, H), (u1, H), "right"), L.joint)
        if w.shallow:
            sh = flat_by[w.shallow]
            Ls = sh.y1 - sh.y0
            vb = sh.z - w.z0
            if w.y0 + t / 2 < (sh.y0 + sh.y1) / 2:
                notch(sh.name, -1.0, t)
                b_seg = jn.Segment((u0, 0.0), (u1, 0.0), "left")
            else:
                notch(sh.name, Ls - t, Ls + 1.0)
                b_seg = jn.Segment((u0, Ls), (u1, Ls), "right")
            pp(w.name, sh.name, jn.Segment((u0, vb), (u1, vb), "left"),
               b_seg, "tslot")

    # -- cable openings in the dividers, clear of every joint ----------------
    co = scene.rack.cable_openings
    for w in walls:
        if not w.divider:
            continue
        feats = [f.geom for _a, _b, j in joints
                 for role, nm in (("a", _a), ("b", _b)) if nm == w.name
                 for _i, f in j.of(role)]
        cut, why = cable_openings(w, flat_by, co, wt, t, feats)
        if cut:
            p = parts[w.name]
            parts[w.name] = jn.Plate(_largest(p.geom.difference(
                unary_union(cut))), p.material, p.holes, p.keep_clear)
            notes.append(f"{w.name}: {len(cut)} cable opening"
                         f"{'s' if len(cut) != 1 else ''}, "
                         f"{co.width:g} x {co.height:g} mm")
        if why:
            notes.append(f"{w.name}: {why}")

    out_plates: list = []
    for f in flats:
        pnotes = list(lid_notes) if f.role == "deck" else []
        out_plates.append(BoxPlate(f.name, f.role, parts[f.name].geom,
                                   (-t, f.y0, f.z), FLAT_AXES, mat, pnotes))
    for w in walls:
        out_plates.append(BoxPlate(w.name, w.role, parts[w.name].geom,
                                   (-t, w.y0 + t, w.z0), WALL_AXES, mat))

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
        f"M5x{m5_len:g} through each side panel into each rail end "
        f"({t:g} mm panel + {rack.M5_ENGAGEMENT:g} mm of thread)")

    zmin = min(L.zf(z) for z in L.zones) - t
    model = BoxModel(spec=spec, outer=rect(-t, L.y_min - L.grow[2],
                                           W + t, L.y_max + L.grow[3]),
                     plates=plates, joints=joints, standoffs=standoffs,
                     z0=zmin, z1=z1, joint=L.joint, notes=notes,
                     members=members, kind="hybrid")
    model.extras["bom"] = bom
    model.extras["hybrid"] = summary(L, scene)
    model.extras["sheet"] = mat.model_dump(mode="json")
    return model


def cable_openings(w: Wall, flat_by, co, wt: float, t: float, feats):
    """Rounded slots through a divider for the cables between its zones:
    `co.count` of them (auto: one per 90 mm of width), `co.width` by
    `co.height` with full radii, just above the higher of the floors it
    meets so a cable lies on the floor through it. Each is slid along the
    wall until it keeps half a sheet and a little clear of every joint
    feature; never more than half the wall's width is opened, so it still
    carries the deck. Returns the openings in the wall's frame, and a note
    if any had to be left out."""
    if co.count == 0:
        return [], None
    W = wt - 2 * t
    width, height = co.width, co.height
    want = co.count if co.count is not None else max(1, int(W // 90.0))
    n = min(want, int(0.5 * W // width))
    if n <= 0:
        return [], "too narrow for a cable opening"
    floors = [flat_by[w.floor]] + ([flat_by[w.shallow]] if w.shallow else [])
    v_lo = max(f.z + t for f in floors) - w.z0 + 1.0
    H = w.z1 - w.z0
    r = min(width, height) / 2
    clear = (unary_union(feats).buffer(t / 2 + 0.5) if feats else Polygon())
    pitch = W / n
    reach = max(0.0, pitch / 2 - width / 2 - 1.0)
    out = []
    for k in range(n):
        c = t + pitch * (k + 0.5)
        found = None
        offsets = [0.0]
        d = 1.0
        while d <= reach + 1e-9:
            offsets += [d, -d]
            d += 1.0
        for dv in (0.0, 3.0, 6.0, 10.0):
            v = v_lo + dv
            if v + height > H - t - 1.0:
                break
            for du in offsets:
                cu = c + du
                if width >= height:
                    line = LineString([(cu - width / 2 + r, v + r),
                                       (cu + width / 2 - r, v + r)])
                else:
                    line = LineString([(cu, v + r), (cu, v + height - r)])
                hole = line.buffer(r, quad_segs=12)
                if hole.bounds[0] < t + 1.0 or hole.bounds[2] > wt - t - 1.0:
                    continue
                if not hole.intersects(clear):
                    found = hole
                    break
            if found is not None:
                break
        if found is not None:
            out.append(found)
    why = None
    if len(out) < want:
        why = (f"{want - len(out)} of {want} cable openings left out: "
               f"no room clear of the joints")
    return out, why


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
                   "depth": z.depth, "floor": z.floor, "z_floor": L.zf(z),
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
