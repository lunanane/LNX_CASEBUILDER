"""Joints between two flat plates: finger joints and captive-nut T-slots.

Two plates that meet at a right angle share a corner volume: a bar as long
as the joint, as wide as one plate is thick and as deep as the other. A
joint is nothing more than a decision, along the edge, about which plate
owns that bar -- plus a bolt and a nut where the joint has to come apart.

* **Finger joint** (glued boxes). The bar is cut into equal fingers and the
  plates take turns: where `a` keeps a finger, `b` has a notch, and the other
  way round. Both plates are drawn to the outer corner.
* **T-slot** (D-1, A-6). `b` stands on `a`'s inner face and pushes tabs
  through closed slots in `a`; between the tabs a bolt goes through `a` and
  into a T-shaped cut in `b`'s edge, whose crossbar traps a hex nut. `b` is
  drawn to the outer corner, and so the tabs end flush with `a`'s outer face;
  `a` has to carry on past `b`'s outer face far enough for its slots and
  bolt holes to be closed, so its joint line is usually inside it.

Everything here works in each plate's own 2D frame with shapely, the same
convention as `case.py` and `rack.Member.section`. Nothing knows about 3D:
which edge of which plate meets which is the caller's statement, an `Edge`.

The edge
--------
An `Edge` is two `Segment`s, one per plate, of equal length. A segment runs
from `p0` to `p1` in its plate's frame and lies along the *other* plate's
outer face; `inside` says on which side of it ("left" or "right", looking
from `p0` to `p1`) the other plate's thickness stands, which is also the side
the joint cuts into. Arc length `s` along the two segments names the same
physical point: `s = 0` is `a.p0` and `b.p0`.

Inside the joint code a feature is drawn in the *edge frame* of its plate:
x is `s` along the joint, y is the depth `d` from the joint line, positive
into the joint. `Segment.to_plate` / `Segment.to_edge` convert.

For a finger joint both segments must lie on their plate's outline with the
plate's material on the `inside` side. For a T-slot that holds for `b`; `a`
needs material on both sides of its line.

Kerf
----
Joints are drawn at nominal size, like everything else the engine draws: the
exporters offset each plate outward by half its own material's kerf
(`case.kerf_compensated`) so the beam lands on the line. What the kerf can
break is a feature narrower than the beam, which compensation would close;
`validate_joint` therefore checks the joint *after* compensating and cutting
each plate with its own kerf, not the drawing.

Clearance
---------
A slot is its tab plus the joint clearance, centred: half the clearance on
each side. The two plates may be different stock with different clearances;
the joint takes the larger, since the looser fit is the one that assembles.

Not a constant in sight (D-2): thickness, kerf and clearance come from each
plate's `Material`, bolt and nut sizes from the fastener table (A-6). The
layout choices -- finger width, tab width, bolt pitch -- are keyword
arguments with defaults scaled from those.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Union

from shapely.affinity import affine_transform
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .fasteners import Fastener, fastener as _fastener
from .schema import Material

#: geometric tolerance for "the same length", "the same position" -- far below
#: anything a laser resolves, far above float noise after a transform
TOL = 1e-6
#: positions that meet bought hardware or another sheet are held to this (the
#: plan's 0.01 mm for anything drilled into a mating part)
ALIGN_TOL = 0.01


class JointError(ValueError):
    """The joint cannot be made as asked. The message says why."""


# ---------------------------------------------------------------------------
# the inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """One plate's side of a joint, in that plate's own frame."""

    p0: tuple[float, float]
    p1: tuple[float, float]
    #: the side of p0 -> p1 the joint cuts into (the other plate stands there)
    inside: Literal["left", "right"] = "left"

    def __post_init__(self):
        if self.inside not in ("left", "right"):
            raise ValueError(f"inside must be 'left' or 'right', not {self.inside!r}")
        if self.length <= TOL:
            raise ValueError("a joint segment needs two distinct points")

    @property
    def length(self) -> float:
        return math.dist(self.p0, self.p1)

    def _axes(self):
        ux = (self.p1[0] - self.p0[0]) / self.length
        uy = (self.p1[1] - self.p0[1]) / self.length
        nx, ny = (-uy, ux) if self.inside == "left" else (uy, -ux)
        return ux, uy, nx, ny

    def point(self, s: float, d: float = 0.0) -> tuple[float, float]:
        """The plate-frame point `s` along the joint and `d` into it."""
        ux, uy, nx, ny = self._axes()
        return (self.p0[0] + s * ux + d * nx, self.p0[1] + s * uy + d * ny)

    def to_plate(self, geom: BaseGeometry) -> BaseGeometry:
        """Edge frame (s, d) -> plate frame."""
        ux, uy, nx, ny = self._axes()
        return affine_transform(geom, [ux, nx, uy, ny, self.p0[0], self.p0[1]])

    def to_edge(self, geom: BaseGeometry) -> BaseGeometry:
        """Plate frame -> edge frame (s, d)."""
        ux, uy, nx, ny = self._axes()
        x0, y0 = self.p0
        return affine_transform(geom, [ux, uy, nx, ny,
                                       -(x0 * ux + y0 * uy), -(x0 * nx + y0 * ny)])

    @classmethod
    def on(cls, geom: Polygon, p0, p1) -> "Segment":
        """The segment p0 -> p1 of an outline, `inside` towards its material."""
        left = cls(tuple(p0), tuple(p1), "left")
        probe = Point(left.point(left.length / 2, min(0.01, left.length / 4)))
        return left if geom.covers(probe) else cls(tuple(p0), tuple(p1), "right")


@dataclass(frozen=True)
class Edge:
    """Where two plates meet: one segment in each plate's frame.

    `s` along `a` and `s` along `b` are the same point in the assembly, so
    the two segments have to be the same length.
    """

    a: Segment
    b: Segment

    def __post_init__(self):
        if abs(self.a.length - self.b.length) > TOL:
            raise JointError(
                f"the two sides of a joint must be the same length: "
                f"{self.a.length:.4f} mm on a, {self.b.length:.4f} mm on b")

    @property
    def length(self) -> float:
        return self.a.length


@dataclass(frozen=True)
class Plate:
    """A flat part as a joint sees it: outline, stock, and what to avoid.

    `holes` are declared holes -- rail screws, mounting holes -- in the plate's
    frame. The joint never cuts them and never puts a feature within
    `keep_clear` of one; unset, that distance is the plate's thickness.
    """

    geom: Polygon
    material: Material
    holes: tuple[BaseGeometry, ...] = ()
    keep_clear: Optional[float] = None

    @property
    def t(self) -> float:
        return self.material.thickness

    @property
    def clear(self) -> float:
        return self.t if self.keep_clear is None else self.keep_clear


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

FeatureKind = Literal["tab", "slot", "cutback", "bolt_hole", "tslot", "nut_trap"]


@dataclass(frozen=True)
class Feature:
    """One cut, or one piece of kept material, that the joint made.

    `tab`: material a plate keeps in the corner bar (a finger, or a T-slot
    tab). `slot`: where the other plate's tab goes. `cutback`: the corner bar
    a T-slot's `b` gives up between its tabs. `bolt_hole` in `a`; `tslot` (the
    shank's channel) and `nut_trap` in `b`.

    `span` is the range of `s` along the joint, `depth` the range of `d`
    into it, both in the plate's edge frame; `geom` is the same rectangle (or
    circle) in the plate's own frame. `mate` is the index of the feature this
    one fits into or lines up with.
    """

    kind: FeatureKind
    plate: Literal["a", "b"]
    span: tuple[float, float]
    depth: tuple[float, float]
    geom: Polygon
    mate: Optional[int] = None

    @property
    def width(self) -> float:
        return self.span[1] - self.span[0]

    @property
    def deep(self) -> float:
        return self.depth[1] - self.depth[0]

    @property
    def cut(self) -> bool:
        """Material removed (as opposed to a tab, which is material kept)."""
        return self.kind != "tab"


@dataclass(frozen=True)
class Bolt:
    """One bolt and its captive nut."""

    fastener: str
    #: length under the head, a stock length from the fastener table
    length: float
    #: where along the joint
    s: float


@dataclass(frozen=True)
class Joint:
    kind: Literal["finger", "tslot"]
    edge: Edge
    #: the fit: a slot is its tab plus this, half on each side
    clearance: float
    features: tuple[Feature, ...]
    bolts: tuple[Bolt, ...] = ()
    fastener: Optional[Fastener] = None
    #: T-slot: material between `a`'s inner face and the nut
    nut_web: float = 0.0

    @property
    def nuts(self) -> int:
        return len(self.bolts)

    def bom(self) -> dict[str, int]:
        """Bought parts this joint needs: bolts by length, and nuts."""
        out: dict[str, int] = {}
        for b in self.bolts:
            key = f"{b.fastener}x{b.length:g} bolt"
            out[key] = out.get(key, 0) + 1
        if self.bolts:
            out[f"{self.fastener.name} hex nut"] = len(self.bolts)
        return out

    def of(self, plate: str, kind: Optional[str] = None) -> list[tuple[int, Feature]]:
        return [(i, f) for i, f in enumerate(self.features)
                if f.plate == plate and (kind is None or f.kind == kind)]


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    feature: Optional[int] = None


# ---------------------------------------------------------------------------
# shared geometry
# ---------------------------------------------------------------------------

def _rect(s0, s1, d0, d1) -> Polygon:
    return box(min(s0, s1), min(d0, d1), max(s0, s1), max(d0, d1))


def _circle(cs: float, cd: float, d: float, quad_segs: int = 16) -> Polygon:
    """A polygonal hole whose flats, not its corners, sit on diameter `d`.

    shapely's circle is inscribed in the true one, so a bolt hole drawn with
    it is a hair small. Scaling the radius out by 1/cos(half a step) makes
    the narrowest width the full diameter.
    """
    n = 4 * quad_segs
    r = d / 2 / math.cos(math.pi / n)
    return Point(cs, cd).buffer(r, quad_segs=quad_segs)


def _one_piece(geom) -> BaseGeometry:
    """Drop the numerical crumbs a boolean leaves on a shared edge.

    A notch cut flush with the plate's end leaves, after the transform to
    the plate frame, a sliver a few 1e-12 mm wide. That is float noise, not
    material, and it would otherwise read as a second piece. Anything bigger
    than a thousandth of a square millimetre is kept and left for
    `validate_joint` to report.
    """
    if isinstance(geom, MultiPolygon):
        parts = [p for p in geom.geoms if p.area > 1e-3]
        geom = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return geom


def _check_on_outline(plate: Plate, seg: Segment, name: str) -> None:
    """The joint line must be the plate's edge, with material on `inside`."""
    probe = min(0.01, seg.length / 4)
    edge = seg.to_edge(plate.geom)
    L = seg.length
    inner = edge.intersection(_rect(0, L, 0, probe)).area
    outer = edge.intersection(_rect(0, L, -probe, 0)).area
    if inner < 0.99 * L * probe or outer > 0.01 * L * probe:
        raise JointError(
            f"plate {name}'s joint segment {seg.p0} -> {seg.p1} must run along "
            f"its outline with the material on the {seg.inside}")


def _apply(plate: Plate, seg: Segment, cuts: list[Polygon]) -> Plate:
    if not cuts:
        return plate
    geom = plate.geom.difference(seg.to_plate(unary_union(cuts)))
    return Plate(_one_piece(geom), plate.material, plate.holes, plate.keep_clear)


def _feature(kind, plate_name, seg: Segment, s0, s1, d0, d1, mate=None) -> Feature:
    return Feature(kind, plate_name, (s0, s1), (d0, d1),
                   seg.to_plate(_rect(s0, s1, d0, d1)), mate)


def _choose(candidates, edge, a, b, check: bool, what: str):
    """The first candidate layout that validates; or loudly, why none did."""
    first = None
    for build in candidates:
        a2, b2, joint = build()
        if not check:
            return a2, b2, joint
        problems = validate_joint(joint, a2, b2)
        if not problems:
            return a2, b2, joint
        if first is None:
            first = problems
    if first is None:
        raise JointError(f"{what}: the edge is too short for any layout")
    raise JointError(
        f"{what}: no layout along this {edge.length:.1f} mm edge passes; the "
        f"preferred one fails on:\n  " + "\n  ".join(
            f"[{v.rule}] {v.message}" for v in first))


# ---------------------------------------------------------------------------
# finger joint
# ---------------------------------------------------------------------------

def finger_joint(edge: Edge, a: Plate, b: Plate, *,
                 finger: Optional[float] = None,
                 check: bool = True) -> tuple[Plate, Plate, Joint]:
    """Cut matching fingers into `a` and `b` along `edge`.

    `finger` is the target finger width; unset, three times the thicker
    sheet. The count is the nearest that keeps every finger at least as wide
    as the thicker sheet, odd counts first (both ends then belong to the same
    plate, the usual box corner); other counts, and the other plate owning
    the ends, are tried in turn until one keeps clear of every declared hole.

    Returns the cut plates and the joint record. With `check`, the joint
    returned is guaranteed to pass `validate_joint`; JointError otherwise.
    """
    _check_on_outline(a, edge.a, "a")
    _check_on_outline(b, edge.b, "b")
    L = edge.length
    widest = max(a.t, b.t)
    target = finger if finger is not None else 3.0 * widest
    if target <= 0:
        raise JointError("finger width must be positive")
    c = max(a.material.joint_clearance, b.material.joint_clearance)
    n_max = int(L // widest + TOL)
    if n_max < 2:
        raise JointError(
            f"a finger joint needs two fingers at least {widest:g} mm wide; "
            f"this edge is {L:.1f} mm")
    n_target = min(n_max, max(2, round(L / target)))
    counts = sorted(range(2, n_max + 1),
                    key=lambda n: (abs(n - n_target), n % 2 == 0, n))

    def layout(n: int, first: str):
        def build():
            plates = {"a": a, "b": b}
            segs = {"a": edge.a, "b": edge.b}
            other = {"a": "b", "b": "a"}
            ext = 1.0 + widest              # carry a notch clean off the edge
            feats: list[Feature] = []
            cuts: dict[str, list[Polygon]] = {"a": [], "b": []}
            for i in range(n):
                own = first if i % 2 == 0 else other[first]
                opp = other[own]
                x0, x1 = i * L / n, (i + 1) * L / n
                u0 = x0 + (c / 4 if i > 0 else 0.0)
                u1 = x1 - (c / 4 if i < n - 1 else 0.0)
                feats.append(_feature("tab", own, segs[own], u0, u1,
                                      0.0, plates[opp].t, mate=len(feats) + 1))
                feats.append(_feature("slot", opp, segs[opp], u0 - c / 2,
                                      u1 + c / 2, 0.0, plates[own].t,
                                      mate=len(feats) - 1))
                cuts[opp].append(_rect(u0 - c / 2, u1 + c / 2, -ext, plates[own].t))
            joint = Joint("finger", edge, c, tuple(feats))
            return (_apply(a, edge.a, cuts["a"]), _apply(b, edge.b, cuts["b"]), joint)
        return build

    candidates = [layout(n, first) for n in counts for first in ("a", "b")]
    return _choose(candidates, edge, a, b, check, "finger joint")


# ---------------------------------------------------------------------------
# T-slot with a captive nut
# ---------------------------------------------------------------------------

def tslot_joint(edge: Edge, a: Plate, b: Plate,
                fastener: Union[str, Fastener] = "M3", *,
                tab: Optional[float] = None,
                pitch: Optional[float] = None,
                nut_web: Optional[float] = None,
                check: bool = True) -> tuple[Plate, Plate, Joint]:
    """Tabs on `b` through slots in `a`, with captive-nut bolts between them.

    `a` is the plate the bolt heads sit on; `b` is the plate standing on it,
    whose edge carries the tabs and the T-shaped nut traps. Along the joint
    the pattern is tab, bolt, tab, ..., tab.

    `tab` is the tab width (default twice the thicker sheet), `pitch` the
    target distance between bolts (default 15 bolt diameters), `nut_web` the
    material between `a`'s inner face and the nut (default one bolt
    diameter). The bolt is the shortest stock length that reaches through
    `a`, the web and the nut and leaves `thread_past_nut` beyond it.

    Bolt counts nearest the pitch are tried first, then the same counts with
    the pattern pulled in from either end, until one keeps clear of every
    declared hole. With `check`, the joint returned passes `validate_joint`;
    JointError otherwise.
    """
    f = _fastener(fastener) if isinstance(fastener, str) else fastener
    _check_on_outline(b, edge.b, "b")
    L = edge.length
    ta, tb = a.t, b.t
    c = max(a.material.joint_clearance, b.material.joint_clearance)
    w = tab if tab is not None else 2.0 * max(ta, tb)
    if w <= 0:
        raise JointError("tab width must be positive")
    e = nut_web if nut_web is not None else f.thread_d
    pitch = pitch if pitch is not None else 15.0 * f.thread_d
    web = _min_web(a)
    hole_r = f.clearance_d / 2
    # how close a tab and the bolt beside it may sit, centre to centre: half
    # a slot, a web of `a`, the bolt hole. Two neighbouring nut traps in `b`
    # (two steps apart) need a web of `b` between them as well.
    step_min = max(w / 2 + c / 2 + web + hole_r,
                   (f.nut_af + c + _min_web(b)) / 2)
    margin = w / 2 + c / 2 + web            # first tab centre from either end
    run = L - 2 * margin
    n_max = int(run // (2 * step_min) + TOL)
    if n_max < 1:
        raise JointError(
            f"a {f.name} T-slot needs an edge of at least "
            f"{2 * margin + 2 * step_min:.1f} mm; this one is {L:.1f} mm")
    needed = ta + e + f.nut_t + c + f.thread_past_nut
    try:
        length = f.bolt_length(needed)
    except ValueError as exc:
        raise JointError(str(exc)) from None
    n_target = min(n_max, max(1, round(run / pitch)))
    counts = [n_target] + [n for n in range(n_target - 1, 0, -1)] \
        + [n for n in range(n_target + 1, n_max + 1)]

    def layout(n: int, left: float, right: float):
        def build():
            step = (L - left - right) / (2 * n)
            ext = 1.0 + max(ta, tb)
            feats: list[Feature] = []
            cuts_a: list[Polygon] = []
            cuts_b: list[Polygon] = []
            bolts: list[Bolt] = []
            tabs: list[tuple[float, float]] = []
            for i in range(2 * n + 1):
                sc = left + i * step
                if i % 2 == 0:
                    u0, u1 = sc - w / 2, sc + w / 2
                    tabs.append((u0, u1))
                    feats.append(_feature("tab", "b", edge.b, u0, u1, 0.0, ta,
                                          mate=len(feats) + 1))
                    # centred on b's thickness, half the clearance all round
                    feats.append(_feature("slot", "a", edge.a, u0 - c / 2, u1 + c / 2,
                                          -c / 2, tb + c / 2, mate=len(feats) - 1))
                    cuts_a.append(_rect(u0 - c / 2, u1 + c / 2, -c / 2, tb + c / 2))
                    continue
                k = len(feats)
                hole = Feature("bolt_hole", "a", (sc - hole_r, sc + hole_r),
                               (tb / 2 - hole_r, tb / 2 + hole_r),
                               edge.a.to_plate(_circle(sc, tb / 2, f.clearance_d)),
                               mate=k + 2)
                stem_w = f.thread_d + c
                stem = _feature("tslot", "b", edge.b, sc - stem_w / 2, sc + stem_w / 2,
                                ta, length + c, mate=k)
                d0 = ta + e
                trap = _feature("nut_trap", "b", edge.b,
                                sc - (f.nut_af + c) / 2, sc + (f.nut_af + c) / 2,
                                d0, d0 + f.nut_t + c, mate=k)
                feats += [hole, stem, trap]
                cuts_a.append(_circle(sc, tb / 2, f.clearance_d))
                cuts_b.append(_rect(sc - stem_w / 2, sc + stem_w / 2, -ext, length + c))
                cuts_b.append(_rect(trap.span[0], trap.span[1], *trap.depth))
                bolts.append(Bolt(f.name, length, sc))
            # b gives up the corner bar everywhere but its tabs
            edges = [0.0] + [x for t in tabs for x in t] + [L]
            for g0, g1 in zip(edges[::2], edges[1::2]):
                if g1 - g0 > TOL:
                    feats.append(_feature("cutback", "b", edge.b, g0, g1, 0.0, ta))
                    cuts_b.append(_rect(g0, g1, -ext, ta))
            joint = Joint("tslot", edge, c, tuple(feats), tuple(bolts), f, e)
            return _apply(a, edge.a, cuts_a), _apply(b, edge.b, cuts_b), joint
        return build

    candidates = []
    for n in counts:
        step = run / (2 * n)
        candidates.append(layout(n, margin, margin))
        for frac in (0.5, 1.0):
            pull = frac * step
            if (run - pull) / (2 * n) >= step_min - TOL:
                candidates.append(layout(n, margin + pull, margin))
                candidates.append(layout(n, margin, margin + pull))
    return _choose(candidates, edge, a, b, check, f"{f.name} T-slot joint")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _min_web(p: Plate) -> float:
    """Narrowest strip of sheet allowed around a closed cut: half the sheet.

    Thin enough to fit a T-slot into 2 mm stock, and on 6 mm plywood it
    still leaves three -- less than that and the strip snaps when the bolt
    is tightened.
    """
    return p.t / 2


def _as_cut(p: Plate) -> BaseGeometry:
    """The part the laser actually produces: compensated, then cut.

    `case.kerf_compensated` grows the outline by half the kerf; the beam then
    takes half the kerf away again. Any feature narrower than the kerf is
    closed by the first step and never comes back.
    """
    k = p.material.kerf
    if k <= 0:
        return p.geom
    return p.geom.buffer(k / 2, join_style=2).buffer(-k / 2, join_style=2)


def _owned(p: Plate, seg: Segment, depth: float, L: float) -> list[tuple[float, float]]:
    """The s-intervals where `p` has material, `depth` into the joint."""
    line = seg.to_plate(LineString([(-1.0, depth), (L + 1.0, depth)]))
    hit = _as_cut(p).intersection(line)
    out = []
    for g in getattr(hit, "geoms", [hit]):
        if g.is_empty or g.length <= 0:
            continue
        xs = [x for x, _ in seg.to_edge(g).coords]
        out.append((max(0.0, min(xs)), min(L, max(xs))))
    return sorted((s0, s1) for s0, s1 in out if s1 - s0 > TOL)


def _merge(iv):
    out = []
    for s0, s1 in sorted(iv):
        if out and s0 <= out[-1][1] + TOL:
            out[-1] = (out[-1][0], max(out[-1][1], s1))
        else:
            out.append((s0, s1))
    return out


def validate_joint(joint: Joint, a: Plate, b: Plate) -> list[Violation]:
    """Every rule the joint breaks, not just the first. Empty means sound.

    `a` and `b` are the plates as the joint function returned them -- cut,
    with their declared holes. Rules:

    * `one_piece` -- each plate is still one valid polygon.
    * `tab_slot_fit` -- every tab has a slot, the slot is the tab plus the
      clearance, centred on it; a finger's notch is as deep as the finger's
      plate is thick, a T-slot's slot is the tab's sheet plus the clearance.
    * `complementary` -- along the corner bar, after kerf, the plates never
      both own a point and never leave a gap wider than the clearance (bolt
      holes excepted, they are meant to be empty).
    * `kerf` -- no feature closes up when the plate is kerf-compensated.
    * `nut_trap` -- each trap holds the nut plus clearance, sits on its
      shank channel, and its bolt hole in `a` is on the same axis, in the
      middle of `b`'s sheet; the bolt reaches through the nut.
    * `keep_clear` -- no feature within the plate's keep-clear distance of a
      declared hole.
    * `web` -- slots, bolt holes and nut traps are closed by at least half a
      sheet of material.
    """
    out: list[Violation] = []
    plates = {"a": a, "b": b}
    segs = {"a": joint.edge.a, "b": joint.edge.b}
    other = {"a": "b", "b": "a"}
    c = joint.clearance
    L = joint.edge.length

    for name, p in plates.items():
        if not isinstance(p.geom, Polygon) or not p.geom.is_valid or p.geom.is_empty:
            n = len(getattr(p.geom, "geoms", [p.geom]))
            out.append(Violation("one_piece", f"plate {name} is {n} pieces, "
                                 f"or not a valid polygon, after the joint"))

    # -- tabs and slots -----------------------------------------------------
    for i, f in enumerate(joint.features):
        if f.kind != "tab":
            continue
        m = joint.features[f.mate] if f.mate is not None and \
            0 <= f.mate < len(joint.features) else None
        if m is None or m.kind != "slot" or m.plate == f.plate:
            out.append(Violation("tab_slot_fit",
                                 f"tab {i} on {f.plate} has no slot in the other plate", i))
            continue
        if abs(m.width - (f.width + c)) > TOL or \
                abs((m.span[0] + m.span[1]) - (f.span[0] + f.span[1])) > 2 * TOL:
            out.append(Violation("tab_slot_fit",
                                 f"tab {i} is {f.width:.3f} mm wide, its slot "
                                 f"{m.width:.3f} mm: wants the tab plus {c:g}, centred", i))
        sheet = plates[f.plate].t
        want = sheet + c if joint.kind == "tslot" else sheet
        if abs(m.deep - want) > TOL:
            out.append(Violation("tab_slot_fit",
                                 f"slot {f.mate} is {m.deep:.3f} mm across, the "
                                 f"{sheet:g} mm tab of {f.plate} wants {want:.3f}", f.mate))
        if abs(f.deep - plates[other[f.plate]].t) > TOL:
            out.append(Violation("tab_slot_fit",
                                 f"tab {i} reaches {f.deep:.3f} mm; the sheet it "
                                 f"passes through is {plates[other[f.plate]].t:g}", i))
    for i, f in enumerate(joint.features):
        if f.kind == "slot" and not any(g.kind == "tab" and g.mate == i
                                        for g in joint.features):
            out.append(Violation("tab_slot_fit", f"slot {i} on {f.plate} has no tab", i))

    # -- complementary, after kerf ---------------------------------------------
    if all(isinstance(p.geom, (Polygon, MultiPolygon)) for p in plates.values()):
        own_a = _owned(a, segs["a"], b.t / 2, L)
        own_b = _owned(b, segs["b"], a.t / 2, L)
        overlap = sum(max(0.0, min(x1, y1) - max(x0, y0))
                      for x0, x1 in own_a for y0, y1 in own_b)
        if overlap > 1e-4:
            out.append(Violation("complementary",
                                 f"the plates both claim {overlap:.3f} mm of the "
                                 f"corner bar: they will not go together"))
        covered = _merge(own_a + own_b + [f.span for f in joint.features
                                         if f.kind == "bolt_hole"])
        gaps = []
        cursor = 0.0
        for s0, s1 in covered:
            if s0 - cursor > TOL:
                gaps.append((cursor, s0))
            cursor = max(cursor, s1)
        if L - cursor > TOL:
            gaps.append((cursor, L))
        for g0, g1 in gaps:
            if g1 - g0 > c + 1e-4:
                out.append(Violation("complementary",
                                     f"a {g1 - g0:.3f} mm gap at s = {g0:.2f}..{g1:.2f}, "
                                     f"more than the {c:g} mm clearance"))

    # -- kerf ------------------------------------------------------------------
    for name, p in plates.items():
        if p.material.kerf <= 0 or not isinstance(p.geom, (Polygon, MultiPolygon)):
            continue
        region = unary_union([f.geom for f in joint.features if f.plate == name]) \
            .buffer(p.material.kerf)
        lost = _as_cut(p).symmetric_difference(p.geom).intersection(region).area
        if lost > 1e-3:
            out.append(Violation("kerf",
                                 f"plate {name}: {lost:.3f} mm² of the joint is "
                                 f"narrower than the {p.material.kerf:g} mm kerf"))

    # -- nut traps and bolt holes ----------------------------------------------
    if joint.kind == "tslot":
        fz = joint.fastener
        for i, h in joint.of("a", "bolt_hole"):
            trap = joint.features[h.mate] if h.mate is not None and \
                h.mate < len(joint.features) else None
            if trap is None or trap.kind != "nut_trap":
                out.append(Violation("nut_trap", f"bolt hole {i} has no nut trap", i))
                continue
            stem = next((g for g in joint.features
                         if g.kind == "tslot" and g.mate == i), None)
            if trap.width < fz.nut_af + c - TOL or trap.deep < fz.nut_t + c - TOL:
                out.append(Violation("nut_trap",
                                     f"nut trap {h.mate} is {trap.width:.3f} x "
                                     f"{trap.deep:.3f}; an {fz.name} nut plus clearance "
                                     f"needs {fz.nut_af + c:.3f} x {fz.nut_t + c:.3f}", h.mate))
            hs = (h.span[0] + h.span[1]) / 2
            ts = (trap.span[0] + trap.span[1]) / 2
            hd = (h.depth[0] + h.depth[1]) / 2
            if abs(hs - ts) > ALIGN_TOL:
                out.append(Violation("nut_trap",
                                     f"bolt hole {i} at s = {hs:.3f} misses its nut "
                                     f"trap at s = {ts:.3f}", i))
            if abs(hd - b.t / 2) > ALIGN_TOL:
                out.append(Violation("nut_trap",
                                     f"bolt hole {i} is {hd:.3f} mm into the joint, "
                                     f"not on the middle of b's {b.t:g} mm sheet", i))
            if h.width < fz.clearance_d - TOL:
                out.append(Violation("nut_trap",
                                     f"bolt hole {i} is {h.width:.3f} mm, an {fz.name} "
                                     f"wants {fz.clearance_d:g}", i))
            if stem is None or abs((stem.span[0] + stem.span[1]) / 2 - ts) > ALIGN_TOL \
                    or stem.width < fz.thread_d + c - TOL \
                    or stem.depth[0] > a.t + TOL:
                out.append(Violation("nut_trap",
                                     f"nut trap {h.mate} has no shank channel from "
                                     f"b's shoulder on its axis", h.mate))
            else:
                bolt = next((x for x in joint.bolts if abs(x.s - hs) <= ALIGN_TOL), None)
                if bolt is None:
                    out.append(Violation("nut_trap", f"bolt hole {i} has no bolt", i))
                elif bolt.length < trap.depth[1] + fz.thread_past_nut - TOL \
                        or stem.depth[1] < bolt.length - TOL:
                    out.append(Violation("nut_trap",
                                         f"the {bolt.length:g} mm bolt at s = {hs:.2f} "
                                         f"does not reach through its nut, or its "
                                         f"channel is shorter than the bolt", i))
        if len(joint.bolts) != len(joint.of("b", "nut_trap")):
            out.append(Violation("nut_trap", f"{len(joint.bolts)} bolts for "
                                 f"{len(joint.of('b', 'nut_trap'))} nut traps"))

    # -- keep-clear ------------------------------------------------------------
    for i, f in enumerate(joint.features):
        if not f.cut:
            continue
        p = plates[f.plate]
        for h in p.holes:
            gap = f.geom.distance(h)
            if gap < p.clear - TOL:
                out.append(Violation("keep_clear",
                                     f"{f.kind} {i} on {f.plate} comes {gap:.2f} mm from "
                                     f"a declared hole; keep-clear is {p.clear:g}", i))

    # -- webs round closed cuts ------------------------------------------------
    for name, p in plates.items():
        if not isinstance(p.geom, (Polygon, MultiPolygon)):
            continue
        whole = unary_union([p.geom] + [f.geom for f in joint.features
                                        if f.plate == name and f.cut])
        for i, f in enumerate(joint.features):
            if f.plate != name or f.kind not in ("slot", "bolt_hole", "nut_trap"):
                continue
            if joint.kind == "finger" and f.kind == "slot":
                continue                    # a finger notch is open on purpose
            if not whole.buffer(TOL).covers(f.geom.buffer(_min_web(p))):
                out.append(Violation("web",
                                     f"{f.kind} {i} on {name} is not closed by "
                                     f"{_min_web(p):g} mm of sheet", i))
    return out
