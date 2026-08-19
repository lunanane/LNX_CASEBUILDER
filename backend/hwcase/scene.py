"""Resolving a scene into world geometry, and checking it.

The solver is deliberately small: placements are either free (an explicit world
pose) or snapped to a parent through a mate. Mates along +Z/-Z -- board stacking,
which is what a HAT, a keypad pad or a Eurorack power header actually is -- are
solved exactly. Anything else is reported rather than silently approximated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .geom import Frame, box_polygon, corridor, outline_polygon, z_overlap
from .library import PartLibrary
from .schema import (Box, Confidence, Connector, Face, Part, Placement, Scene,
                     Source, Vec2, VolumeKind)


@dataclass
class Solid:
    """One box of one placement, in world coordinates."""

    placement: str
    part: str
    name: str
    kind: VolumeKind
    poly: Polygon
    z: Vec2
    src: Source

    @property
    def ref(self) -> str:
        return f"{self.placement}.{self.name}"


@dataclass
class WorldConnector:
    placement: str
    part: str
    conn: Connector
    at: tuple[float, float, float]
    face_normal: tuple[float, float, float]
    corridor_poly: Polygon
    corridor_z: Vec2

    @property
    def ref(self) -> str:
        return f"{self.placement}.{self.conn.name}"


@dataclass
class Issue:
    level: str            # "error" | "warning" | "info"
    code: str
    message: str
    refs: list[str] = field(default_factory=list)


@dataclass
class Resolved:
    scene: Scene
    frames: dict[str, Frame]
    solids: list[Solid]
    connectors: list[WorldConnector]
    parents: dict[str, Optional[str]]
    issues: list[Issue] = field(default_factory=list)

    def bodies(self) -> list[Solid]:
        return [s for s in self.solids if s.kind is VolumeKind.body]

    def bounds(self, kinds: Iterable[VolumeKind] | None = None) -> tuple[float, float, float, float, float, float]:
        keep = set(kinds) if kinds else None
        sel = [s for s in self.solids if keep is None or s.kind in keep]
        if not sel:
            return (0, 0, 0, 0, 0, 0)
        xs0, ys0, xs1, ys1, zs0, zs1 = [], [], [], [], [], []
        for s in sel:
            x0, y0, x1, y1 = s.poly.bounds
            xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
            zs0.append(s.z[0]); zs1.append(s.z[1])
        return (min(xs0), min(ys0), min(zs0), max(xs1), max(ys1), max(zs1))


# --------------------------------------------------------------------------
# resolving
# --------------------------------------------------------------------------

def _order(placements: list[Placement]) -> list[Placement]:
    """Parents before children; cycles raise."""
    by_id = {p.id: p for p in placements}
    out: list[Placement] = []
    seen: set[str] = set()
    stack: set[str] = set()

    def visit(p: Placement) -> None:
        if p.id in seen:
            return
        if p.id in stack:
            raise ValueError(f"attachment cycle at {p.id!r}")
        stack.add(p.id)
        if p.parent:
            if p.parent not in by_id:
                raise ValueError(f"{p.id!r} attached to unknown placement {p.parent!r}")
            visit(by_id[p.parent])
        stack.discard(p.id)
        seen.add(p.id)
        out.append(p)

    for p in placements:
        visit(p)
    return out


def _solve_mate(placement: Placement, part: Part, parent_part: Part,
                parent_frame: Frame) -> tuple[Frame, list[Issue]]:
    issues: list[Issue] = []
    pm = next((m for m in parent_part.mates if m.name == placement.parent_mate), None)
    cm = next((m for m in part.mates if m.name == placement.mate), None)
    if pm is None or cm is None:
        raise ValueError(
            f"{placement.id!r}: mate {placement.parent_mate!r}/{placement.mate!r} not found"
        )
    if pm.standard != cm.standard:
        issues.append(Issue("error", "mate_mismatch",
                            f"{placement.id}: {cm.standard} cannot mate with {pm.standard}",
                            [placement.id]))
    if pm.role == cm.role:
        issues.append(Issue("error", "mate_role",
                            f"{placement.id}: two {pm.role}s cannot mate", [placement.id]))

    n_parent = parent_frame.direction(pm.face.normal)
    if abs(n_parent[2]) < 0.999:
        issues.append(Issue("warning", "mate_unsupported",
                            f"{placement.id}: only +Z/-Z mates are solved exactly; "
                            f"{pm.name} points sideways and was approximated",
                            [placement.id]))

    # The child's mate must face back at the parent's.
    need_nz = -n_parent[2]
    flip = (cm.face.normal[2] * (1.0) ) * need_nz < 0
    rot_z = parent_frame.rot_z + placement.rot_z

    target = parent_frame.point(pm.at)
    target = (target[0] + n_parent[0] * placement.mate_gap,
              target[1] + n_parent[1] * placement.mate_gap,
              target[2] + n_parent[2] * placement.mate_gap)

    probe = Frame(pos=(0.0, 0.0, 0.0), rot_z=rot_z, flip=flip)
    off = probe.point(cm.at)
    frame = Frame(pos=(target[0] - off[0], target[1] - off[1], target[2] - off[2]),
                  rot_z=rot_z, flip=flip)
    return frame, issues


def resolve(scene: Scene, lib: PartLibrary) -> Resolved:
    frames: dict[str, Frame] = {}
    parents: dict[str, Optional[str]] = {}
    issues: list[Issue] = []
    solids: list[Solid] = []
    connectors: list[WorldConnector] = []

    ordered = _order(list(scene.placements))
    by_id = {p.id: p for p in scene.placements}

    for pl in ordered:
        part = lib[pl.part]
        parents[pl.id] = pl.parent
        if pl.parent:
            parent_pl = by_id[pl.parent]
            frame, mate_issues = _solve_mate(pl, part, lib[parent_pl.part], frames[pl.parent])
            issues += mate_issues
        else:
            frame = Frame(pos=pl.pos, rot_z=pl.rot_z, flip=pl.flip)
        frames[pl.id] = frame

    # anchor: shift everything so the anchor placement sits at the origin
    if scene.anchor and scene.anchor in frames:
        a = frames[scene.anchor].pos
        frames = {k: Frame(pos=(f.pos[0] - a[0], f.pos[1] - a[1], f.pos[2] - a[2]),
                           rot_z=f.rot_z, flip=f.flip)
                  for k, f in frames.items()}

    for pl in ordered:
        part = lib[pl.part]
        frame = frames[pl.id]

        # the PCB itself is a body too
        pcb = outline_polygon(part.outline)
        if part.pcb_thickness > 0:
            solids.append(Solid(pl.id, part.id, "pcb", VolumeKind.body,
                                frame.polygon(pcb),
                                frame.z_interval((0.0, part.pcb_thickness)), part.src))

        for v in part.volumes:
            solids.append(Solid(pl.id, part.id, v.name, v.kind,
                                frame.polygon(box_polygon(v)),
                                frame.z_interval((v.z_min(), v.z_max())), v.src))

        for c in part.connectors:
            if c.body is not None:
                solids.append(Solid(pl.id, part.id, c.body.name, VolumeKind.body,
                                    frame.polygon(box_polygon(c.body)),
                                    frame.z_interval((c.body.z_min(), c.body.z_max())),
                                    c.src))
            # An external connector needs the plug *and* room for the cable to
            # turn away from the wall. An internal one only needs the plug body:
            # the cable can curve off in any direction inside the box.
            reach = c.plug_depth + (c.bend_radius if c.external else 0.0)
            width = max(c.cutout[0] if c.cutout else 10.0, 8.0)
            height = max(c.cutout[1] if c.cutout else 10.0, 8.0)
            poly, zi = corridor(c.at, c.face, reach, width, height)
            connectors.append(WorldConnector(
                pl.id, part.id, c, frame.point(c.at), frame.direction(c.face.normal),
                frame.polygon(poly), frame.z_interval(zi)))

    return Resolved(scene=scene, frames=frames, solids=solids,
                    connectors=connectors, parents=parents, issues=issues)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _related(res: Resolved, a: str, b: str) -> bool:
    return res.parents.get(a) == b or res.parents.get(b) == a


def check(res: Resolved, lib: PartLibrary) -> list[Issue]:
    """Everything that could make this layout not work in the real world."""
    issues = list(res.issues)
    case = res.scene.case
    bodies = res.bodies()

    # 1. hard collisions between unrelated placements
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            if a.placement == b.placement or _related(res, a.placement, b.placement):
                continue
            if z_overlap(a.z, b.z) <= 0:
                continue
            inter = a.poly.intersection(b.poly)
            if inter.area > 0.5:
                issues.append(Issue(
                    "error", "collision",
                    f"{a.ref} and {b.ref} overlap by {inter.area:.1f} mm^2 "
                    f"over {z_overlap(a.z, b.z):.1f} mm of height",
                    [a.ref, b.ref]))

    # 2. wiring room: unrelated parts closer than cable_clearance
    gap = case.cable_clearance
    if gap > 0:
        seen: set[tuple[str, str]] = set()
        for i, a in enumerate(bodies):
            for b in bodies[i + 1:]:
                if a.placement == b.placement or _related(res, a.placement, b.placement):
                    continue
                key = tuple(sorted((a.placement, b.placement)))
                if key in seen or z_overlap(a.z, b.z) <= 0:
                    continue
                d = a.poly.distance(b.poly)
                if 0 < d < gap:
                    seen.add(key)
                    issues.append(Issue(
                        "warning", "tight",
                        f"{a.placement} and {b.placement} are {d:.1f} mm apart; "
                        f"{gap:.1f} mm wanted for cable runs",
                        [a.ref, b.ref]))

    # 3. connector access -- one issue per (connector, offending placement)
    blocked: set[tuple[str, str]] = set()
    for wc in res.connectors:
        reach = wc.conn.plug_depth + (wc.conn.bend_radius if wc.conn.external else 0.0)
        for s in bodies:
            if s.placement == wc.placement or _related(res, s.placement, wc.placement):
                continue
            key = (wc.ref, s.placement)
            if key in blocked or z_overlap(wc.corridor_z, s.z) <= 0:
                continue
            if wc.corridor_poly.intersection(s.poly).area > 1.0:
                blocked.add(key)
                issues.append(Issue(
                    "error" if wc.conn.external else "warning", "connector_blocked",
                    f"{wc.ref} ({wc.conn.type}) needs {reach:.0f} mm of clear run "
                    f"but {s.placement} is in the way",
                    [wc.ref, s.ref]))

    # 4. things the user must see or touch must not be buried
    for s in res.solids:
        if s.kind not in (VolumeKind.display, VolumeKind.actuator):
            continue
        for o in bodies:
            if o.placement == s.placement or _related(res, o.placement, s.placement):
                continue
            if o.z[0] < s.z[1] - 0.5:
                continue          # not above it
            if o.poly.intersection(s.poly).area > 1.0:
                issues.append(Issue(
                    "error", "obstructed",
                    f"{s.ref} ({s.kind.value}) is covered by {o.ref}",
                    [s.ref, o.ref]))

    # 5. how much of this design rests on numbers we have not verified
    unverified: dict[str, list[str]] = {}
    for pl in res.scene.placements:
        part = lib[pl.part]
        if part.min_confidence().rank <= Confidence.community.rank:
            unverified.setdefault(part.id, []).append(pl.id)
    for part_id, refs in unverified.items():
        conf = lib[part_id].min_confidence()
        issues.append(Issue(
            "warning", "unverified",
            f"{part_id} carries {conf.value} dimensions ({', '.join(refs)}) -- "
            f"measure before cutting",
            refs))
    return issues


def footprint(res: Resolved, kinds: Iterable[VolumeKind] | None = None) -> Polygon:
    keep = set(kinds) if kinds else None
    polys = [s.poly for s in res.solids if keep is None or s.kind in keep]
    return unary_union(polys) if polys else Polygon()
