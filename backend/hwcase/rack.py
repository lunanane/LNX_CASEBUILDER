"""Generating a Eurorack frame.

The other pipeline (`hwcase.case`) wraps a box around measured boards: the
hardware decides the shape. This one is the opposite. A Eurorack frame's shape
is decided by the *format* before any module exists, so nothing here looks at a
part -- it looks at a row stack and a width in HP.

It is also not a stack of sheet layers. The frame is four kinds of thing, and
three of them are extrusions:

* **rails** -- an aluminium profile cut to the case width. The rail is the
  case's visible front edge, not something hidden inside it.
* **the body** -- one folded aluminium U, whatever the case's height. It is a
  skin, not a chassis: it slides into the rails' slots and is clamped with
  sliding nuts.
* **side panels** -- flat plate, and the only structural member. Every rail is
  bolted between the two side panels with one M5 per end. Six holes in a panel
  means six rails means three rows.

So a member is a cross-section plus a length plus a pose, and the whole
generator is arithmetic over `hwcase.eurorack`. No CAD kernel, no meshing --
the browser extrudes these the same way it extrudes a case layer.

Coordinates: **X** is width (the HP axis, and every rail's length), **Y** is
height (the U axis), **Z** is depth into the case. Y = 0 is the bottom edge of
the side panel, which is the outside of the case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal, Optional

from shapely.affinity import scale
from shapely.geometry import Point, Polygon
from shapely.geometry import box as shapely_box

from .eurorack import (HP, RACK_U, RowFormat, SLOT_EDGE_INSET, frame_width,
                       slot_span)
from .schema import Material

# --------------------------------------------------------------------------
# the rail, as installed
# --------------------------------------------------------------------------
#
# The vendor drawing (f&w Art. 16500) is drawn on its side. Installed, the
# profile is 10.00 across the case's height and 19.00 deep into it -- and it
# has to be that way round, because two rows' slots sit 10.85 mm apart and a
# 19 mm tall rail could not fit between them. See docs/eurorack-frame-stock.md
# section 4.1.

#: How much of the case's height one rail occupies.
RAIL_H = 10.0

#: How far it reaches into the case.
RAIL_D = 19.0

#: Module slot centre, from either edge of the rail's height. Centred.
RAIL_SLOT_C = 5.0

#: The slot the body's flange edge slides into.
RAIL_SHEET_SLOT = 1.5

#: The end channel that takes an M5 from the side panel. This is what carries
#: the rail; it is not a module fixing.
RAIL_END_BORE = 4.4

#: Clearance hole for that M5 in the side panel.
M5_CLEAR = 5.0

#: Thread an M5 needs in a tapped aluminium rail end: two diameters, the
#: usual rule for a steel screw in a soft alloy.
M5_ENGAGEMENT = 10.0

#: Module fixings are bought by the row and not designed here, so they are
#: an estimate: modules average about this wide...
MODULE_HP_ESTIMATE = 8.0
#: ...and take a screw at the top and the bottom of the panel (4 from 12 HP,
#: which the average hides). An M3 goes through a 2 mm panel into a sliding
#: nut about 3 mm thick.
MODULE_PANEL_T = 2.0
SLIDING_NUT_T = 3.0

# --------------------------------------------------------------------------
# the body
# --------------------------------------------------------------------------

#: Sheet thickness of the folded U.
BODY_T = 1.0

#: Flange depth -- and therefore the case depth. Fixed by the stock, not
#: derived from anything.
#: How deep a module may be: clear space from the module face back to the
#: inside of the body's web. This is *the* depth parameter -- the flange, the
#: case depth and the side panels all follow from it.
#:
#: 51 is the built case: a 40 mm flange starting 12 mm behind the module face,
#: less the 1 mm web. Skiffs run 25-40; deeper cases leave room for a PSU
#: behind the modules.
MODULE_DEPTH = 51.0

#: How far the rail's outer edge sits below the sheet slot the body's flange
#: enters -- and therefore how far the rail stands proud of the body.
#:
#: **This is a property of the extrusion, not a setting.** There is one rail
#: (f&w Art. 16500) and its slot is where it is; the body has to meet it
#: there. It is exposed as a function argument only so the geometry tests can
#: probe it.
#:
#: It cannot exceed `RAIL_SLOT_C`: past that the flange would have to enter
#: the rail outside the rail, which is not a case, it is a contradiction.
#:
#: 2.7 is measured three ways over: it puts the body's web at 216.0 mm (the
#: channel that was actually ordered), the outermost M5 at 12.3 mm from the
#: panel edge (the built panel's first hole is at 12), and it agrees with the
#: 2.80 lip dimension on the vendor's own cross-section.
RAIL_OVERHANG = 2.7


def body_edge_inset(overhang: float = RAIL_OVERHANG) -> float:
    """How far the body's edge sits outboard of the outermost slot centre.

    Not independent of the overhang: the slot sits `RAIL_SLOT_C` inside the
    rail's edge, so whatever the rail projects past the body comes off that.
    Which is also why the overhang cannot exceed `RAIL_SLOT_C` -- at that
    point the inset is zero and beyond it the body would have to hang off the
    outside of the rail.
    """
    if overhang > RAIL_SLOT_C:
        raise ValueError(
            f"rail overhang {overhang} exceeds {RAIL_SLOT_C} mm: the body's "
            f"flange would sit outside the rail. The overhang is a property "
            f"of the extrusion and is not adjustable.")
    return RAIL_SLOT_C - overhang

# --------------------------------------------------------------------------
# side panels
# --------------------------------------------------------------------------

#: Vertical clearance a 3U module's PCB needs through an insert slot: the
#: panel height less the board's shortfall. Measured off the built panel's
#: 112 mm slot against a 128.5 panel.
PCB_SHORTFALL = 16.5

#: How far a side panel reaches past the **body's** edge, along the case's
#: height. Measured from the body rather than the rail on purpose: the rail
#: already sits `overhang` outside the body, so this says whether the panel
#: catches up with it.
#:
#: At 0 the panel stops level with the body, the rail is the only thing below
#: the case, and it stands on aluminium -- which is the point of having an
#: overhang. Once it exceeds the overhang the panel reaches past the rail and
#: the case stands on acrylic instead.
#:
#: The built case measures 10.5: a 237 mm panel around a 216 mm web. So that
#: case stands on its panels, not its rails -- and that is the default here,
#: because the alternative is thin.
#:
#: The trade-off is forced. The M5 sits at the rail's mid-height, 5 mm inside
#: its outer edge, so a panel that stops short of the rail has under 5 mm of
#: acrylic below the hole. Standing on the rails means accepting about 2 mm
#: there. To have both, raise `RAIL_OVERHANG` until it exceeds the margin you
#: want -- the panel reports which one it ended up on.
PANEL_MARGIN = 10.5

#: The built case's value, kept as the regression fixture's reference.
BUILT_PANEL_MARGIN = 10.5

#: Acrylic left around an M5 clearance hole before the panel's edge.
#:
#: Thin, deliberately. The M5 sits at the rail's mid-height, 5 mm inside its
#: outer edge, so a panel that stops short of the rail -- which is the whole
#: point of an overhang -- has under 5 mm to play with. 2 mm in 5 mm acrylic
#: holds; less starts to look like a tear-out waiting to happen.
PANEL_HOLE_EDGE = 2.0


def min_panel_margin(overhang: float = RAIL_OVERHANG) -> float:
    """The smallest panel margin that still contains the M5 holes.

    The outermost hole centre sits `body_edge_inset` above the body's edge,
    and the hole needs its radius plus `PANEL_HOLE_EDGE` of material below
    that. Anything less and the hole runs off the panel: the outline stops
    being a rectangle with holes in it and becomes a comb, which is neither
    cuttable nor renderable.
    """
    reach = M5_CLEAR / 2 + PANEL_HOLE_EDGE
    return max(0.0, reach - body_edge_inset(overhang))


def resolve_margin(margin: float | None = None,
                   overhang: float = RAIL_OVERHANG) -> float:
    """The margin actually used: never below what the holes need."""
    lo = min_panel_margin(overhang)
    return lo if margin is None else max(float(margin), lo)

#: Thickness of a side panel. The panels sit *outside* the rails: a rail
#: spans the full case width and each end takes an M5 through the panel, so
#: the panel's inner face is where the rail stops.
SIDE_PANEL_T = 5.0

#: How far the sheet slot reaches forward from the rail's back face -- and so
#: how far the body's flange enters the rail.
#:
#: A property of the extrusion, like `RAIL_OVERHANG`. It is what used to be
#: written down as "the side panel is 12 mm deeper than the flange": the same
#: fact seen from the other end, since the body starts `RAIL_D - this` behind
#: the module face. Naming it here means the case's depth is derived from the
#: module depth instead of from a fitted panel dimension.
RAIL_SLOT_DEPTH = 7.0


def body_depth_offset() -> float:
    """How far back the body starts, from the module face.

    Fixed by the rail: the flange has to reach into the sheet slot, and the
    slot is where the extrusion puts it. Independent of how deep the case is.
    """
    return RAIL_D - RAIL_SLOT_DEPTH


def body_flange(module_depth: float = MODULE_DEPTH) -> float:
    """Depth of the folded flange, for a given module depth.

    The flange runs from where it enters the rail to the web at the back, so
    it is the case depth less the head start the rail gives it.
    """
    f = module_depth + BODY_T - body_depth_offset()
    if f < RAIL_SLOT_DEPTH:
        raise ValueError(
            f"module depth {module_depth} mm leaves a {f:.1f} mm flange, "
            f"which is shorter than the {RAIL_SLOT_DEPTH} mm it has to reach "
            f"into the rail. The shallowest case this rail allows is "
            f"{RAIL_SLOT_DEPTH + body_depth_offset() - BODY_T:.1f} mm.")
    return f


def case_depth(module_depth: float = MODULE_DEPTH) -> float:
    """Front of the rail to the back of the body -- the case's own depth."""
    return module_depth + BODY_T


def rail_body_overlap() -> float:
    """How much of the rail's depth the body's flange reaches into."""
    return RAIL_SLOT_DEPTH


def rail_bore_depth() -> float:
    """Where the M5 end bore sits along the rail's depth.

    Not a free choice. The back of the rail is occupied: that is where the
    sheet slot is, and behind it the body's flange. A bore placed there runs
    straight through the flange -- the screw and the sheet fight over the same
    millimetres, which is neither buildable nor, once you look closely at the
    render, invisible.

    So it goes in the solid section in front of the slot, centred, which is
    the only place it can be without touching either.
    """
    return body_depth_offset() / 2


@dataclass(frozen=True)
class PanelPlan:
    """How far the side panel reaches past the case, and how its corners are cut.

    Each edge is independent because they answer to different things. Bottom
    and top decide what the case rests on and how much acrylic surrounds the
    outermost M5s. Front and back are pure overhang -- a lip in front of the
    rails, or behind the body's web -- and default to nothing.

    Depth runs front (the module face) to back (the body's web), so the
    corners are named by the two edges that meet at them.
    """

    #: past the body's edge, along the case's height
    top: float = PANEL_MARGIN
    bottom: float = PANEL_MARGIN
    #: past the rail's face / the body's web, along the case's depth
    front: float = 0.0
    back: float = 0.0

    #: corner radii, each named for the two edges it joins
    r_bottom_front: float = 0.0
    r_bottom_back: float = 0.0
    r_top_back: float = 0.0
    r_top_front: float = 0.0

    def resolved(self, overhang: float = RAIL_OVERHANG) -> "PanelPlan":
        """The same plan with top and bottom raised to what the holes need."""
        lo = min_panel_margin(overhang)
        return replace(self, top=max(self.top, lo), bottom=max(self.bottom, lo))

    @property
    def radii(self) -> tuple[float, float, float, float]:
        return (self.r_bottom_front, self.r_bottom_back,
                self.r_top_back, self.r_top_front)


def _rounded_rect(d0: float, h0: float, d1: float, h1: float,
                  radii: tuple[float, float, float, float],
                  seg: int = 14) -> Polygon:
    """A rectangle with a different radius at each corner.

    Shapely can round every corner at once or none, which is no use when the
    point is to bevel them separately. A radius of 0 collapses its arc back to
    the sharp corner, so a plain rectangle costs nothing.
    """
    r_bf, r_bb, r_tb, r_tf = (max(0.0, r) for r in radii)
    # never let two radii on one edge eat more than the edge
    span_d, span_h = d1 - d0, h1 - h0
    for a, b, span in ((r_bf, r_bb, span_d), (r_tf, r_tb, span_d),
                       (r_bf, r_tf, span_h), (r_bb, r_tb, span_h)):
        if a + b > span:
            raise ValueError(
                f"corner radii {a} + {b} exceed the {span:.2f} mm edge "
                f"between them")

    def arc(cx, cy, r, a0, a1):
        if r <= 0:
            return [(cx, cy)]
        return [(cx + r * math.cos(a0 + (a1 - a0) * i / seg),
                 cy + r * math.sin(a0 + (a1 - a0) * i / seg))
                for i in range(seg + 1)]

    pts = []
    pts += arc(d0 + r_bf, h0 + r_bf, r_bf, math.pi, 1.5 * math.pi)
    pts += arc(d1 - r_bb, h0 + r_bb, r_bb, 1.5 * math.pi, 2 * math.pi)
    pts += arc(d1 - r_tb, h1 - r_tb, r_tb, 0.0, 0.5 * math.pi)
    pts += arc(d0 + r_tf, h1 - r_tf, r_tf, 0.5 * math.pi, math.pi)
    return Polygon(pts)


@dataclass
class Hole:
    """A drilled feature on a member, in that member's own 2D frame."""

    at: tuple[float, float]
    diameter: float
    note: str = ""


@dataclass
class Member:
    """One physical piece: a cross-section, a length, and where it sits.

    `section` is a polygon in the member's own 2D frame. `axis` says which
    world axis it is swept along, and `origin` is where the section's local
    origin lands. That covers an extrusion (rail, body) and a plate (side
    panel) with one representation, which is the whole reason the frame does
    not need the layer machinery.
    """

    name: str
    #: `plate` is a laser-cut frame's base, back or top
    kind: Literal["rail", "body", "side_panel", "plate"]
    section: Polygon
    length: float
    origin: tuple[float, float, float]
    axis: Literal["x", "y", "z"] = "x"
    material: str = "aluminium"
    holes: list[Hole] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The plate as it is cut, in its own flat frame, when that is not the
    #: section. A base plate is drawn for the 3D view as a slab swept across
    #: the width; its joints are at the two ends of that sweep, which a
    #: section cannot show, so the cutter gets this instead.
    cut: Optional[Polygon] = None
    #: The sheet it is cut from, when the frame knows it (laser style). Left
    #: unset, a plate is cut from `material` at `length` thick.
    stock: Optional[Material] = None


@dataclass
class Row:
    """One row, placed."""

    fmt: RowFormat
    #: rack coordinates of this row's allocation, from the stack's bottom
    rack: tuple[float, float]
    #: world Y of this row's two rail slot centres, bottom then top
    slots: tuple[float, float]

    @property
    def span(self) -> float:
        return self.slots[1] - self.slots[0]


@dataclass
class RackModel:
    rows: list[Row]
    members: list[Member]
    hp: float
    width: float
    web: float
    panel_height: float
    overhang: float = RAIL_OVERHANG
    module_depth: float = MODULE_DEPTH
    plan: PanelPlan = field(default_factory=lambda: PanelPlan())
    #: `u_channel`: rails, a folded aluminium body and two side panels.
    #: `laser`: the same rails, and every other piece cut from sheet.
    style: Literal["u_channel", "laser"] = "u_channel"
    #: how thick each side panel (cheek) is
    panel_t: float = SIDE_PANEL_T
    #: laser style: every joint between two plates, for the BOM and checks
    joints: list = field(default_factory=list)
    #: laser style: the margin the plates and their joints need; the panel's
    #: margins were raised to at least this
    min_margin: Optional[float] = None
    #: laser style: 'tslot' or 'finger'
    joint: str = ""

    @property
    def margin(self) -> float:
        """The bottom margin -- what decides whether it stands on its rails."""
        return self.plan.bottom

    @property
    def rails(self) -> list[Member]:
        return [m for m in self.members if m.kind == "rail"]

    @property
    def depth(self) -> float:
        """How far the case reaches back from the module face."""
        return max(
            (m.section.bounds[2] for m in self.members),
            default=case_depth(self.module_depth))

    @property
    def outer_width(self) -> float:
        """Rail span plus a side panel either side."""
        return self.width + 2 * self.panel_t

    @property
    def plates(self) -> list[Member]:
        """Everything cut from sheet: the side panels, and a laser frame's
        base, back and top."""
        return [m for m in self.members if m.kind in ("side_panel", "plate")]

    @property
    def stands_on(self) -> str:
        """What the case actually rests on, once the margin is resolved."""
        return "rails" if self.overhang > self.margin else "side panels"

    def bounds(self) -> tuple[float, float, float, float, float, float]:
        """(x0, y0, z0, x1, y1, z1) over every member, in rack's own frame."""
        xs, ys, zs = [], [], []
        for m in self.members:
            d0, h0, d1, h1 = m.section.bounds
            xs += [m.origin[0], m.origin[0] + m.length]
            ys += [m.origin[1] + h0, m.origin[1] + h1]
            zs += [m.origin[2] + d0, m.origin[2] + d1]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def order_spec(self) -> Optional[dict[str, float | str]]:
        """The U-channel, in the fields a sheet-metal shop's form asks for.

        Suppliers who fold to order want a length and the three flats of the
        developed cross-section, in order: `A / B / C`. That is exactly what
        the built case was ordered as (al5905100upko, 376 x 40/216/40 x 1.0),
        so this is the same form filled in for whatever you have designed.

        None for a laser-cut frame, which has no U-channel to order.
        """
        if self.style == "laser":
            return None
        return {
            "part": "aluminium U-channel, folded, cut to size",
            "length_mm": round(self.width, 1),
            "width_a_mm": round(body_flange(self.module_depth), 1),
            "width_b_mm": round(self.web, 1),
            "width_c_mm": round(body_flange(self.module_depth), 1),
            "thickness_mm": BODY_T,
            "developed_mm": round(
                2 * body_flange(self.module_depth) + self.web, 1),
        }

    def rail_screw_length(self) -> float:
        """The M5 that holds a rail: through the side panel and
        `M5_ENGAGEMENT` into the tapped rail end, rounded up to stock."""
        from .fasteners import fastener
        return fastener("M5").bolt_length(self.panel_t + M5_ENGAGEMENT)

    def module_fixings(self) -> list[dict[str, float | int | str]]:
        """An estimate per row of the M3s and sliding nuts its modules take.

        Not designed -- what goes in a row is the user's choice -- so it is
        sized by HP at `MODULE_HP_ESTIMATE` per module and two screws each,
        and always labelled an estimate.
        """
        import math

        from .fasteners import fastener
        m3 = fastener("M3")
        length = m3.bolt_length(MODULE_PANEL_T + SLIDING_NUT_T)
        out = []
        for i, r in enumerate(self.rows):
            n = 2 * math.ceil(self.hp / MODULE_HP_ESTIMATE)
            out.append({"row": i + 1, "format": r.fmt.name, "hp": self.hp,
                        "screws": n, "screw": f"M3x{length:g}",
                        "strip_mm": round(self.width, 1)})
        return out

    def bill_of_materials(self) -> dict[str, int | float]:
        screws = 2 * len(self.rails)
        if self.style != "laser":
            out: dict[str, int | float] = {
                "rail mm": round(self.width, 2) * len(self.rails),
                "rails": len(self.rails),
                "body mm": round(self.width, 2),
                "side panels": 2,
                "M5 screws": screws,
            }
        else:
            out = {
                "rail mm": round(self.width, 2) * len(self.rails),
                "rails": len(self.rails),
                "side panels": 2,
                "plates": len([m for m in self.members if m.kind == "plate"]),
                "M5 screws": screws,
            }
        out["M5 screw length mm"] = self.rail_screw_length()
        fix = self.module_fixings()
        if fix:
            n = sum(int(f["screws"]) for f in fix)
            out[f"{fix[0]['screw']} module screws (estimate)"] = n
            out["M3 sliding nuts (estimate)"] = n
        for j in self.joints:
            for item, n in j.bom().items():
                out[item] = out.get(item, 0) + n
        return out


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------


def web_height(rows: list[RowFormat],
               overhang: float = RAIL_OVERHANG) -> float:
    """Height of the body's web for a row stack.

    `(U * 44.45 - 10.85) + 2c`, which is the outermost slot span plus the
    body's overhang at each end. For 5U that is 216.00 -- the built case,
    exactly.
    """
    total_u = sum(r.u for r in rows)
    return slot_span(total_u) + 2 * body_edge_inset(overhang)


def panel_height(rows: list[RowFormat],
                 overhang: float = RAIL_OVERHANG,
                 plan: "PanelPlan | None" = None) -> float:
    """Height of a side panel: the body, plus each margin independently.

    The panel wraps the body, not the rails: with both margins at their floor
    the rails -- which sit `overhang` further out -- are the only thing the
    case can rest on.
    """
    q = (plan or PanelPlan()).resolved(overhang)
    return web_height(rows, overhang) + q.top + q.bottom


def panel_base(rows: list[RowFormat],
               overhang: float = RAIL_OVERHANG,
               plan: "PanelPlan | None" = None) -> float:
    """World Y of a side panel's bottom edge.

    The body's edge is the datum; the panel starts its bottom margin below it.
    """
    q = (plan or PanelPlan()).resolved(overhang)
    placed = layout_rows(rows, overhang)
    return placed[0].slots[0] - RAIL_SLOT_C + overhang - q.bottom


def layout_rows(rows: list[RowFormat],
                overhang: float = RAIL_OVERHANG) -> list[Row]:
    """Place each row and both of its rail slots, bottom to top.

    Rack space is allocated strictly: row `k` gets `u * 44.45` of it and its
    slots sit `5.425` in from each of its own boundaries. That is what makes a
    mixed stack work -- a 1U tile row costs exactly 44.45 whatever its panels
    do, so `1U + 3U + 1U` lands on the same 5U the built case uses.
    """
    out: list[Row] = []
    # world Y of the first slot: past the bottom rail, then the body's inset
    y0 = RAIL_H + body_edge_inset(overhang)
    cursor = 0.0
    for fmt in rows:
        a, b = cursor, cursor + fmt.u * RACK_U
        out.append(Row(
            fmt=fmt,
            rack=(a, b),
            slots=(y0 + a, y0 + b - 2 * SLOT_EDGE_INSET),
        ))
        cursor = b
    return out


# --------------------------------------------------------------------------
# cross-sections
# --------------------------------------------------------------------------


def rail_section(flip: bool = False,
                 overhang: float = RAIL_OVERHANG) -> Polygon:
    """The rail's cross-section, in (depth, height), front face at depth 0.

    Simplified to the envelope plus the sheet slot: what decides assembly is
    the 10.00 height, the 19.00 depth, the slot the body's flange enters and
    the centred module slot. The decorative relief of the real extrusion
    changes nothing a case generator needs.

    The slot is placed where the flange actually is, rather than at some
    convenient mid-height: it opens at the rail's **back** face, reaches
    forward exactly as far as the body overlaps the rail, and sits at the
    height the flange arrives at -- which is `overhang` up from the rail's
    outer edge, because that is how far the rail projects past the sheet.

    Every rail in a case is the same extrusion. `flip` mirrors it for the
    upper rail of a row, so the two lips face outwards and the two slots face
    each other, which is how a pair is actually mounted.
    """
    body = shapely_box(0.0, 0.0, RAIL_D, RAIL_H)
    mid = overhang + BODY_T / 2
    lo = max(0.0, mid - RAIL_SHEET_SLOT / 2)
    slot = shapely_box(RAIL_D - rail_body_overlap(), lo,
                       RAIL_D, min(RAIL_H, lo + RAIL_SHEET_SLOT))
    section = body.difference(slot)
    if flip:
        section = scale(section, xfact=1.0, yfact=-1.0, origin=(0.0, RAIL_H / 2))
    return section


def body_section(web: float,
                 module_depth: float = MODULE_DEPTH) -> Polygon:
    """The folded U, as a 1.0 mm open profile in (depth, height).

    Flange, web, flange -- the shape you get by unrolling the supplier's
    `40 / web / 40` and bending it twice. Drawn as a real thin section rather
    than a centre line so it extrudes into something with a wall.

    **The U opens towards the front.** Depth 0 is the module face, so the web
    sits at the *back* (depth = flange) and the two flanges run forward along
    the case's top and bottom edges, where their front lips enter the outer
    rails' sheet slots. Built the other way round the web lands across the
    front of the case, which is a lid, not a chassis.
    """
    t, f = BODY_T, body_flange(module_depth)
    outer = shapely_box(0.0, 0.0, f, web)
    inner = shapely_box(0.0, t, f - t, web - t)
    return outer.difference(inner)


def side_panel_section(rows: list[RowFormat], hp: float,
                       inserts_per_row: int = 0,
                       insert_hp: float = 4.0,
                       overhang: float = RAIL_OVERHANG,
                       plan: "PanelPlan | None" = None,
                       module_depth: float = MODULE_DEPTH,
                       depth: Optional[float] = None,
                       ) -> tuple[Polygon, list[Hole]]:
    """The side panel outline and every hole in it.

    The M5 pattern is *derived* from where the rails land, never tabulated --
    which is the point: change the row stack and the drilling follows. The
    built 5U panel is a regression fixture for this, not its source.

    `depth` overrides the case's depth: a laser frame's cheek runs past its
    back plate rather than stopping at a U-channel's web.
    """
    q = (plan or PanelPlan()).resolved(overhang)
    h = panel_height(rows, overhang, q)
    depth = case_depth(module_depth) if depth is None else depth
    base = panel_base(rows, overhang, q)
    # The panel's own frame starts at its front-bottom corner, so a front
    # overhang pushes the case's depth 0 to `q.front` rather than moving
    # everything else.
    outline = _rounded_rect(0.0, 0.0, q.front + depth + q.back, h, q.radii)

    holes: list[Hole] = []
    placed = layout_rows(rows, overhang)
    # the M5 lands on the rail's end bore: centred in the rail's height, and
    # forward of the sheet slot so it misses the body's flange entirely
    z_bore = rail_bore_depth()
    for i, row in enumerate(placed):
        for end, y in zip(("lo", "hi"), row.slots):
            holes.append(Hole(
                at=(q.front + z_bore, y - base),
                diameter=M5_CLEAR,
                note=f"M5 into row {i} {end} rail end",
            ))

    slots: list[Polygon] = []
    for row in placed:
        if not inserts_per_row or row.fmt.u < 3.0:
            continue
        length = row.fmt.panel - PCB_SHORTFALL
        centre = (row.slots[0] + row.slots[1]) / 2 - base
        width = insert_hp * HP
        # breaks the back edge whatever the overhang, so the module still
        # slides in
        back = q.front + depth + q.back
        slots.append(shapely_box(
            back - width, centre - length / 2, back + 1.0, centre + length / 2,
        ))

    for s in slots:
        outline = outline.difference(s)
    # Drill them into the section too. The list stays authoritative for the
    # cutlist; this is so the thing you see has holes in it.
    for h in holes:
        outline = outline.difference(
            Point(*h.at).buffer(h.diameter / 2, quad_segs=12))
    return outline, holes


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def build(rows: list[RowFormat], hp: float, inserts_per_row: int = 0,
          insert_hp: float = 4.0,
          overhang: float = RAIL_OVERHANG,
          plan: "PanelPlan | None" = None,
          module_depth: float = MODULE_DEPTH, *,
          style: Literal["u_channel", "laser"] = "u_channel",
          material: Optional[Material] = None,
          joint: Literal["tslot", "finger"] = "tslot",
          top_plate: bool = True,
          fastener: str = "M3") -> RackModel:
    """Turn a row stack and a width into every piece of the frame.

    `style="laser"` builds the same rails with cut plates in place of the
    folded body; see `_build_laser`. The U-channel frame is described below.

    Three datums, and everything else hangs off them:

    * **the back of the body is the back of the case**, so the body is pushed
      back from the module face rather than starting at it, and what is left
      in front is the depth the rails and the body share;
    * **the bottom rail's outer edge is the bottom of the case**, with the
      body's edge sitting `overhang` above it -- so at overhang > 0 the case
      stands on aluminium and not on a folded 1 mm lip;
    * **the rails span the width exactly**, and the side panels bolt to their
      ends from outside, so the panels add their own thickness either side.
    """
    if style == "laser":
        return _build_laser(rows, hp, inserts_per_row, overhang,
                            plan or PanelPlan(), module_depth,
                            material or LASER_MATERIAL, joint, top_plate,
                            fastener)
    if style != "u_channel":
        raise ValueError(f"unknown frame style {style!r}: "
                         f"'u_channel' or 'laser'")
    plan = (plan or PanelPlan()).resolved(overhang)
    body_flange(module_depth)          # fail loudly on an impossible depth
    width = frame_width(hp)
    web = web_height(rows, overhang)
    ph = panel_height(rows, overhang, plan)
    placed = layout_rows(rows, overhang)

    members: list[Member] = _rails(placed, width, overhang)

    # The body sits `overhang` above the bottom rail's outer edge, and its web
    # lands on the back of the case.
    body_y = placed[0].slots[0] - RAIL_SLOT_C + overhang
    members.append(Member(
        name="body",
        kind="body",
        section=body_section(web, module_depth),
        length=width,
        origin=(0.0, body_y, body_depth_offset()),
        axis="x",
        notes=[f"one folded U, web {web:.2f}, "
               f"flange {body_flange(module_depth):.2f}, "
               f"{overhang:.2f} mm inside the rail edge"],
    ))

    panel, holes = side_panel_section(
        rows, hp, inserts_per_row, insert_hp, overhang, plan, module_depth)
    base = panel_base(rows, overhang, plan)
    for side, x in (("left", -SIDE_PANEL_T), ("right", width)):
        members.append(Member(
            name=f"side-{side}",
            kind="side_panel",
            section=panel,
            length=SIDE_PANEL_T,
            origin=(x, base, -plan.front),
            axis="x",
            material="acrylic",
            holes=list(holes),
            notes=[f"{len(holes)} x M5 into rail ends"],
        ))

    return RackModel(rows=placed, members=members, hp=hp, width=width,
                     web=web, panel_height=ph, overhang=overhang, plan=plan,
                     module_depth=module_depth)


def _rails(placed: list[Row], width: float, overhang: float) -> list[Member]:
    """Two rails per row, spanning the width exactly. Shared by both styles,
    so a laser frame's rails -- and therefore its M5s -- are the U-channel
    frame's to the hundredth."""
    out: list[Member] = []
    # Every rail is the same extrusion; the upper rail of a pair is mirrored
    # so the two lips face outwards and the two slots face each other.
    lower = rail_section(flip=False, overhang=overhang)
    upper = rail_section(flip=True, overhang=overhang)
    for i, row in enumerate(placed):
        for end, y, section in (("lo", row.slots[0], lower),
                                ("hi", row.slots[1], upper)):
            out.append(Member(
                name=f"rail-r{i}-{end}",
                kind="rail",
                section=section,
                length=width,
                origin=(0.0, y - RAIL_SLOT_C if end == "lo"
                        else y - (RAIL_H - RAIL_SLOT_C), 0.0),
                axis="x",
                notes=[f"row {i} ({row.fmt.name}) {end} rail, "
                       f"slot centre y={y:.2f}"],
            ))
    return out


# --------------------------------------------------------------------------
# the laser-cut frame
# --------------------------------------------------------------------------
#
# The same rails, bolted the same way into two cheeks; the folded body is
# replaced by a base, a back and (optionally) a top plate, each jointed into
# both cheeks. The cheeks are the side panels as they always were -- the same
# outline arithmetic, the same M5s -- only cut from the frame's sheet, deeper
# by the back plate, and with the joints cut into them.
#
# Where the plates go:
#
# * base and top run *behind* the rails, from the rails' back face to the back
#   plate, so no plate ever crosses a rail whatever its thickness;
# * their outer faces sit on the cheek's edge for finger joints, and one sheet
#   inside it for T-slots, whose slots in the cheek need material on both
#   sides to be closed;
# * the back plate's inner face is the module depth -- the same meaning the
#   setting has on a U-channel frame -- and it covers the ends of base and
#   top. The corners there are butt joints: both plates are already held by
#   both cheeks.

#: The sheet a laser frame is cut from when the scene names none: 5 mm, the
#: thickness the acrylic side panels always were.
LASER_MATERIAL = Material(name="plywood-5mm", thickness=5.0, kerf=0.15,
                          color="#c8a165")

#: Air between a base or top plate and the outer edge of the nearest rail.
#: The plate runs behind the rails, so this is not about the rail: it keeps
#: the plate out of the space a module's PCB hangs into.
LASER_RAIL_GAP = 0.5


def laser_keep_clear(t: float) -> float:
    """Sheet kept between a joint cut and a cheek's M5 hole: half a sheet,
    the same web every closed slot in a joint is held to."""
    return t / 2


def laser_min_margin(t: float, joint: str = "tslot",
                     overhang: float = RAIL_OVERHANG,
                     clearance: float = 0.1) -> float:
    """The smallest panel margin that fits a base or top plate.

    A margin is measured from the body's edge, which the rail overhangs by
    `overhang`. Two things have to fit in it, and the larger wins:

    * the plate: its outer face at the cheek's edge (finger) or a sheet inside
      it (T-slot), its inner face clear of the rail's outer edge by
      `LASER_RAIL_GAP`;
    * the joint's cuts in the cheek, which reach a sheet (plus half the
      clearance) in from the plate's outer face, and must stay
      `laser_keep_clear` away from the nearest M5 hole -- which sits at the
      rail's mid-height, just above that edge.
    """
    inset = t if joint == "tslot" else 0.0
    plate = overhang + inset + t + LASER_RAIL_GAP
    hole_low = (RAIL_SLOT_C - overhang) - M5_CLEAR / 2   # hole bottom - margin
    cuts = inset + t + clearance / 2 + laser_keep_clear(t) - hole_low
    return max(plate, cuts)


@dataclass
class FrameJoint:
    """One joint of a laser frame: which plate, which cheek, and the record."""

    plate: str
    side: Literal["left", "right"]
    joint: object                    # joinery.Joint

    def bom(self) -> dict[str, int]:
        return self.joint.bom()


def _build_laser(rows, hp, inserts_per_row, overhang, plan, module_depth,
                 material: Material, joint: str, top_plate: bool,
                 fastener: str) -> RackModel:
    from . import joinery as jn

    if joint not in ("tslot", "finger"):
        raise ValueError(f"unknown joint {joint!r}: 'tslot' or 'finger'")
    if inserts_per_row:
        raise ValueError(
            "side panel inserts are not available on a laser-cut frame: the "
            "back plate closes the slot a module would slide in through")
    t = material.thickness
    inset = t if joint == "tslot" else 0.0

    # Margins rise to fit the plates, the way they already rise to keep the
    # M5s on the panel; the cheek's notes say so.
    q = plan.resolved(overhang)
    need = laser_min_margin(t, joint, overhang, material.joint_clearance)
    raised = [f"{edge} margin raised from {v:.2f} to {need:.2f} mm to fit the "
              f"{t:g} mm {name} plate under the rails"
              for edge, v, name, wanted in (("bottom", q.bottom, "base", True),
                                            ("top", q.top, "top", top_plate))
              if wanted and v < need]
    q = replace(q, bottom=max(q.bottom, need),
                top=max(q.top, need) if top_plate else q.top)
    if joint == "finger":
        square = [n for n, v in (("back", q.back),
                                 ("r_bottom_back", q.r_bottom_back),
                                 ("r_top_back", q.r_top_back)) if v > 0]
        if square:
            raise ValueError(
                f"finger joints are cut into the cheek's own edges, so its "
                f"back edge has to be flush and its back corners square: set "
                f"{', '.join(square)} to 0, or use T-slots")

    width = frame_width(hp)
    web = web_height(rows, overhang)
    ph = panel_height(rows, overhang, q)
    placed = layout_rows(rows, overhang)
    base = panel_base(rows, overhang, q)       # world y of the cheeks' bottom
    cheek, holes = side_panel_section(rows, hp, 0, 4.0, overhang, q,
                                      module_depth,
                                      depth=module_depth + t + inset)
    keep = tuple(Point(*h.at).buffer(h.diameter / 2, quad_segs=12)
                 for h in holes)

    f = q.front
    wt = width + 2 * t                          # plates run to the outer faces
    y_lo, y_hi = inset, ph - inset              # base and top outer faces
    z_back = f + module_depth + t               # back plate's outer face

    # Base and top run from where the cheek's front corner radius ends to
    # the back plate. The raised margin already keeps them clear of the
    # rails, and the joints keep clear of the M5s on their own.
    def front(r: float) -> float:
        return max(f, r) - f                    # world z where a plate starts

    # Each plate in its own frame: u across the width (0 at the left cheek's
    # outer face), v along the joint. `trim` shortens the joint at either end
    # by a sheet where another plate's joint meets it in the cheek -- only a
    # T-slot needs this, its slots want a web of cheek all round -- and the
    # plate is notched there so it does not stand in the cheek.
    specs = []
    zb = front(q.r_bottom_front)
    specs.append(("base", module_depth - zb, (0.0, 0.0),
                  jn.Segment((f + zb, y_lo), (f + module_depth, y_lo), "left"),
                  shapely_box(0.0, 0.0, module_depth - zb, t),
                  (-t, base + y_lo, zb)))
    if top_plate:
        zt = front(q.r_top_front)
        specs.append(("top", module_depth - zt, (0.0, 0.0),
                      jn.Segment((f + zt, y_hi), (f + module_depth, y_hi),
                                 "right"),
                      shapely_box(0.0, 0.0, module_depth - zt, t),
                      (-t, base + y_hi - t, zt)))
    back_h = y_hi - y_lo
    lo_trim = t if joint == "tslot" else 0.0
    hi_trim = t if (joint == "tslot" and top_plate) else 0.0
    specs.append(("back", back_h, (lo_trim, hi_trim),
                  jn.Segment((z_back, y_lo + lo_trim),
                             (z_back, y_hi - hi_trim), "left"),
                  shapely_box(0.0, 0.0, t, back_h),
                  (-t, base + y_lo, module_depth)))

    make = jn.tslot_joint if joint == "tslot" else jn.finger_joint
    kw = {"fastener": fastener} if joint == "tslot" else {}
    cheeks = {s: jn.Plate(cheek, material, keep, laser_keep_clear(t))
              for s in ("left", "right")}
    joints: list[FrameJoint] = []
    cut: dict[str, Polygon] = {}
    for name, length, (t0, t1), cheek_seg, _section, _origin in specs:
        geom = shapely_box(0.0, 0.0, wt, length)
        for u0 in (0.0, wt - t):
            if t0:
                geom = geom.difference(shapely_box(u0, 0.0, u0 + t, t0))
            if t1:
                geom = geom.difference(
                    shapely_box(u0, length - t1, u0 + t, length))
        plate = jn.Plate(geom, material)
        for side, own in (
                ("left", jn.Segment((0.0, t0), (0.0, length - t1), "right")),
                ("right", jn.Segment((wt, t0), (wt, length - t1), "left"))):
            try:
                cheeks[side], plate, j = make(
                    jn.Edge(cheek_seg, own), cheeks[side], plate, **kw)
            except jn.JointError as exc:
                raise ValueError(
                    f"the {name} plate cannot be joined to the {side} cheek: "
                    f"{exc}. A deeper frame, thinner sheet or finger joints "
                    f"give it more room.") from None
            joints.append(FrameJoint(name, side, j))
        cut[name] = plate.geom

    members = _rails(placed, width, overhang)
    plate_names = ", ".join(s[0] for s in specs)
    for side, x in (("left", -t), ("right", width)):
        members.append(Member(
            name=f"side-{side}", kind="side_panel", section=cheeks[side].geom,
            length=t, origin=(x, base, -f), material=material.name,
            holes=list(holes), stock=material,
            notes=[f"{len(holes)} x M5 into rail ends",
                   f"{joint} joints to the {plate_names} plates", *raised]))
    for name, _length, _trim, _seg, section, origin in specs:
        members.append(Member(
            name=name, kind="plate", section=section, length=wt,
            origin=origin, material=material.name, cut=cut[name],
            stock=material,
            notes=[f"{t:g} mm {material.name}, {joint} joints into both "
                   f"cheeks"]))

    return RackModel(rows=placed, members=members, hp=hp, width=width,
                     web=web, panel_height=ph, overhang=overhang, plan=q,
                     module_depth=module_depth, style="laser", panel_t=t,
                     joints=joints, min_margin=need, joint=joint)


def build_spec(spec) -> RackModel:
    """Build from a `schema.RackSpec`, resolving row format names."""
    from .eurorack import ROW_FORMATS

    rows: list[RowFormat] = []
    for r in spec.rows:
        fmt = ROW_FORMATS.get(r.format)
        if fmt is None:
            raise ValueError(
                f"unknown row format {r.format!r}; "
                f"known: {', '.join(sorted(ROW_FORMATS))}")
        rows.append(fmt)
    if not rows:
        raise ValueError("a frame needs at least one row")
    # The overhang is deliberately not taken from the spec: it belongs to the
    # rail, and there is only one rail.
    panel = getattr(spec, "panel", None)
    plan = PanelPlan(
        top=getattr(panel, "top", PANEL_MARGIN),
        bottom=getattr(panel, "bottom", PANEL_MARGIN),
        front=getattr(panel, "front", 0.0),
        back=getattr(panel, "back", 0.0),
        r_bottom_front=getattr(panel, "r_bottom_front", 0.0),
        r_bottom_back=getattr(panel, "r_bottom_back", 0.0),
        r_top_back=getattr(panel, "r_top_back", 0.0),
        r_top_front=getattr(panel, "r_top_front", 0.0),
    ) if panel is not None else PanelPlan()
    return build(rows, spec.hp, spec.inserts_per_row, spec.insert_hp,
                 RAIL_OVERHANG, plan,
                 getattr(spec, "module_depth", MODULE_DEPTH),
                 style=getattr(spec, "style", "u_channel"),
                 material=getattr(spec, "material", None),
                 joint=getattr(spec, "joint", "tslot"),
                 top_plate=getattr(spec, "top_plate", True),
                 fastener=getattr(spec, "fastener", "M3"))
