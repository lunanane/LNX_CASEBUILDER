"""Data model for hardware parts, scenes and cases.

Coordinate conventions
----------------------
Part-local frame:  X right, Y up (both in the board plane), Z along the board
normal with +Z pointing at the component / user side.

z = 0 is the **bottom face of the PCB**. The board itself therefore occupies
[0, pcb_thickness]; components sit above that, through-hole pin tails and
socket bodies reach into negative z. This makes "stack a HAT onto a Pi" a
simple z-offset and keeps every datasheet height (which is quoted from the
board surface) usable without translation.

World frame: X right, Y "up" seen from above, Z up out of the bench. A part
placed with rot_z = 0 and flip = False has its local axes aligned with world.

All lengths are millimetres. All angles are degrees.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

class Confidence(str, Enum):
    """How much we trust a number. Anything below `datasheet` is a to-do."""

    measured = "measured"      # we put calipers on it (highest: it is *our* unit)
    datasheet = "datasheet"    # official mechanical drawing or vendor STEP/DXF
    vendor = "vendor"          # vendor product page / shop listing spec
    community = "community"    # forum post, GrabCAD, a case someone printed
    estimated = "estimated"    # our own guess -- MUST be verified before cutting

    @property
    def rank(self) -> int:
        return _CONF_RANK[self]


_CONF_RANK = {
    Confidence.measured: 4,
    Confidence.datasheet: 3,
    Confidence.vendor: 2,
    Confidence.community: 1,
    Confidence.estimated: 0,
}


class Source(Strict):
    confidence: Confidence = Confidence.estimated
    url: Optional[str] = None
    note: Optional[str] = None


# --------------------------------------------------------------------------
# 2D outlines
# --------------------------------------------------------------------------

class RectOutline(Strict):
    type: Literal["rect"] = "rect"
    size: Vec2
    corner_radius: float = 0.0
    #: where the local origin sits inside the rectangle
    origin: Literal["center", "min", "custom"] = "center"
    origin_offset: Vec2 = (0.0, 0.0)


class PolyOutline(Strict):
    type: Literal["poly"] = "poly"
    points: list[Vec2]


Outline = Union[RectOutline, PolyOutline]


# --------------------------------------------------------------------------
# volumes
# --------------------------------------------------------------------------

class VolumeKind(str, Enum):
    body = "body"            # solid matter: nothing else may occupy it
    keepout = "keepout"      # no material and no other part (airflow, tool access)
    cable = "cable"          # space a cable/bend needs; cables may share it
    actuator = "actuator"    # user must physically reach this (button, shaft)
    display = "display"      # user must see this (exposed, or behind a window)


class Face(str, Enum):
    px = "+x"
    nx = "-x"
    py = "+y"
    ny = "-y"
    pz = "+z"
    nz = "-z"

    @property
    def normal(self) -> Vec3:
        return _FACE_NORMAL[self]


_FACE_NORMAL: dict["Face", Vec3] = {
    Face.px: (1.0, 0.0, 0.0),
    Face.nx: (-1.0, 0.0, 0.0),
    Face.py: (0.0, 1.0, 0.0),
    Face.ny: (0.0, -1.0, 0.0),
    Face.pz: (0.0, 0.0, 1.0),
    Face.nz: (0.0, 0.0, -1.0),
}


class Repeat(Strict):
    """A grid of identical features, centred on the parent's `at`.

    A 4x4 keypad is sixteen 10 mm buttons on a 15 mm pitch, not one 55 mm
    square -- and the difference is the difference between a faceplate that
    works and a hole. Same for the four shafts on an encoder strip.
    """

    count: tuple[int, int] = (1, 1)
    pitch: Vec2 = (0.0, 0.0)


class Box(Strict):
    """One feature in part-local coordinates.

    `at` is the XY centre; `z` is the (z_min, z_max) interval measured from the
    PCB bottom face. `shape: circle` makes it a cylinder -- an encoder bushing
    wants a round hole, not a square one.
    """

    name: str
    kind: VolumeKind = VolumeKind.body
    at: Vec2
    size: Vec2
    z: Vec2
    shape: Literal["rect", "circle"] = "rect"
    #: rounds the corners of a rect; ignored for circles
    corner_radius: float = 0.0
    repeat: Optional[Repeat] = None
    src: Source = Field(default_factory=Source)

    def instances(self) -> list[tuple[str, Vec2]]:
        """(name, centre) for every copy of this feature."""
        r = self.repeat
        if r is None or (r.count[0] <= 1 and r.count[1] <= 1):
            return [(self.name, self.at)]
        nx, ny = max(1, r.count[0]), max(1, r.count[1])
        dx, dy = r.pitch
        out = []
        for j in range(ny):
            for i in range(nx):
                out.append((
                    f"{self.name}[{i},{j}]",
                    (self.at[0] + (i - (nx - 1) / 2) * dx,
                     self.at[1] + (j - (ny - 1) / 2) * dy),
                ))
        return out

    def z_min(self) -> float:
        return min(self.z)

    def z_max(self) -> float:
        return max(self.z)


# --------------------------------------------------------------------------
# holes, connectors, mates
# --------------------------------------------------------------------------

class Hole(Strict):
    name: str = "mount"
    at: Vec2
    diameter: float
    #: nominal screw, e.g. "M2.5" -- drives standoff selection later
    screw: Optional[str] = None
    src: Source = Field(default_factory=Source)


class Connector(Strict):
    """A plug-in point.

    What matters for a case: where the socket mouth is, which way you insert
    the plug, how much straight room the plug needs, and how big a hole the
    panel needs.
    """

    name: str
    type: str                      # usb_a, usb_micro_b, hdmi, audio_3v5, rj45,
                                   # stemma_qt, grove, header_2x20, usb_c ...
    face: Face                     # direction you insert the plug *from*
    #: centre of the socket mouth, in part-local coords
    at: Vec3
    #: socket body, so it participates in collision like anything else
    body: Optional[Box] = None
    #: straight, unobstructed room the mated plug needs beyond the mouth
    plug_depth: float = 10.0
    #: extra room past the plug for the cable to bend
    bend_radius: float = 12.0
    #: panel opening (width, height) as seen looking along the face normal
    cutout: Optional[Vec2] = None
    #: a 3.5 mm jack needs a round hole, not a 7 mm square. `circle` uses the
    #: larger of the two cutout dimensions as the diameter.
    cutout_shape: Literal["rect", "circle"] = "rect"

    #: True if this must reach the outside world; False = internal wiring only
    external: bool = False
    src: Source = Field(default_factory=Source)


class Mate(Strict):
    """A coupling that fully determines relative pose.

    Two parts with mates of the same `standard` and opposite `role` can be
    snapped together; the solver places the child so the mate frames coincide
    (plus `gap`).
    """

    name: str
    standard: str                  # "rpi_gpio_40", "eurorack_power_10", "grove"
    role: Literal["socket", "plug"]
    at: Vec3                       # mating plane centre in part-local coords
    face: Face = Face.pz           # which way the mate points
    #: allowed relative rotations about the mate normal, degrees
    rotations: list[float] = Field(default_factory=lambda: [0.0])
    src: Source = Field(default_factory=Source)


# --------------------------------------------------------------------------
# part
# --------------------------------------------------------------------------

class Part(Strict):
    id: str
    name: str
    vendor: Optional[str] = None
    sku: Optional[str] = None
    url: Optional[str] = None
    category: str = "misc"
    tags: list[str] = Field(default_factory=list)

    outline: Outline
    pcb_thickness: float = 1.6

    holes: list[Hole] = Field(default_factory=list)
    volumes: list[Box] = Field(default_factory=list)
    connectors: list[Connector] = Field(default_factory=list)
    mates: list[Mate] = Field(default_factory=list)

    #: reference CAD we downloaded, relative to vendor/cad/
    cad: Optional[str] = None
    notes: Optional[str] = None
    src: Source = Field(default_factory=Source)

    def min_confidence(self) -> Confidence:
        sources = [self.src]
        sources += [h.src for h in self.holes]
        sources += [v.src for v in self.volumes]
        sources += [c.src for c in self.connectors]
        sources += [m.src for m in self.mates]
        return min((s.confidence for s in sources), key=lambda c: c.rank)

    def z_extent(self) -> Vec2:
        """(z_min, z_max) of everything belonging to this part."""
        zs = [0.0, self.pcb_thickness]
        for v in self.volumes:
            zs += [v.z_min(), v.z_max()]
        for c in self.connectors:
            if c.body is not None:
                zs += [c.body.z_min(), c.body.z_max()]
        return (min(zs), max(zs))

    def height(self) -> float:
        lo, hi = self.z_extent()
        return hi - lo


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------

class CutoutPolicy(str, Enum):
    """What the case does about the connectors on one side of a board.

    The connectors that end up *under* a faceplate are the awkward ones: an
    HDMI plug needs to get out sideways, and how you let it out changes the
    whole look of the case. So it is a decision per side, not a global rule.
    """

    #: leave the wall solid here -- nothing gets out on this side
    none = "none"
    #: one opening per connector, just big enough for the plug and its bend
    per_connector = "per_connector"
    #: the same openings, but run out to the edge as slots, so a plug already
    #: fitted to a cable can be threaded in from outside
    open_to_edge = "open_to_edge"
    #: a groove of a chosen width from each port out through the wall, open all
    #: the way DOWN to the underside so a plug can be dropped in from below and
    #: slid home. Threading a cable through a slot from the side alone is
    #: unworkable in practice. The case keeps its shape everywhere else.
    channel = "channel"
    #: the same idea, but one opening spanning all the selected ports of that
    #: side at once, so a whole bank of connectors can be reached with the
    #: bottom plate out of the way. Only as wide and as tall as those ports --
    #: everything above them, and the rest of the case, stays closed.
    open_side = "open_side"


class SidePolicy(Strict):
    side: Face
    cutout: CutoutPolicy = CutoutPolicy.per_connector
    #: which of that side's connectors count. None = every external one.
    #: Naming a subset lets you ignore ports you will never plug into.
    include: Optional[list[str]] = None
    #: How much material sits between this side's outermost port and the
    #: OUTSIDE of the case. Unset, the wall lands wherever the global `wall`
    #: puts it -- which is measured from the bounding box, so a socket set well
    #: inside its board ends up buried that far in. Set it, and the case wall on
    #: that side is brought to exactly this distance from the socket mouth.
    margin: Optional[float] = None
    #: how wide a `channel` is cut. A centimetre gets a finger and a plug in.
    channel_width: float = 10.0
    #: clearance kept above and below the ports, for `open_side` and `channel`
    headroom: float = 2.0
    #: how wide the opening is. `ports` -- the default -- spans only the
    #: selected connectors, which is what you actually need to reach. `board`
    #: widens it to the board, `full` to the whole case beyond that edge.
    span: Literal["ports", "board", "full"] = "ports"


class Support(str, Enum):
    """How the case holds a board by its own mounting holes."""

    #: ignore this board's holes entirely -- it is held some other way, or not
    #: at all yet
    none = "none"
    #: build a column of material up from the bottom plate to the board's
    #: underside, with a clearance hole through it, and countersink the bottom
    #: plate so a screw head sits flush with the outside
    from_floor = "from_floor"
    #: run a clearance hole down through the faceplate so the board can be
    #: screwed up against its underside
    from_lid = "from_lid"


class Mount(str, Enum):
    """How a board's height is decided.

    Nobody should be typing a z. A board with something on top of it -- a
    screen, a knob, a jack, a button -- wants that feature exactly level with
    the faceplate. A board with nothing on top wants to be out of the way, flat
    on the bottom plate. `auto` picks between those two by looking at the part.
    """

    auto = "auto"        # panel if the board has anything facing up, else floor
    panel = "panel"      # a feature of it sits flush with a panel
    floor = "floor"      # it rests on the inside floor
    manual = "manual"    # pos[2] is used exactly as written


class Panel(Strict):
    """A user-facing surface: the plane your fingers and eyes meet.

    Everything a person touches wants to land on one of these. A panel is
    either a fixed world z, or -- better -- derived from a feature of the part
    that already fixes the height, with `from_ref: "<placement>.<volume>"`.
    Deriving it means the whole layout follows when that part moves: change the
    header stack under the screen and every keypad, knob and window follows.
    """

    name: str = "main"
    z: Optional[float] = None
    from_ref: Optional[str] = None       # "screen.active_area"
    note: Optional[str] = None


class Placement(Strict):
    """One instance of a part in the scene.

    Either free-floating (`pos` in world) or attached to another placement via
    a mate (`parent` + `parent_mate` + `mate`), in which case `pos` is ignored
    and computed by the solver.
    """

    id: str
    part: str                                # Part.id
    label: Optional[str] = None

    pos: Vec3 = (0.0, 0.0, 0.0)
    rot_z: float = 0.0
    #: quarter turns about the part's own X axis: 0 flat, 90 on edge, 180 upside
    #: down, 270 on the other edge. Only multiples of 90 -- the engine is 2.5D
    #: and a board at 30 degrees has no single z interval.
    tilt: Literal[0, 90, 180, 270] = 0
    #: old name for `tilt: 180`; kept so existing scenes still load
    flip: bool = False
    locked: bool = False

    @property
    def effective_tilt(self) -> int:
        return 180 if (self.flip and self.tilt == 0) else self.tilt

    parent: Optional[str] = None
    parent_mate: Optional[str] = None
    mate: Optional[str] = None
    mate_gap: float = 0.0

    #: whether the case carries this board on its own mounting holes
    support: Support = Support.none
    #: Counterbore the outer plate so the screw head finishes flush with it.
    #:
    #: Left unset this follows the mount direction, because the right answer is
    #: different at each end:
    #:
    #: * `from_floor` -> ON. A proud screw head on the underside makes the case
    #:   rock on the bench.
    #: * `from_lid` -> OFF. A front-mounted board usually sits directly under
    #:   the faceplate, and counterboring the only plate between the head and
    #:   the board leaves the head nothing to bear on -- it drops through and
    #:   clamps nothing. A plain clearance hole pulls the board up against the
    #:   faceplate, which is the point.
    #:
    #: Set it explicitly when there are layers between the plate and the board
    #: and you want the heads flush with the faceplate.
    screw_inset: Optional[bool] = None
    #: how the case treats each side of this board. Sides not listed use
    #: `per_connector`, which is what the case did before this existed.
    sides: list[SidePolicy] = Field(default_factory=list)
    #: the faceplate passes over this board unbroken -- nothing of it reaches
    #: the surface, so no window or actuator hole is cut for it
    under_panel: bool = False

    #: how z is decided. `auto` reads the part: anything facing up goes to the
    #: faceplate, anything else goes to the floor.
    mount: Mount = Mount.auto
    #: sit flush with this panel -- z is solved for, `pos[2]` is then ignored.
    #: Setting it implies `mount: panel`.
    on_panel: Optional[str] = None
    #: which feature lands on the panel: a volume name (searched in this part
    #: and in anything mated on top of it), "top" for the highest point of the
    #: whole subtree, or "auto" = the highest actuator/display, else "top".
    panel_ref: str = "auto"
    #: + stands proud of the panel, - sits recessed
    panel_offset: float = 0.0


class Material(Strict):
    name: str = "plywood-3mm"
    thickness: float = 3.0
    kerf: float = 0.15
    sheet: Vec2 = (600.0, 300.0)
    color: str = "#c8a165"


class CaseScrews(str, Enum):
    """Bolts that hold the sheet stack together."""

    none = "none"
    #: one near each corner of the outline
    corners = "corners"
    #: corners plus a run along each edge, for a case too big to hold with four
    perimeter = "perimeter"


class Interior(str, Enum):
    """What the middle layers look like between the hardware.

    A case full of I2C cables needs somewhere to put them. Left solid, the
    layers are a block of plywood with a pocket per board and nowhere for a
    wire to run; hollowed out completely, the faceplate has nothing holding it
    up in the middle.
    """

    #: solid, with a pocket per part and a corridor per external port.
    #: Thick and heavy, and internal wiring gets no room at all.
    pocketed = "pocketed"
    #: a wall of `wall` thickness all the way round, empty inside
    hollow = "hollow"
    #: hollow, plus ribs on a grid -- routed around the hardware and the
    #: cables, and pruned so nothing is left floating
    ribs = "ribs"
    #: pockets grown to include every cable port and the clearance around each
    #: board, so the wall is as thick as it can be without crushing a wire
    grown = "grown"


class CaseSpec(Strict):
    """How to wrap the scene in a box."""

    style: Literal["layered", "solid"] = "layered"
    interior: Interior = Interior.pocketed
    #: `ribs` only: width of each stiffener and how far apart they sit
    rib_width: float = 6.0
    rib_spacing: float = 45.0
    #: material stack from bottom to top; the last entry repeats as needed
    materials: list[Material] = Field(default_factory=lambda: [Material()])
    wall: float = 6.0                 # material around the parts, in XY
    floor_gap: float = 3.0            # depth under the lowest BOARD (underside protrusions pierce the base plate)
    ceiling_gap: float = 2.0
    part_clearance: float = 0.6       # slop around each part pocket
    cable_clearance: float = 5.0      # min gap between parts for wiring
    #: diameter of the material column left standing around a mounting hole
    #: Sink each support screw's head bore through every layer of its boss
    #: column except the one directly under the board (over it, for
    #: `from_lid`). The head then always bears on exactly one sheet, so ONE
    #: screw length -- one layer plus the board engagement -- fits every
    #: board in the case, however deep each one sits. Off, and the head sits
    #: in the outer plate instead: each board then needs its own screw
    #: length, measured stack by stack.
    screw_wells: bool = True

    support_boss: float = 9.0
    #: added to a hole's own diameter to get the through hole in the case
    screw_clearance: float = 0.4
    #: countersink in the outermost plate, so a screw head finishes flush
    screw_head: float = 6.5
    #: width of the strut tying an otherwise free-standing boss back to
    #: material -- a ring floating in a hollow layer is an offcut, not a post
    #: A whisker above min_segment on purpose: a rib at exactly the minimum
    #: is mathematically zero after the width check's erosion, and a tie that
    #: the width rules cannot vouch for defeats its own purpose.
    support_rib: float = 5.0

    #: Every internal lead has to be able to get from its board to the next
    #: one. Whatever the interior strategy carves, the empty space around the
    #: internal connectors is checked for connectivity, and a channel this wide
    #: is cut wherever a board would otherwise be walled in on its own.
    link_cables: bool = True
    #: Width of the channels carved so internal leads can reach each other.
    #: 5 mm passes an I2C/STEMMA lead with its plug; wider just costs more
    #: material and more thin-neck trouble beside the pockets.
    cable_channel: float = 5.0

    #: The cutting plates you actually have, as (width, height) in mm.
    #: Exports are split into one file per plate-load: each part goes inside a
    #: plate minus the margin, rotated 90 degrees when that packs better.
    #: One entry means "any number of plates of this size"; several entries
    #: describe a real stock of mixed sizes -- full sheets and the offcuts
    #: from the last job -- and the packer opens whichever size wastes least.
    #: 350 x 350 suits the common desktop machines.
    plates: list[Vec2] = Field(default_factory=lambda: [(350.0, 350.0)])
    #: keep-out from the sheet edge, where clamps live and focus drifts
    sheet_margin: float = 5.0
    #: air between neighbouring parts on a sheet
    sheet_spacing: float = 4.0

    #: An island smaller than this may fall off the cut as scrap -- the
    #: slivers between two neighbouring port openings are the usual case, and
    #: nobody wants them tied back. Anything bigger is real case material and
    #: gets a rib routed back to the main piece.
    min_island: float = 150.0

    #: Material narrower than this snaps. Two cutouts that pass close to each
    #: other leave a sliver of plywood between them that will not survive being
    #: handled, let alone glued up -- so anything thinner is opened out and the
    #: two cutouts become one. Set 0 to leave the geometry exactly as computed.
    min_segment: float = 4.0

    #: bolts through the whole stack, holding it together
    case_screws: CaseScrews = CaseScrews.none
    case_screw_d: float = 3.4          # M3 clearance
    case_screw_inset: float = 6.0      # in from the outline's bounding corners
    case_screw_head: float = 6.5       # countersink in the outer plate
    #: `perimeter` only: roughly how far apart bolts sit along each edge
    case_screw_spacing: float = 80.0
    #: Diameter of the collar of material carried around a case bolt, so it
    #: has something to pass through on every layer. A bolt is only a bolt if
    #: material touches it the whole way down; where a layer is hollow at that
    #: point, cutting a hole in nothing achieves nothing. Set 0 to drill only
    #: where material already happens to be.
    case_screw_boss: float = 9.0

    #: One more bolt in the middle. A wide lid bows between its edge screws,
    #: and this pulls the centre down -- but only works if the middle of the
    #: case is actually empty, so the build warns when it lands on a board.
    case_screw_center: bool = False
    #: vertical room wanted where one board passes over another
    overlap_clearance: float = 1.5
    corner_radius: float = 6.0
    outline: Optional[Outline] = None  # override the auto bounding shape


# ---------------------------------------------------------------------------
# front-panel decoration
# ---------------------------------------------------------------------------
#
# The models live here rather than in hwcase.engrave so that a Scene can
# reference them without the schema having to import shapely. The geometry
# that turns one of these into marks stays in hwcase.engrave.

class Pattern(str, Enum):
    """What to draw."""

    #: parallel raised-looking fins, the amplifier front-panel look
    fins = "fins"
    #: a field of rounded slots, like a speaker or vent grill
    slots = "slots"
    #: concentric rings, for a speaker or a rotary control
    rings = "rings"
    #: a hex mesh -- the most "machined" looking of the lot
    hex = "hex"
    #: a single hairline, for separating groups of controls
    rule = "rule"
    #: a filled border following the region's edge
    frame = "frame"
    #: lettering, in a single-stroke font -- see hwcase.hershey
    text = "text"


PATTERN_HELP = {
    "text": "lettering, in a single-stroke font a laser can follow in one pass",
    "fins": "parallel fins -- the amplifier front-panel look",
    "slots": "rounded slots in rows, like a speaker grill",
    "rings": "concentric rings, for a speaker or a big knob",
    "hex": "hex mesh; the most machined-looking of them",
    "rule": "one hairline, for separating groups of controls",
    "frame": "a border following the edge of the region",
}


class Engraving(Strict):
    """One decoration placed on a panel."""

    name: str = "engraving"
    pattern: Pattern = Pattern.fins
    #: centre, in the same world XY the placements use
    at: tuple[float, float] = (0.0, 0.0)
    size: tuple[float, float] = (60.0, 30.0)
    rotation: float = 0.0

    #: width of one mark, mm. A laser's kerf is around 0.15 mm, so anything
    #: under about 0.3 mm engraves as a single pass and reads as a hairline.
    stroke: float = Field(1.2, gt=0.0, le=20.0)
    #: gap between marks, mm
    pitch: float = Field(3.0, gt=0.0, le=100.0)
    #: rounded ends on the marks. Square ends look cheap at this scale.
    round_ends: bool = True

    #: cut right through instead of marking the surface. Off by default,
    #: because a grill sawn through a faceplate is usually a mistake, and
    #: because it changes whether the part still holds together.
    through: bool = False

    #: What a label says. Newlines start a new line.
    text: Optional[str] = None
    #: Cap height in millimetres -- the dimension you measure on a finished
    #: panel. Em size would make a "6 mm" label come out about 4 mm tall.
    text_size: float = Field(6.0, gt=0.0, le=200.0)
    #: which vendored stroke font: light or medium
    font: str = "light"
    text_align: str = "center"
    line_spacing: float = Field(1.45, gt=0.0, le=6.0)
    #: extra space between characters, mm; negative tightens
    tracking: float = 0.0

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        w, h = self.size
        return (self.at[0] - w / 2, self.at[1] - h / 2,
                self.at[0] + w / 2, self.at[1] + h / 2)


class ViewSettings(Strict):
    """How the preview is lit. None of this reaches the geometry.

    It lives on the scene rather than in the browser because a case is a
    document you come back to, and "which light made this look right" is part
    of what you decided -- losing it to a cleared cache is a small, avoidable
    annoyance.
    """

    #: key into the editor's environment list: room, studio, daylight, dusk
    env: str = "studio"
    exposure: float = Field(1.0, gt=0.0, le=8.0)
    #: show the environment behind the case, not just reflected in it
    backdrop: bool = False
    #: how strongly to darken the seams between slabs, 0..1
    ao: float = Field(0.55, ge=0.0, le=1.0)
    shadows: bool = True


class Scene(Strict):
    name: str = "untitled"
    anchor: Optional[str] = None       # placement id that defines the origin
    panels: list[Panel] = Field(default_factory=list)
    #: world z of the inside floor -- the top face of the bottom plate. Left
    #: unset it is derived: the lowest point of everything that is not itself
    #: floor-mounted, so the floor sits under the deepest hardware.
    floor: Optional[float] = None
    placements: list[Placement] = Field(default_factory=list)
    case: CaseSpec = Field(default_factory=CaseSpec)
    #: preview only -- see ViewSettings
    view: ViewSettings = Field(default_factory=ViewSettings)
    #: front-panel decoration. Marks the surface; does not cut through unless
    #: an engraving says so explicitly. See hwcase.engrave.
    engravings: list["Engraving"] = Field(default_factory=list)
