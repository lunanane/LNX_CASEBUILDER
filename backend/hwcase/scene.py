"""Resolving a scene into world geometry, and checking it.

The solver is deliberately small: placements are either free (an explicit world
pose) or snapped to a parent through a mate. Mates along +Z/-Z -- board stacking,
which is what a HAT, a keypad pad or a Eurorack power header actually is -- are
solved exactly. Anything else is reported rather than silently approximated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .geom import Frame, box_polygon, corridor, outline_polygon, z_overlap
from .library import PartLibrary
from .schema import (Box, Confidence, Connector, CutoutPolicy, Face, Panel,
                     Part, Placement, Scene, SidePolicy, Source, Vec2,
                     VolumeKind)

#: how far a slot or an open side reaches outwards before the case outline
#: clips it. Bigger than any case we would ever cut.
REACH = 1000.0


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
    #: what the case does about this connector, from its side's policy
    policy: CutoutPolicy = CutoutPolicy.per_connector
    #: False when the side's `include` list leaves it out, i.e. the user said
    #: they will never plug into it and the case need not make room
    included: bool = True

    @property
    def ref(self) -> str:
        return f"{self.placement}.{self.conn.name}"

    @property
    def cuts_the_wall(self) -> bool:
        return self.included and self.policy in (
            CutoutPolicy.per_connector, CutoutPolicy.open_to_edge)


@dataclass
class SideOpening:
    """A whole side left open, from the floor up to just above the cable."""

    placement: str
    side: Face
    poly: Polygon          # world XY, already extended past any case outline
    z: Vec2                # world z interval that gets removed
    reason: str

    @property
    def ref(self) -> str:
        return f"{self.placement}.{self.side.value}"


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
    side_openings: list[SideOpening]
    parents: dict[str, Optional[str]]
    panels: dict[str, float] = field(default_factory=dict)
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


def _subtree(placements: list[Placement], root: str) -> list[str]:
    """`root` plus everything mated onto it, directly or transitively."""
    out = [root]
    grew = True
    while grew:
        grew = False
        for p in placements:
            if p.parent in out and p.id not in out:
                out.append(p.id)
                grew = True
    return out


def _volume_top(part: Part, frame: Frame, name: str) -> Optional[float]:
    for v in part.volumes:
        if v.name == name:
            return frame.z_interval((v.z_min(), v.z_max()))[1]
    return None


def _part_top(part: Part, frame: Frame, kinds: Optional[set] = None) -> Optional[float]:
    tops = []
    for v in part.volumes:
        if kinds is None or v.kind in kinds:
            tops.append(frame.z_interval((v.z_min(), v.z_max()))[1])
    if kinds is None:
        tops.append(frame.z_interval((0.0, part.pcb_thickness))[1])
    return max(tops) if tops else None


def _resolve_panels(scene: Scene, lib: PartLibrary, frames: dict[str, Frame],
                    by_id: dict[str, Placement]) -> tuple[dict[str, float], list[Issue]]:
    panels: dict[str, float] = {}
    issues: list[Issue] = []
    for panel in scene.panels:
        if panel.from_ref:
            ref, _, vol = panel.from_ref.partition(".")
            pl = by_id.get(ref)
            if pl is None or ref not in frames:
                issues.append(Issue("error", "panel_ref",
                                    f"panel {panel.name}: no placement {ref!r}", [panel.name]))
                continue
            if pl.on_panel:
                issues.append(Issue("error", "panel_cycle",
                                    f"panel {panel.name} is defined by {ref}, which is itself "
                                    f"fitted to a panel", [panel.name, ref]))
                continue
            top = _volume_top(lib[pl.part], frames[ref], vol) if vol else None
            if top is None:
                top = _part_top(lib[pl.part], frames[ref])
                issues.append(Issue("warning", "panel_ref",
                                    f"panel {panel.name}: {pl.part} has no volume {vol!r}, "
                                    f"used the top of the whole part instead", [panel.name]))
            panels[panel.name] = top
        elif panel.z is not None:
            panels[panel.name] = panel.z
        else:
            issues.append(Issue("error", "panel_ref",
                                f"panel {panel.name} has neither z nor from_ref", [panel.name]))
    return panels, issues


def _fit_to_panels(scene: Scene, lib: PartLibrary, frames: dict[str, Frame],
                   panels: dict[str, float], by_id: dict[str, Placement],
                   ) -> list[Issue]:
    """Slide each `on_panel` placement (and whatever is mated to it) up or down
    so its reference feature lands on the panel. Mates are pure z stacks, so
    translating the whole subtree keeps every one of them valid."""
    issues: list[Issue] = []
    for pl in scene.placements:
        if not pl.on_panel:
            continue
        if pl.parent:
            issues.append(Issue("warning", "panel_ignored",
                                f"{pl.id} is mated to {pl.parent}, so its height comes from "
                                f"the mate; on_panel was ignored", [pl.id]))
            continue
        if pl.on_panel not in panels:
            issues.append(Issue("error", "panel_missing",
                                f"{pl.id}: no panel called {pl.on_panel!r}", [pl.id]))
            continue

        ids = _subtree(scene.placements, pl.id)
        ref_top: Optional[float] = None
        if pl.panel_ref not in ("auto", "top"):
            for pid in ids:
                ref_top = _volume_top(lib[by_id[pid].part], frames[pid], pl.panel_ref)
                if ref_top is not None:
                    break
            if ref_top is None:
                issues.append(Issue("warning", "panel_ref",
                                    f"{pl.id}: no volume {pl.panel_ref!r} in it or anything "
                                    f"mated to it; used the highest point instead", [pl.id]))
        if ref_top is None:
            kinds = {VolumeKind.actuator, VolumeKind.display} \
                if pl.panel_ref == "auto" else None
            tops = [t for pid in ids
                    if (t := _part_top(lib[by_id[pid].part], frames[pid], kinds)) is not None]
            if not tops and kinds is not None:
                tops = [t for pid in ids
                        if (t := _part_top(lib[by_id[pid].part], frames[pid], None)) is not None]
            if not tops:
                issues.append(Issue("error", "panel_ref",
                                    f"{pl.id}: nothing to reference against", [pl.id]))
                continue
            ref_top = max(tops)

        dz = panels[pl.on_panel] + pl.panel_offset - ref_top
        for pid in ids:
            f = frames[pid]
            frames[pid] = Frame(pos=(f.pos[0], f.pos[1], f.pos[2] + dz),
                                rot_z=f.rot_z, flip=f.flip)
    return issues


def _side_region(part: Part, frame: Frame, side: Face, span: str) -> Polygon:
    """Everything beyond one edge of a board, in world XY.

    `span: board` keeps the opening as wide as the board, so the hardware next
    door keeps its floor; `full` opens the case right across, which is what you
    want when the whole end of the machine should be open underneath.
    """
    x0, y0, x1, y1 = outline_polygon(part.outline).bounds
    wide = REACH if span == "full" else 0.0
    if side is Face.px:
        local = box(x1, y0 - wide, x1 + REACH, y1 + wide)
    elif side is Face.nx:
        local = box(x0 - REACH, y0 - wide, x0, y1 + wide)
    elif side is Face.py:
        local = box(x0 - wide, y1, x1 + wide, y1 + REACH)
    elif side is Face.ny:
        local = box(x0 - wide, y0 - REACH, x1 + wide, y0)
    else:
        return Polygon()          # +z / -z are the panel and the floor
    return frame.polygon(local)


def resolve(scene: Scene, lib: PartLibrary) -> Resolved:
    frames: dict[str, Frame] = {}
    parents: dict[str, Optional[str]] = {}
    issues: list[Issue] = []
    solids: list[Solid] = []
    connectors: list[WorldConnector] = []
    side_openings: list[SideOpening] = []

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

    # panels are world planes, so they are solved after the anchor shift, and
    # the placements that ride on them are slid into place after that
    panels, panel_issues = _resolve_panels(scene, lib, frames, by_id)
    issues += panel_issues
    issues += _fit_to_panels(scene, lib, frames, panels, by_id)

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
            # a repeat grid expands here: sixteen buttons, four shafts
            for inst_name, inst_at in v.instances():
                solids.append(Solid(pl.id, part.id, inst_name, v.kind,
                                    frame.polygon(box_polygon(v, inst_at)),
                                    frame.z_interval((v.z_min(), v.z_max())), v.src))

        by_side: dict[Face, SidePolicy] = {sp.side: sp for sp in pl.sides}

        for c in part.connectors:
            policy_for = by_side.get(c.face)
            policy = policy_for.cutout if policy_for else CutoutPolicy.per_connector
            # By default the case makes room for the ports that face the
            # outside world and ignores the internal wiring. Naming an
            # `include` list overrides that completely, in both directions:
            # drop a port you will never use, or add an internal one you want
            # to be able to reach.
            included = c.external
            if policy_for is not None and policy_for.include is not None:
                included = c.name in policy_for.include

            if c.body is not None:
                solids.append(Solid(pl.id, part.id, c.body.name, VolumeKind.body,
                                    frame.polygon(box_polygon(c.body)),
                                    frame.z_interval((c.body.z_min(), c.body.z_max())),
                                    c.src))
            # An external connector needs the plug *and* room for the cable to
            # turn away from the wall. An internal one only needs the plug body:
            # the cable can curve off in any direction inside the box.
            reach = c.plug_depth + (c.bend_radius if c.external else 0.0)
            if policy is CutoutPolicy.open_to_edge:
                reach = REACH          # run the slot out through the wall
            width = max(c.cutout[0] if c.cutout else 10.0, 8.0)
            height = max(c.cutout[1] if c.cutout else 10.0, 8.0)
            body_z = (c.body.z_min(), c.body.z_max()) if c.body is not None else None
            poly, zi = corridor(c.at, c.face, reach, width, height, body_z)
            connectors.append(WorldConnector(
                pl.id, part.id, c, frame.point(c.at), frame.direction(c.face.normal),
                frame.polygon(poly), frame.z_interval(zi), policy, included))

        # a side asked to be left open: work out how high the hole has to go
        for sp in pl.sides:
            if sp.cutout is not CutoutPolicy.open_side:
                continue
            tops = [max(c.corridor_z) for c in connectors
                    if c.placement == pl.id and c.conn.face is sp.side and c.included]
            if not tops:
                issues.append(Issue(
                    "warning", "open_side_empty",
                    f"{pl.id}: side {sp.side.value} is set to open_side but has no "
                    f"connectors to clear -- nothing was cut",
                    [pl.id]))
                continue
            region = _side_region(part, frame, sp.side, sp.span)
            if region.is_empty:
                issues.append(Issue(
                    "warning", "open_side_face",
                    f"{pl.id}: open_side only applies to the four upright sides, "
                    f"not {sp.side.value}",
                    [pl.id]))
                continue
            ceiling = max(tops) + sp.headroom
            side_openings.append(SideOpening(
                pl.id, sp.side, region, (-1e6, ceiling),
                f"open side for {', '.join(sorted(c.conn.name for c in connectors if c.placement == pl.id and c.conn.face is sp.side and c.included))}"))

    return Resolved(scene=scene, frames=frames, solids=solids,
                    connectors=connectors, side_openings=side_openings,
                    parents=parents, panels=panels, issues=issues)


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

    # 2b. boards that pass over each other: allowed, but say how much room
    # there actually is. A flat board sliding under another one is exactly what
    # you want -- right up until the gap is 0.3 mm and you cannot get it in.
    seen_gap: set[tuple[str, str]] = set()
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            if a.placement == b.placement or _related(res, a.placement, b.placement):
                continue
            key = tuple(sorted((a.placement, b.placement)))
            if key in seen_gap:
                continue
            if z_overlap(a.z, b.z) > 0:
                continue                      # that is a collision, handled above
            if a.poly.intersection(b.poly).area <= 1.0:
                continue                      # they do not pass over each other
            gap = max(a.z[0], b.z[0]) - min(a.z[1], b.z[1])
            if gap < case.overlap_clearance:
                seen_gap.add(key)
                lower, upper = (a, b) if a.z[1] <= b.z[0] else (b, a)
                issues.append(Issue(
                    "warning", "tight_overlap",
                    f"{upper.ref} passes {gap:.1f} mm over {lower.ref} -- "
                    f"{case.overlap_clearance:.1f} mm wanted to get it in",
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

    # 5a. a board told to hide under the faceplate that does not actually fit
    if res.panels:
        thickness = (case.materials[-1].thickness if case.materials else 3.0)
        for pl in res.scene.placements:
            if not pl.under_panel:
                continue
            panel_z = res.panels.get(pl.on_panel) if pl.on_panel else (
                max(res.panels.values()) if res.panels else None)
            if panel_z is None:
                continue
            tops = [s.z[1] for s in res.solids if s.placement == pl.id]
            if tops and max(tops) > panel_z - thickness + 1e-6:
                issues.append(Issue(
                    "error", "under_panel_collision",
                    f"{pl.id} is set to sit under the faceplate but reaches "
                    f"{max(tops):.1f} mm, and the underside of the plate is at "
                    f"{panel_z - thickness:.1f} mm",
                    [pl.id]))

    # 5b. controls that ended up below the surface you press them through
    if res.panels:
        on_panel = {pl.id: pl.on_panel for pl in res.scene.placements if pl.on_panel}
        subtrees = {root: set(_subtree(res.scene.placements, root)) for root in on_panel}
        for s in res.solids:
            if s.kind is not VolumeKind.actuator:
                continue
            for root, panel_name in on_panel.items():
                if s.placement not in subtrees[root] or panel_name not in res.panels:
                    continue
                z = res.panels[panel_name]
                if s.z[1] < z - 0.5:
                    issues.append(Issue(
                        "warning", "recessed",
                        f"{s.ref} tops out {z - s.z[1]:.1f} mm below panel "
                        f"'{panel_name}' -- you could not press it",
                        [s.ref]))
                break

    # 6. how much of this design rests on numbers we have not verified
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
