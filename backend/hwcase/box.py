"""A parts scene built as a box of jointed plates instead of a stack of layers.

A layered case gets its depth by stacking sheets, which is right for a slim
box and wasteful for a deep one: forty millimetres of hardware is thirteen
sheets of 3 mm plywood. A box gets it for nothing. Six plates:

* the **deck** is the faceplate -- the panel plane is its top face -- and is
  cut by exactly the code that cuts a layered lid (`case._layer_geometry`),
  so every window, shaft hole, port cutout and screw clearance is the same;
* the **floor** is the plate the boards stand off;
* four **walls** stand between them, carrying an opening wherever a port
  faces them.

Walls are jointed into the deck and the floor (`hwcase.joinery`): captive-nut
T-slots, whose slots in the deck and floor need a sheet of material outside
the wall, so the walls stand a sheet in from the deck's edge; or glued finger
joints, flush. The four vertical corners are butt joints: every wall is
already held along its top and bottom. A T-slot side wall's joints stop a
sheet short of the front and back walls, so their slots never crowd each
other in the deck, and it is notched there so it does not stand in the deck.

Boards are held on standoffs, not posts: `from_lid` screws through the deck,
`from_floor` through the floor, and the cut list gives each standoff's length.

Frames: the box's world is the scene's (x, y, z). Each plate is drawn in its
own (u, v) frame with its lower-left corner at the origin, and `matrix` maps
(u, v, w) -- w through the sheet's thickness -- into the world, with a
positive determinant so a renderer extruding along w does not turn it inside
out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry import box as rect
from shapely.ops import unary_union

from . import joinery as jn
from .schema import Material

#: plate (u, v, w) -> world axes, per wall: (u axis, v axis, w axis)
_AXES = {
    "deck":  ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "floor": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "back":  ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "left":  ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "right": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}

#: which wall a port faces, by the direction its plug comes in from
_WALL_OF_NORMAL = {(0, -1): "front", (0, 1): "back", (-1, 0): "left",
                   (1, 0): "right"}
_WALL_OF_FACE = {"-y": "front", "+y": "back", "-x": "left", "+x": "right"}


@dataclass
class BoxPlate:
    """One cut plate of the box."""

    name: str
    role: str                         # deck | floor | wall | cheek
    geom: Polygon                     # in its own (u, v) frame
    origin: tuple[float, float, float]
    axes: tuple                       # (u, v, w) world directions
    material: Material
    notes: list[str] = field(default_factory=list)

    @property
    def matrix(self) -> list[float]:
        """Row-major 4x4: plate (u, v, w) -> world."""
        (ux, uy, uz), (vx, vy, vz), (wx, wy, wz) = self.axes
        ox, oy, oz = self.origin
        return [ux, vx, wx, ox, uy, vy, wy, oy, uz, vz, wz, oz, 0, 0, 0, 1]

    def to_world(self, u: float, v: float, w: float = 0.0):
        (ux, uy, uz), (vx, vy, vz), (wx, wy, wz) = self.axes
        ox, oy, oz = self.origin
        return (ox + u * ux + v * vx + w * wx, oy + u * uy + v * vy + w * wy,
                oz + u * uz + v * vz + w * wz)


@dataclass
class Standoff:
    placement: str
    through: str                      # deck | floor
    length: float
    screw: Optional[str]
    at: tuple[float, float]


@dataclass
class BoxModel:
    spec: object
    outer: Polygon                    # the deck's footprint, world XY
    plates: list[BoxPlate]
    joints: list                      # (plate a, plate b, joinery.Joint)
    standoffs: list[Standoff]
    z0: float                         # underside of the floor
    z1: float                         # top of the deck: the panel plane
    joint: str = "tslot"
    #: a box has no layers; kept so code that walks a case's layers -- the
    #: renderer, the orphan-support report -- sees an empty stack, not a crash
    layers: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: M10: anything a rack bay adds (rails, module space), for the BOM
    extras: dict = field(default_factory=dict)

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    def plate(self, name: str) -> BoxPlate:
        return next(p for p in self.plates if p.name == name)

    def bill_of_materials(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.plates:
            out[p.material.name] = out.get(p.material.name, 0) + 1
        for _a, _b, j in self.joints:
            for item, n in j.bom().items():
                out[item] = out.get(item, 0) + n
        for item, n in self.extras.get("bom", {}).items():
            out[item] = out.get(item, 0) + n
        if self.standoffs:
            out["standoffs"] = len(self.standoffs)
        return out


# --------------------------------------------------------------------------
# building it
# --------------------------------------------------------------------------


def _largest(geom) -> Polygon:
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def panel_plane(res, spec) -> float:
    if res.panels:
        return max(res.panels.values())
    return res.bounds()[5] + spec.ceiling_gap


def build_box(res, spec, deck_top: Optional[float] = None,
              floor_top: Optional[float] = None, outer=None,
              deck_cuts=(), extra=None) -> BoxModel:
    """The box around a resolved scene.

    `deck_top`, `floor_top`, `outer` and `deck_cuts` let a caller (M10's rack
    bays) fix the deck's height, the box's footprint and cut extra openings;
    `extra(box_parts)` is called before the joints are cut, to add plates and
    joints of its own. Left alone, everything follows from the hardware.
    """
    from .case import _layer_geometry, outer_shape

    mat = spec.materials[0]
    t = mat.thickness
    joint = spec.box_joint
    if joint not in ("tslot", "finger"):
        raise ValueError(f"unknown box joint {joint!r}: 'tslot' or 'finger'")
    i = t if joint == "tslot" else 0.0          # walls stand this far in
    trim = t if joint == "tslot" else 0.0       # side-wall joints stop short

    if outer is None:
        outer = outer_shape(res, spec)
    x0, y0, x1, y1 = outer.bounds
    W, D = x1 - x0, y1 - y0
    footprint = rect(x0, y0, x1, y1)

    z1 = panel_plane(res, spec) if deck_top is None else deck_top
    if floor_top is None:
        zmin = res.bounds()[2]
        pcbs = [s.z[0] for s in res.solids if s.name == "pcb"]
        floor_top = min(zmin, (min(pcbs) if pcbs else zmin) - spec.floor_gap)
    zf = floor_top
    z0 = zf - t
    H = z1 - z0
    notes: list[str] = []

    # -- the deck: the faceplate, cut as a layered lid is ------------------
    deck_world, deck_notes = _layer_geometry(
        res, spec, footprint, (z1 - t, z1), "lid", (z0, z1), [(z1 - t, z1)])
    for c in deck_cuts:
        deck_world = deck_world.difference(c)
    standoffs: list[Standoff] = []
    floor_holes: list[Polygon] = []
    for s in res.supports:
        clear = (s.screw_d + spec.screw_clearance) / 2
        hole = Point(*s.at).buffer(clear, quad_segs=16)
        if s.mode.value == "from_lid":
            deck_world = deck_world.difference(hole)
            standoffs.append(Standoff(s.placement, "deck",
                                      (z1 - t) - s.board_top, s.screw, s.at))
        elif s.mode.value == "from_floor":
            floor_holes.append(hole)
            standoffs.append(Standoff(s.placement, "floor",
                                      s.board_bottom - zf, s.screw, s.at))
    deck = _largest(translate(deck_world, -x0, -y0))
    floor = rect(0.0, 0.0, W, D)
    if floor_holes:
        floor = _largest(floor.difference(
            translate(unary_union(floor_holes), -x0, -y0)))

    # -- the walls, with their port openings --------------------------------
    lx, ly = W - 2 * i, D - 2 * i - 2 * t
    if ly <= 2 * trim or lx <= 0:
        raise ValueError(f"a {W:.1f} x {D:.1f} mm box is too small for "
                         f"{t:g} mm {joint} walls")
    wall_frames = {
        # name: (u length, world origin, u-offset function from world xy)
        "front": (lx, (x0 + i, y0 + i + t, z0), lambda x, y: x - (x0 + i)),
        "back":  (lx, (x0 + i, y1 - i, z0), lambda x, y: x - (x0 + i)),
        "left":  (ly, (x0 + i, y0 + i + t, z0), lambda x, y: y - (y0 + i + t)),
        "right": (ly, (x1 - i - t, y0 + i + t, z0),
                  lambda x, y: y - (y0 + i + t)),
    }
    openings: dict[str, list[Polygon]] = {w: [] for w in wall_frames}
    for wc in res.connectors:
        if not wc.cuts_the_wall:
            continue
        key = (round(wc.face_normal[0]), round(wc.face_normal[1]))
        wall = _WALL_OF_NORMAL.get(key)
        if wall is None:
            continue                            # up or down: the deck's job
        openings[wall].append(_projected(
            wc.corridor_poly, wc.corridor_z, wall, wall_frames, z0,
            spec.part_clearance))
        notes.append(f"{wall} wall: opening for {wc.ref} ({wc.conn.type})")
    for so in res.side_openings:
        wall = _WALL_OF_FACE.get(so.side.value)
        if wall is None:
            continue
        openings[wall].append(_projected(so.poly, so.z, wall, wall_frames,
                                         z0, 0.0))
        notes.append(f"{wall} wall: {so.reason}")

    walls: dict[str, Polygon] = {}
    for name, (length, _o, _f) in wall_frames.items():
        g = rect(0.0, 0.0, length, H)
        if name in ("left", "right") and trim:
            for u0 in (0.0, length - trim):    # notched where joints stop
                g = g.difference(rect(u0, 0.0, u0 + trim, t))
                g = g.difference(rect(u0, H - t, u0 + trim, H))
        cut = [o.intersection(rect(0.0, 0.0, length, H)) for o in openings[name]]
        cut = [c for c in cut if not c.is_empty]
        if cut:
            g = g.difference(unary_union(cut))
        walls[name] = _largest(g)

    # -- joints --------------------------------------------------------------
    make = jn.tslot_joint if joint == "tslot" else jn.finger_joint
    kw = {"fastener": spec.box_fastener} if joint == "tslot" else {}
    keep = t / 2

    def jplate(geom, holes=()):
        return jn.Plate(geom, mat, tuple(holes), keep)

    deck_p = jplate(deck, [Polygon(r) for r in deck.interiors])
    floor_p = jplate(floor, [Polygon(r) for r in floor.interiors])
    wall_p = {n: jplate(g, [Polygon(r) for r in g.interiors])
              for n, g in walls.items()}

    # (a segment in deck/floor frame, b segment in wall frame) per wall
    def segs(name, top: bool):
        length = wall_frames[name][0]
        hv = H if top else 0.0
        b_in = "right" if top else "left"       # wall material below / above
        if name == "front":
            return (jn.Segment((i, i), (W - i, i), "left"),
                    jn.Segment((0.0, hv), (length, hv), b_in))
        if name == "back":
            return (jn.Segment((i, D - i), (W - i, D - i), "right"),
                    jn.Segment((0.0, hv), (length, hv), b_in))
        v0, v1 = i + t + trim, D - i - t - trim
        u = i if name == "left" else W - i
        side = "right" if name == "left" else "left"
        return (jn.Segment((u, v0), (u, v1), side),
                jn.Segment((trim, hv), (length - trim, hv), b_in))

    joints = []
    parts = {"deck": deck_p, "floor": floor_p, **wall_p}
    #: name -> (role, world origin, axes) for plates `extra` adds
    placed: dict[str, tuple] = {}
    if extra is not None:
        extra(parts, joints, dict(t=t, i=i, x0=x0, y0=y0, z0=z0, z1=z1,
                                  W=W, D=D, H=H, make=make, kw=kw, mat=mat,
                                  place=placed, notes=notes))
    # A wall full of ports cannot take a joint everywhere: a T-slot's nut
    # sits a bolt's length in from the edge, and the Pi's USB stack reaches
    # nearly to the deck. So each edge is jointed along the stretches clear of
    # every opening, and left as a plain butt -- the wall notched clear of the
    # deck or floor -- where it is not.
    band = _joint_band(joint, t, spec.box_fastener, mat.joint_clearance)
    for name in ("front", "back", "left", "right"):
        length = wall_frames[name][0]
        joined_any = False
        for top, flat in ((True, "deck"), (False, "floor")):
            a_seg, b_seg = segs(name, top)
            u0 = b_seg.p0[0]                    # wall u at the segment's start
            near = [(o.bounds[0], o.bounds[2]) for o in openings[name]
                    if (o.bounds[3] > H - band if top else o.bounds[1] < band)]
            runs = _runs(b_seg.length, [(a - u0 - keep, b - u0 + keep)
                                        for a, b in near])
            joined = []
            for s0, s1 in runs:
                a_sub = jn.Segment(a_seg.point(s0), a_seg.point(s1), a_seg.inside)
                b_sub = jn.Segment(b_seg.point(s0), b_seg.point(s1), b_seg.inside)
                try:
                    parts[flat], parts[name], j = make(
                        jn.Edge(a_sub, b_sub), parts[flat], parts[name], **kw)
                except jn.JointError:
                    continue                    # too short: a butt stretch
                joints.append((flat, name, j))
                joined.append((s0, s1))
            # notch the wall clear of the plate wherever it is not jointed
            gaps = _complement(joined, 0.0, b_seg.length)
            if gaps:
                cut = unary_union([b_seg.to_plate(rect(g0, -1.0, g1, t))
                                   for g0, g1 in gaps])
                parts[name] = jn.Plate(_largest(parts[name].geom.difference(cut)),
                                       mat, parts[name].holes, keep)
            if joined:
                joined_any = True
            else:
                notes.append(f"the {name} wall is not jointed to the {flat}: "
                             f"its openings leave no stretch long enough")
        if not joined_any:
            raise ValueError(
                f"the {name} wall cannot be jointed to the deck or the floor "
                f"anywhere: its openings leave no stretch of edge long enough "
                f"for a {joint} joint")

    plates = [
        BoxPlate("deck", "deck", parts["deck"].geom, (x0, y0, z1 - t),
                 _AXES["deck"], mat, deck_notes),
        BoxPlate("floor", "floor", parts["floor"].geom, (x0, y0, z0),
                 _AXES["floor"], mat),
    ]
    for name, (_length, origin, _f) in wall_frames.items():
        plates.append(BoxPlate(name, "wall", parts[name].geom, origin,
                               _AXES[name], mat))
    for name, (role, origin, axes) in placed.items():   # added by `extra`
        plates.append(BoxPlate(name, role, parts[name].geom, origin, axes,
                               mat))
    return BoxModel(spec=spec, outer=footprint, plates=plates, joints=joints,
                    standoffs=standoffs, z0=z0, z1=z1, joint=joint,
                    notes=notes)


def _joint_band(joint: str, t: float, fastener: str, clearance: float) -> float:
    """How far in from a wall's edge a joint reaches, plus the web it keeps:
    a finger is a sheet deep; a T-slot's channel runs a bolt's length in."""
    if joint == "finger":
        return t + t / 2
    from .fasteners import fastener as _fastener

    f = _fastener(fastener)
    needed = t + f.thread_d + f.nut_t + clearance + f.thread_past_nut
    return f.bolt_length(needed) + clearance + t / 2


def _runs(length: float, blocked: list[tuple[float, float]]
          ) -> list[tuple[float, float]]:
    """[0, length] minus the blocked intervals, as clear stretches."""
    return _complement(sorted(blocked), 0.0, length)


def _complement(taken: list[tuple[float, float]], lo: float, hi: float
                ) -> list[tuple[float, float]]:
    out, cursor = [], lo
    for a, b in sorted(taken):
        if a > cursor + 1e-9:
            out.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
    if cursor < hi - 1e-9:
        out.append((cursor, hi))
    return [(a, b) for a, b in out if b - a > 1e-6]


def _projected(poly, zr, wall, frames, z0, pad) -> Polygon:
    """A corridor seen along its wall's normal: its extent across the wall,
    its height up it."""
    _length, _origin, u_of = frames[wall]
    bx0, by0, bx1, by1 = poly.bounds
    u0, u1 = sorted((u_of(bx0, by0), u_of(bx1, by1)))
    return rect(u0 - pad, zr[0] - z0 - pad, u1 + pad, zr[1] - z0 + pad)


# --------------------------------------------------------------------------
# what leaves the tool
# --------------------------------------------------------------------------


def box_cutlist(model: BoxModel, sheets, stock) -> str:
    from .export import sheet_manifest

    x0, y0, x1, y1 = model.outer.bounds
    t = model.plates[0].material.thickness
    lines = [
        sheet_manifest(sheets, stock),
        f"box -- {x1 - x0:.1f} x {y1 - y0:.1f} x {model.height:.1f} mm, "
        f"{t:g} mm {model.plates[0].material.name}, {model.joint} joints",
        "",
        f"plates (cut) -- {len(model.plates)}:",
    ]
    for p in model.plates:
        bx0, by0, bx1, by1 = p.geom.bounds
        lines.append(f"  {p.name:<12} {bx1 - bx0:6.1f} x {by1 - by0:6.1f} mm")
    if model.standoffs:
        lines += ["", "standoffs:"]
        for s in model.standoffs:
            lines.append(f"  {s.placement:<16} {s.length:5.1f} mm "
                         f"{s.screw or 'screw'} through the {s.through} "
                         f"at ({s.at[0]:.1f}, {s.at[1]:.1f})")
    hardware = {k: v for k, v in model.bill_of_materials().items()
                if k.endswith((" bolt", " nut", " screw", " mm"))
                or k in ("rails",)}
    if hardware:
        lines += ["", "fixings and bought parts:"]
        lines += [f"  {v:g} x {k}" if not k.endswith(" mm")
                  else f"  {k[:-3]}: {v:.2f} mm" for k, v in hardware.items()]
    for n in model.notes:
        lines.append(f"note: {n}")
    lines.append("")
    return "\n".join(lines)


def box_to_json(model: BoxModel) -> dict:
    """For the browser: each plate's rings in its own frame and the matrix
    that puts it in the world, so the editor extrudes and places it."""
    from .export import _rings

    def rings(geom):
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        return [[list(c) for c in ring] for p in polys for ring in _rings(p)]

    return {
        "kind": "box",
        "z0": model.z0, "z1": model.z1,
        "outline": [list(c) for c in model.outer.exterior.coords],
        "joint": model.joint,
        "plates": [{
            "name": p.name, "role": p.role, "material": p.material.name,
            "color": p.material.color, "thickness": p.material.thickness,
            "matrix": p.matrix, "rings": rings(p.geom),
        } for p in model.plates],
        "standoffs": [{"placement": s.placement, "through": s.through,
                       "length": s.length, "screw": s.screw,
                       "at": list(s.at)} for s in model.standoffs],
        "bom": model.bill_of_materials(),
        "extras": {k: v for k, v in model.extras.items() if k != "bom"},
        "notes": model.notes,
    }
