"""Flat parts: every piece that is cut out of a sheet, whichever pipeline drew it.

Two pipelines make cut plates. `hwcase.case` makes a stack of layers -- each
one a sheet with the hardware's pockets in it, sitting at a height. `hwcase.rack`
makes a frame whose side panels are plates bolted to the rails. Until now only
the first could reach a cutter, because the packer and the sheet writers took a
layered `CaseModel` and read layer things off it: an index, a role, whether it
is the floor.

None of that is what a cutter cares about. A cutter wants an outline with its
holes, the marks to engrave, the material it comes out of, and a name to write
on the cut list. So that is what a `FlatPart` is, and both pipelines produce
it: `from_case` turns layers into flat parts, `from_rack` turns a frame's
plates into flat parts, and `hwcase.export` packs and writes flat parts
without knowing which pipeline they came from.

The one thing a flat part has that a layer never needed is a **datum edge**:
the edge everything on the part is measured from when somebody checks it with
a rule. A side panel's M5 holes are where the rails land, and the cut list
gives them from the panel's bottom edge, because that is the edge you put
against the square. Joints (milestone M6 onwards) are cut along edges in the
same representation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from shapely.geometry import MultiPolygon, Polygon

from .schema import Material

if TYPE_CHECKING:                                         # pragma: no cover
    from .case import CaseModel, Layer
    from .rack import Member, RackModel


Point2 = tuple[float, float]


@dataclass(frozen=True)
class Edge:
    """A straight edge of a flat part, in the part's own 2D frame.

    **Directed, with the part on its left.** Walking from `a` to `b`, the
    material is on the left-hand side and open air on the right -- the way an
    outline's exterior ring runs when it is counter-clockwise. That one rule
    gives every edge an inside and an outside without carrying the outline
    around with it: the outward normal is the right-hand normal of `b - a`.

    An edge is a line, not a promise that material runs its whole length: a
    rounded corner lifts the outline off the ends of its datum edge, and the
    edge is still the straight line a square is held against.
    """

    a: Point2
    b: Point2
    name: str = ""

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def direction(self) -> Point2:
        """Unit vector from `a` towards `b`."""
        n = self.length
        if n <= 0:
            raise ValueError(f"edge {self.name!r} has zero length")
        return ((self.b[0] - self.a[0]) / n, (self.b[1] - self.a[1]) / n)

    @property
    def inward(self) -> Point2:
        """Unit normal pointing into the part (the left-hand side)."""
        dx, dy = self.direction
        return (-dy, dx)

    @property
    def outward(self) -> Point2:
        """Unit normal pointing out of the part (the right-hand side)."""
        dx, dy = self.direction
        return (dy, -dx)

    def measure(self, p: Point2) -> Point2:
        """(along, into): how far along the edge from `a`, and how far into
        the part from the edge's line -- what you read off a rule held
        against this edge."""
        dx, dy = self.direction
        ix, iy = self.inward
        vx, vy = p[0] - self.a[0], p[1] - self.a[1]
        return (vx * dx + vy * dy, vx * ix + vy * iy)


@dataclass(frozen=True)
class Labels:
    """Every name a writer puts on a part, decided by whoever made the part.

    The writers used to build these strings from a layer's index and role. A
    side panel has neither, and making one up would put "layer 00" on a piece
    of a Eurorack frame. So the producer names the part and the writers only
    copy the names out -- which is also what keeps a layered export
    byte-for-byte what it was: `for_layer` spells them exactly as the writers
    used to.
    """

    #: SVG group id of the part's cut passes (`-outline` is appended for the
    #: outer edge)
    group: str
    #: SVG group id of its engrave pass
    engrave: str
    #: DXF layer the cut entities go on (`_ENGRAVE` is appended for marks)
    cad_layer: str
    #: the SVG `<title>` text, before the pass is named
    title: str
    #: the cut list's columns for this part, before its size
    listing: str
    #: how an error message names it
    called: str

    @classmethod
    def plain(cls, name: str, role: str, material: Material) -> "Labels":
        """Names for a part that has a name of its own."""
        return cls(group=name, engrave=f"{name}-engrave", cad_layer=name,
                   title=f"{name} {material.name}",
                   listing=f"{name:<12} {material.name:<20}",
                   called=f"{name} ({role})")

    @classmethod
    def for_layer(cls, index: int, role: str, material: Material) -> "Labels":
        """A layer's names, spelled exactly as the layered writers always did."""
        return cls(group=f"layer-{index}", engrave=f"engrave-{index}",
                   cad_layer=f"L{index:02d}_{role}",
                   title=f"{index:02d} {role} {material.name}",
                   listing=f"layer {index:02d} {role:<6} {material.name:<20}",
                   called=f"layer {index} ({role})")


@dataclass
class FlatPart:
    """One piece cut out of a sheet.

    `outline` is the whole cut, in the part's own 2D frame: the exterior is
    the outer edge and every interior ring is a hole. `holes` is the drilled
    features listed one by one -- the outline already has them punched in, the
    list is what the cut list prints and what tests check positions against.
    """

    name: str
    material: Material
    #: Polygon or MultiPolygon; interiors are holes
    outline: Polygon | MultiPolygon
    #: what kind of piece this is -- "floor", "body", "lid" for a layer,
    #: "side_panel" for a frame plate. Written into the SVG as `data-role`.
    role: str
    #: position in its job: the tie-break when two parts are the same size,
    #: so the packing is deterministic
    index: int
    labels: Labels
    #: surface marks, never kerf-compensated, on the face the laser sees
    engrave: Optional[Polygon | MultiPolygon] = None
    #: drilled features with `.at`, `.diameter`, `.note` (`rack.Hole`)
    holes: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: the edge the part is measured from; see `Edge`
    datum: Optional[Edge] = None
    #: The part is installed with its engraved face down. A laser marks the
    #: face looking up at it, so such a part is cut mirrored -- outline and
    #: marks together -- and the flip at assembly puts every hole back where
    #: the design has it. Only matters when there is something to engrave.
    engraved_face_down: bool = False

    def datum_positions(self) -> list[tuple[str, float, float]]:
        """(note, along, into) for every hole, measured from the datum edge."""
        if self.datum is None:
            raise ValueError(f"{self.name} has no datum edge to measure from")
        return [(h.note, *self.datum.measure(h.at)) for h in self.holes]


# ---------------------------------------------------------------------------
# producers
# ---------------------------------------------------------------------------


def _bottom_edge(geom) -> Optional[Edge]:
    """The line along the bottom of a geometry's bounds, left to right."""
    if geom is None or geom.is_empty:
        return None
    x0, y0, x1, _y1 = geom.bounds
    return Edge((x0, y0), (x1, y0), "bottom")


def from_layer(layer: "Layer") -> FlatPart:
    """One case layer as a flat part. Its datum is the bottom of its bounds:
    a layer has no edge of its own that anything is drilled from."""
    return FlatPart(
        name=f"layer-{layer.index}",
        material=layer.material,
        outline=layer.geom,
        role=layer.role,
        index=layer.index,
        labels=Labels.for_layer(layer.index, layer.role, layer.material),
        engrave=layer.engrave,
        notes=list(layer.notes),
        datum=_bottom_edge(layer.geom),
        # Surface marks on the floor belong on its outside (bottom) face.
        engraved_face_down=layer.role == "floor",
    )


def from_case(case: "CaseModel") -> list[FlatPart]:
    """Every layer of a layered case, bottom to top."""
    return [from_layer(layer) for layer in case.layers]


#: Kerf for a frame's plates. A frame scene carries no material of its own
#: for its panels; this is the same 0.15 mm the side-panel SVG always used.
RACK_PLATE_KERF = 0.15


def rack_plate_material(member: "Member", kerf: float = RACK_PLATE_KERF
                        ) -> Material:
    """The sheet a frame plate is cut from, named with its thickness.

    A laser frame knows its sheet; a U-channel frame's acrylic panels are
    named from the member, as they always were.
    """
    if getattr(member, "stock", None) is not None:
        return member.stock
    return Material(name=f"{member.material}-{member.length:g}mm",
                    thickness=member.length, kerf=kerf, color="#d8dde3")


def from_member(member: "Member", index: int,
                kerf: float = RACK_PLATE_KERF) -> FlatPart:
    """One frame plate as a flat part.

    A side panel's section is drawn in (depth, height) with its origin at
    the front-bottom corner, and its `length` is the plate's thickness. The
    datum is the bottom edge, front to back: it is the outside of the case,
    and the edge both the rails' height and the M5 drilling are measured up
    from.

    A laser frame's base, back and top are cut from their own flat outline
    (`member.cut`), with the joints at its two ends; their datum is the edge
    along the front (base, top) or the bottom (back), left to right.
    """
    if member.kind not in ("side_panel", "plate"):
        raise ValueError(
            f"{member.name} is a {member.kind}, which is bought as extrusion "
            f"or folded sheet, not cut from a plate")
    mat = rack_plate_material(member, kerf)
    outline = member.cut if getattr(member, "cut", None) is not None \
        else member.section
    _x0, _y0, x1, _y1 = outline.bounds
    along = ("bottom edge, front to back" if member.kind == "side_panel"
             else "back plate's bottom edge, left to right"
             if member.name == "back"
             else f"{member.name} plate's front edge, left to right")
    return FlatPart(
        name=member.name,
        material=mat,
        outline=outline,
        role=member.kind,
        index=index,
        labels=Labels.plain(member.name, member.kind, mat),
        holes=list(member.holes),
        notes=list(member.notes),
        datum=Edge((0.0, 0.0), (x1, 0.0), along),
    )


def from_rack(model: "RackModel", kerf: float = RACK_PLATE_KERF
              ) -> list[FlatPart]:
    """Every plate of a frame. The rails and the U-channel are bought, not
    cut, so they are the cut list's business and not the packer's.

    A U-channel frame's two side panels come out of the same outline: they
    are mirror images as installed, and a plate turned over is its own mirror
    image, so the second is simply cut the same and flipped at assembly. A
    laser frame's cheeks carry their own joint cuts, so each is cut as drawn.
    """
    plates = [m for m in model.members if m.kind in ("side_panel", "plate")]
    return [from_member(m, i, kerf) for i, m in enumerate(plates)]


def parts_of(source) -> list[FlatPart]:
    """Flat parts from a layered case, or a list of flat parts as given."""
    from .case import CaseModel

    if isinstance(source, CaseModel):
        return from_case(source)
    parts = list(source)
    for p in parts:
        if not isinstance(p, FlatPart):
            raise TypeError(f"expected flat parts, got {type(p).__name__}")
    return parts
