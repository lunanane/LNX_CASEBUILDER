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
    #: remove everything beyond this edge, from the bottom up to just above the
    #: cable. Gives a seamless faceplate over an open, variable underside.
    open_side = "open_side"


class SidePolicy(Strict):
    side: Face
    cutout: CutoutPolicy = CutoutPolicy.per_connector
    #: which of that side's connectors count. None = every external one.
    #: Naming a subset lets you ignore ports you will never plug into.
    include: Optional[list[str]] = None
    #: clearance kept above the highest included connector, for `open_side`
    headroom: float = 2.0
    #: `full` opens the whole case beyond that edge; `board` only the width of
    #: the board itself, so neighbouring hardware keeps its floor
    span: Literal["full", "board"] = "full"


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
    #: flipped upside down (180 deg about local X): component side faces -Z
    flip: bool = False
    locked: bool = False

    parent: Optional[str] = None
    parent_mate: Optional[str] = None
    mate: Optional[str] = None
    mate_gap: float = 0.0

    #: how the case treats each side of this board. Sides not listed use
    #: `per_connector`, which is what the case did before this existed.
    sides: list[SidePolicy] = Field(default_factory=list)
    #: the faceplate passes over this board unbroken -- nothing of it reaches
    #: the surface, so no window or actuator hole is cut for it
    under_panel: bool = False

    #: sit flush with this panel -- z is solved for, `pos[2]` is then ignored
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


class CaseSpec(Strict):
    """How to wrap the scene in a box."""

    style: Literal["layered", "solid"] = "layered"
    #: material stack from bottom to top; the last entry repeats as needed
    materials: list[Material] = Field(default_factory=lambda: [Material()])
    wall: float = 6.0                 # material around the parts, in XY
    floor_gap: float = 3.0            # air under the lowest part
    ceiling_gap: float = 2.0
    part_clearance: float = 0.6       # slop around each part pocket
    cable_clearance: float = 5.0      # min gap between parts for wiring
    #: vertical room wanted where one board passes over another
    overlap_clearance: float = 1.5
    corner_radius: float = 6.0
    outline: Optional[Outline] = None  # override the auto bounding shape


class Scene(Strict):
    name: str = "untitled"
    anchor: Optional[str] = None       # placement id that defines the origin
    panels: list[Panel] = Field(default_factory=list)
    placements: list[Placement] = Field(default_factory=list)
    case: CaseSpec = Field(default_factory=CaseSpec)
