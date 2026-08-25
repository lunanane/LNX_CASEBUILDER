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
from .schema import (Box, Confidence, Connector, CutoutPolicy, Face, Mount,
                     Panel, Part, Placement, Scene, SidePolicy, Source,
                     Support, Vec2, VolumeKind)

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
        # `channel` and `open_side` are cut as regions instead, so the corridor
        # here stays what it should be: the room a plug and its cable need,
        # which the access check still measures.
        return self.included and self.policy in (
            CutoutPolicy.per_connector, CutoutPolicy.open_to_edge)


def _wants_inset(pl) -> bool:
    """Whether this board's screw heads sink into the outer plate.

    Unset means "whichever is right for this end of the case": flush on the
    bottom so it does not rock, proud on the faceplate so the head has
    something to pull against.
    """
    if pl.screw_inset is not None:
        return bool(pl.screw_inset)
    return pl.support == Support.from_floor


@dataclass
class SupportPoint:
    """One mounting hole the case is asked to carry, in world coordinates.

    The z extent is left to the case builder: how far a boss has to reach
    depends on where the floor and the lid end up, and that is not known until
    the sheet stack is laid out.
    """

    placement: str
    hole: str
    at: Vec2               # world XY
    mode: Support
    screw_d: float         # the hole in the board
    board_bottom: float    # world z of the board's underside
    board_top: float       # world z of its top face
    screw: Optional[str] = None
    #: counterbore the outer plate for the head -- see Placement.screw_inset
    inset: bool = False
    #: the user's own words, un-defaulted: None = "whatever is right", False
    #: is an explicit request for the head on the outer plate -- which is the
    #: opposite of a screw well, so wells respect it and defaults do not
    inset_explicit: Optional[bool] = None

    @property
    def ref(self) -> str:
        return f"{self.placement}.{self.hole}"


@dataclass
class WallTarget:
    """Where one side of the case wall should sit, in world coordinates.

    The auto outline is the bounding box of the hardware plus a single `wall`.
    That is measured from the board *edge*, so a socket recessed 20 mm inside
    its board ends up 20 mm + wall from the outside and no plug reaches it.
    A margin says instead: put the outer surface exactly this far from the
    socket mouth.
    """

    placement: str
    side: Face
    axis: int              # 0 = x, 1 = y
    sign: int              # +1 = the max edge, -1 = the min edge
    value: float           # world coordinate for the OUTER surface
    reason: str


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
    wall_targets: list[WallTarget]
    supports: list[SupportPoint]
    parents: dict[str, Optional[str]]
    panels: dict[str, float] = field(default_factory=dict)
    floor: Optional[float] = None
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

    probe = Frame(pos=(0.0, 0.0, 0.0), rot_z=rot_z, tilt=180 if flip else 0)
    off = probe.point(cm.at)
    frame = Frame(pos=(target[0] - off[0], target[1] - off[1], target[2] - off[2]),
                  rot_z=rot_z, tilt=180 if flip else 0)
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
            return frame.place(box_polygon(v), (v.z_min(), v.z_max()))[1][1]
    return None


def _part_top(part: Part, frame: Frame, kinds: Optional[set] = None) -> Optional[float]:
    tops = []
    for v in part.volumes:
        if kinds is None or v.kind in kinds:
            tops.append(frame.place(box_polygon(v), (v.z_min(), v.z_max()))[1][1])
    if kinds is None:
        tops.append(frame.place(outline_polygon(part.outline),
                                (0.0, part.pcb_thickness))[1][1])
    return max(tops) if tops else None


def has_top_periphery(part: Part) -> bool:
    """Does anything on this board want to reach the faceplate?

    A screen, a knob, a button, or a connector you plug into from above. If
    none of that is present the board is internal and belongs on the floor.
    """
    if any(v.kind in (VolumeKind.display, VolumeKind.actuator) for v in part.volumes):
        return True
    return any(c.face is Face.pz for c in part.connectors)


def effective_mount(pl: Placement, part: Part,
                    subtree: Optional[list[Part]] = None) -> Mount:
    """What `auto` actually resolves to for this placement.

    `subtree` is the part plus anything mated on top of it. A NeoTrellis has
    nothing facing up -- its buttons belong to the silicone pad glued to it --
    so judging the board alone would send the whole keypad to the floor.
    """
    if pl.parent:
        return Mount.manual                 # a mated board's height is the mate's
    if pl.locked:
        return Mount.manual                 # locked means locked
    if pl.mount is not Mount.auto:
        return pl.mount
    if pl.on_panel:
        return Mount.panel
    parts = subtree if subtree is not None else [part]
    return Mount.panel if any(has_top_periphery(p) for p in parts) else Mount.floor


def _subtree_parts(scene: Scene, lib: PartLibrary, root: str) -> list[Part]:
    return [lib[p.part] for p in scene.placements
            if p.id in _subtree(scene.placements, root)]


def mount_of(scene: Scene, lib: PartLibrary, pl: Placement) -> Mount:
    """`effective_mount` with the mate stack taken into account."""
    return effective_mount(pl, lib[pl.part], _subtree_parts(scene, lib, pl.id))


def _part_bottom(part: Part, frame: Frame) -> float:
    zs = [frame.place(outline_polygon(part.outline), (0.0, part.pcb_thickness))[1][0]]
    for v in part.volumes:
        zs.append(frame.place(box_polygon(v), (v.z_min(), v.z_max()))[1][0])
    for c in part.connectors:
        if c.body is not None:
            zs.append(frame.place(box_polygon(c.body),
                                  (c.body.z_min(), c.body.z_max()))[1][0])
    return min(zs)


def _fit_to_floor(scene: Scene, lib: PartLibrary, frames: dict[str, Frame],
                  by_id: dict[str, Placement]) -> tuple[Optional[float], list[Issue]]:
    """Drop the internal boards onto the inside floor.

    The datum is whatever the deepest non-floor board reaches, so the floor
    lands under the hardware rather than under an arbitrary origin.
    """
    issues: list[Issue] = []
    resting = [pl for pl in scene.placements
               if not pl.parent and mount_of(scene, lib, pl) is Mount.floor]
    if not resting:
        return scene.floor, issues

    if scene.floor is not None:
        datum = scene.floor
    else:
        others = [_part_bottom(lib[pl.part], frames[pl.id])
                  for pl in scene.placements
                  if pl.id in frames and pl not in resting]
        datum = min(others) if others else 0.0

    for pl in resting:
        ids = _subtree(scene.placements, pl.id)
        bottom = min(_part_bottom(lib[by_id[pid].part], frames[pid]) for pid in ids)
        dz = datum - bottom
        for pid in ids:
            f = frames[pid]
            frames[pid] = Frame(pos=(f.pos[0], f.pos[1], f.pos[2] + dz),
                                rot_z=f.rot_z, tilt=f.tilt)
    return datum, issues


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
            if mount_of(scene, lib, pl) is Mount.panel:
                issues.append(Issue(
                    "error", "panel_cycle",
                    f"panel {panel.name} is defined by {ref}, but {ref} is itself "
                    f"fitted to a panel -- its height cannot be both the cause "
                    f"and the effect. Set {ref}'s height to manual.",
                    [panel.name, ref]))
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
    default_panel = next(iter(panels), None)
    for pl in scene.placements:
        if mount_of(scene, lib, pl) is not Mount.panel:
            continue
        if not pl.on_panel:
            if default_panel is None:
                continue                    # no panels in this scene
            pl = pl.model_copy(update={"on_panel": default_panel})
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
                                rot_z=f.rot_z, tilt=f.tilt)
    return issues


def _side_region(part: Part, frame: Frame, side: Face,
                 lo: float, hi: float) -> Polygon:
    """The slot beyond one edge of a board, between `lo` and `hi` along it.

    `lo`/`hi` are part-local coordinates on the axis that runs ALONG the edge:
    y for the left and right sides, x for front and back. Limiting it to the
    ports means the rest of the case keeps its bottom.
    """
    x0, y0, x1, y1 = outline_polygon(part.outline).bounds
    if side is Face.px:
        local = box(x1, lo, x1 + REACH, hi)
    elif side is Face.nx:
        local = box(x0 - REACH, lo, x0, hi)
    elif side is Face.py:
        local = box(lo, y1, hi, y1 + REACH)
    elif side is Face.ny:
        local = box(lo, y0 - REACH, hi, y0)
    else:
        return Polygon()          # +z / -z are the panel and the floor
    return frame.polygon(local)


def _along_axis(side: Face) -> int:
    """Which local axis runs along an edge: 0 = x, 1 = y."""
    return 1 if side in (Face.px, Face.nx) else 0


def _span_bounds(part: Part, side: Face, span: str, centres: list[float],
                 widths: list[float], clearance: float) -> tuple[float, float]:
    x0, y0, x1, y1 = outline_polygon(part.outline).bounds
    if span == "full":
        return -REACH, REACH
    if span == "board":
        return (y0, y1) if _along_axis(side) == 1 else (x0, x1)
    lo = min(c - w / 2.0 for c, w in zip(centres, widths)) - clearance
    hi = max(c + w / 2.0 for c, w in zip(centres, widths)) + clearance
    return lo, hi


def resolve(scene: Scene, lib: PartLibrary) -> Resolved:
    res_clearance = scene.case.part_clearance
    frames: dict[str, Frame] = {}
    parents: dict[str, Optional[str]] = {}
    issues: list[Issue] = []
    solids: list[Solid] = []
    connectors: list[WorldConnector] = []
    side_openings: list[SideOpening] = []
    wall_targets: list[WallTarget] = []
    supports: list[SupportPoint] = []

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
            frame = Frame(pos=pl.pos, rot_z=pl.rot_z, tilt=pl.effective_tilt)
        frames[pl.id] = frame

    # anchor: shift everything so the anchor placement sits at the origin
    if scene.anchor and scene.anchor in frames:
        a = frames[scene.anchor].pos
        frames = {k: Frame(pos=(f.pos[0] - a[0], f.pos[1] - a[1], f.pos[2] - a[2]),
                           rot_z=f.rot_z, tilt=f.tilt)
                  for k, f in frames.items()}

    # panels are world planes, so they are solved after the anchor shift, and
    # the placements that ride on them are slid into place after that
    panels, panel_issues = _resolve_panels(scene, lib, frames, by_id)
    issues += panel_issues
    issues += _fit_to_panels(scene, lib, frames, panels, by_id)
    floor_z, floor_issues = _fit_to_floor(scene, lib, frames, by_id)
    issues += floor_issues

    for pl in ordered:
        part = lib[pl.part]
        frame = frames[pl.id]

        # the PCB itself is a body too
        pcb = outline_polygon(part.outline)
        if part.pcb_thickness > 0:
            poly, zi = frame.place(pcb, (0.0, part.pcb_thickness))
            solids.append(Solid(pl.id, part.id, "pcb", VolumeKind.body,
                                poly, zi, part.src))

        for v in part.volumes:
            # a repeat grid expands here: sixteen buttons, four shafts
            for inst_name, inst_at in v.instances():
                poly, zi = frame.place(box_polygon(v, inst_at), (v.z_min(), v.z_max()))
                solids.append(Solid(pl.id, part.id, inst_name, v.kind, poly, zi, v.src))

        # The space between a mated child and its parent is unbuildable: the
        # two boards are plugged together BEFORE the unit goes into the case,
        # so no sheet can ever be slid between them -- or under the child's
        # overhang inside the shared footprint, since the unit is lowered in
        # as one piece. Modelled as a keepout over the union of both
        # outlines, it cuts pockets like any solid and keeps tie ribs out.
        # A flush mate (the silicone pad glued straight onto the trellis)
        # has no gap and gets none.
        if pl.parent:
            parent_pl = by_id[pl.parent]
            parent_part = lib[parent_pl.part]
            p_poly, p_z = frames[pl.parent].place(
                outline_polygon(parent_part.outline),
                (0.0, parent_part.pcb_thickness))
            c_poly, c_z = frame.place(outline_polygon(part.outline),
                                      (0.0, part.pcb_thickness))
            if c_z[0] >= p_z[1] - 1e-6:
                gap = (p_z[1], c_z[0])
            elif p_z[0] >= c_z[1] - 1e-6:
                gap = (c_z[1], p_z[0])
            else:
                gap = None
            if gap and gap[1] - gap[0] > 0.5:
                solids.append(Solid(
                    pl.id, part.id, "mate_space", VolumeKind.keepout,
                    unary_union([p_poly, c_poly]), gap,
                    Source(confidence=Confidence.measured,
                           note="derived: nothing can be assembled between "
                                "mated boards")))

        if pl.support != Support.none:
            if not part.holes:
                issues.append(Issue(
                    "warning", "no_mounting_holes",
                    f"{pl.id}: asked to be supported by its mounting holes, but "
                    f"{part.id} has none in the library",
                    [pl.id]))
            elif frame.tilt % 180 != 0:
                issues.append(Issue(
                    "warning", "support_on_edge",
                    f"{pl.id}: its holes face sideways once the board is on edge, "
                    f"so the case cannot post up to them",
                    [pl.id]))
            else:
                # The PCB's own faces, not the part's extremes. A screw seats on
                # the board; the encoder shafts that stand 8 mm proud of the
                # faceplate are not somewhere a boss can reach, and taking them
                # as the top meant `from_lid` found no layer at all to drill.
                bottom, top = frame.z_interval((0.0, part.pcb_thickness))
                for h in part.holes:
                    at = frame.point((h.at[0], h.at[1], 0.0))
                    supports.append(SupportPoint(
                        pl.id, h.name, (at[0], at[1]), pl.support,
                        h.diameter, bottom, top, h.screw,
                        inset=_wants_inset(pl),
                        inset_explicit=pl.screw_inset))

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
                poly, zi = frame.place(box_polygon(c.body),
                                       (c.body.z_min(), c.body.z_max()))
                solids.append(Solid(pl.id, part.id, c.body.name, VolumeKind.body,
                                    poly, zi, c.src))
            # An external connector needs the plug *and* room for the cable to
            # turn away from the wall. An internal one only needs the plug body:
            # the cable can curve off in any direction inside the box.
            reach = c.plug_depth + (c.bend_radius if c.external else 0.0)
            if policy is CutoutPolicy.open_to_edge:
                reach = REACH          # run the slot out through the wall
            # An explicit cutout is a measurement and must be honoured: a
            # 3.5 mm jack wants a 6.5 mm hole, and clamping it to 8 mm makes a
            # sloppy one. The floor only applies when the part did not say.
            if c.cutout:
                width, height = c.cutout
            else:
                width = height = 10.0
            if policy is CutoutPolicy.channel and policy_for is not None:
                # The groove is cut as a region; here we only warn when the
                # width asked for would be narrower than the port itself.
                if included and policy_for.channel_width < width - 1e-6:
                    issues.append(Issue(
                        "info", "channel_widened",
                        f"{pl.id}.{c.name}: the {policy_for.channel_width:.1f} mm "
                        f"channel was opened to {width:.1f} mm, which is what the "
                        f"port itself needs",
                        [f"{pl.id}.{c.name}"]))
            body_z = (c.body.z_min(), c.body.z_max()) if c.body is not None else None
            poly, zi = corridor(c.at, c.face, reach, width, height, body_z,
                                round_mouth=c.cutout_shape == "circle")
            wpoly, wzi = frame.place(poly, zi)
            connectors.append(WorldConnector(
                pl.id, part.id, c, frame.point(c.at), frame.direction(c.face.normal),
                wpoly, wzi, policy, included))

        # a side asked for the wall to sit a fixed distance from its ports
        for sp in pl.sides:
            if sp.margin is None:
                continue
            normal = frame.direction(sp.side.normal)
            axis = 0 if abs(normal[0]) > abs(normal[1]) else 1
            if abs(normal[2]) > 0.5 or abs(normal[axis]) < 0.999:
                issues.append(Issue(
                    "warning", "margin_skewed",
                    f"{pl.id}: side {sp.side.value} does not face along X or Y "
                    f"after rotation, so its wall margin was ignored",
                    [pl.id]))
                continue
            sign = 1 if normal[axis] > 0 else -1
            mouths = [c.at[axis] for c in connectors
                      if c.placement == pl.id and c.conn.face is sp.side and c.included]
            if not mouths:
                issues.append(Issue(
                    "warning", "margin_no_ports",
                    f"{pl.id}: side {sp.side.value} has a wall margin but no ports "
                    f"to measure it from",
                    [pl.id]))
                continue
            outermost = max(mouths) if sign > 0 else min(mouths)
            wall_targets.append(WallTarget(
                pl.id, sp.side, axis, sign, outermost + sign * sp.margin,
                f"{sp.margin:.1f} mm from {pl.id}'s {sp.side.value} ports"))

        # Sides that want real access: a groove per port, or one opening
        # across a whole bank of them. Both go all the way DOWN through the
        # underside, because sliding a plug in through a slot from the side
        # alone does not work in practice -- you drop it in from below and
        # push it home. Everything above the ports stays closed.
        for sp in pl.sides:
            if sp.cutout not in (CutoutPolicy.open_side, CutoutPolicy.channel):
                continue
            mine = [c for c in connectors
                    if c.placement == pl.id and c.conn.face is sp.side and c.included]
            if not mine:
                issues.append(Issue(
                    "warning", "open_side_empty",
                    f"{pl.id}: side {sp.side.value} is set to {sp.cutout.value} but "
                    f"has no selected ports -- nothing was cut",
                    [pl.id]))
                continue

            axis = _along_axis(sp.side)
            groups: list[tuple[list, float]]
            if sp.cutout is CutoutPolicy.channel:
                # one groove per port, each its own width
                groups = [([c], sp.channel_width) for c in mine]
            else:
                groups = [(mine, 0.0)]

            for group, forced in groups:
                centres = [c.conn.at[axis] for c in group]
                widths = []
                for c in group:
                    w = c.conn.cutout[0] if c.conn.cutout else 10.0
                    widths.append(max(w, forced) if forced else w)
                lo, hi = _span_bounds(part, sp.side, sp.span, centres, widths,
                                      res_clearance)
                region = _side_region(part, frame, sp.side, lo, hi)
                if region.is_empty:
                    issues.append(Issue(
                        "warning", "open_side_face",
                        f"{pl.id}: {sp.cutout.value} only applies to the four "
                        f"upright sides, not {sp.side.value}",
                        [pl.id]))
                    break

                # down to the underside, up to just above the highest port
                ceiling = max(max(c.corridor_z) for c in group) + sp.headroom
                names = ", ".join(sorted(c.conn.name for c in group))
                kind = "channel" if sp.cutout is CutoutPolicy.channel else "open side"
                side_openings.append(SideOpening(
                    pl.id, sp.side, region, (-1e6, ceiling),
                    f"{kind} for {names}"))

    return Resolved(scene=scene, frames=frames, solids=solids,
                    connectors=connectors, side_openings=side_openings,
                    wall_targets=wall_targets, supports=supports,
                    parents=parents, panels=panels, floor=floor_z, issues=issues)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _related(res: Resolved, a: str, b: str) -> bool:
    return res.parents.get(a) == b or res.parents.get(b) == a


#: Vertical interference below this is a graze, not a collision. The case is
#: cut with a 0.15 mm kerf into plywood that varies more than that; an
#: interference smaller than the build tolerance cannot be distinguished from
#: measurement error, especially against estimated volumes.
GRAZE = 0.2


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
                depth = z_overlap(a.z, b.z)
                if depth < GRAZE:
                    # Interference below the tolerance the case is even cut
                    # to (the kerf alone is 0.15 mm) is a graze, not a crash.
                    # It became visible when part undersides went from
                    # estimated slabs to measured bumps: a 0.07 mm clash
                    # between a measured solder tail and a datasheet-guessed
                    # box height is inside the guess's error bar, and
                    # painting the board red for it buries real collisions.
                    # Still reported -- it wants calipers, not silence.
                    issues.append(Issue(
                        "warning", "graze",
                        f"{a.ref} grazes {b.ref} by {depth:.2f} mm over "
                        f"{inter.area:.1f} mm^2 -- below build tolerance; "
                        f"measure before trusting either part's height",
                        [a.ref, b.ref]))
                else:
                    issues.append(Issue(
                        "error", "collision",
                        f"{a.ref} and {b.ref} overlap by {inter.area:.1f} mm^2 "
                        f"over {depth:.1f} mm of height",
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

    # 5. the anchor re-centres the scene on itself every solve, so dragging it
    # only slides everything else the other way. Worth saying out loud rather
    # than letting someone wonder why a board will not move.
    if res.scene.anchor:
        anchored = next((p for p in res.scene.placements
                         if p.id == res.scene.anchor), None)
        if anchored is not None and not anchored.locked:
            issues.append(Issue(
                "warning", "anchor_pinned",
                f"{anchored.id} is the scene anchor, so it is always at the origin "
                f"-- moving it just shifts everything else. Clear `anchor` to move it.",
                [anchored.id]))

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
