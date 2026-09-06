"""The Eurorack format as arithmetic.

Everything here is the *format*, not a part -- the numbers that are true of every
Eurorack case whoever built it. Parts get measured; these get derived. The prose
version, with sources, is `docs/eurorack.md`.

Two constants do almost all the work:

* **HP = 5.08 mm** is the horizontal grid. Width is always `n * HP`.
* **U = 44.45 mm** is the rack unit. Height is always a whole number of these.

Everything else is a deduction from one of those two, and the deductions are the
part people get wrong. A 3U panel is not 3 * 44.45 = 133.35 tall; it is 128.5,
because a panel has to fit *inside* its rack allocation. The mounting holes are
not at the panel's edges; they are 3.0 mm in, which is why the hole span is
122.5 rather than 128.5. Each of those is a named constant below rather than a
magic number at a call site, because every one of them has cost somebody a
panel.

Coordinates, where this module implies any: X is width (the HP axis), Y is
height (the U axis), Z is depth into the case.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# the two grids
# --------------------------------------------------------------------------

#: horizontal pitch: 1 HP, in mm. 0.2 inch.
HP = 5.08

#: rack unit: 1 U, in mm. The vertical grid everything else is cut out of.
RACK_U = 44.45

# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

#: A 3U panel is this tall. NOT 3 * RACK_U -- see the module docstring.
PANEL_3U = 128.5

#: How far a panel's rack allocation exceeds the panel itself: 133.35 - 128.5.
#: The gap the rails and the row clearance live in.
PANEL_DEDUCT = 3 * RACK_U - PANEL_3U          # 4.85

#: Mounting hole centre, in from the panel's top and bottom edges.
HOLE_INSET = 3.0

#: Distance between a 3U panel's two hole centres: 128.5 - 2 * 3.0.
SLOT_SPAN_3U = PANEL_3U - 2 * HOLE_INSET      # 122.5

#: Hole span as a deduction from the rack allocation. For 3U:
#: 133.35 - 10.85 = 122.5. Holds for any U, which is what makes `slot_span`
#: a one-liner rather than a table.
SLOT_DEDUCT = 3 * RACK_U - SLOT_SPAN_3U       # 10.85

#: Half of it -- how far the outermost slot centre sits inside its rack boundary.
SLOT_EDGE_INSET = SLOT_DEDUCT / 2             # 5.425

#: Panel mounting hardware.
HOLE_D = 3.2                                  # M3 clearance
SCREW = "M3"

# --------------------------------------------------------------------------
# row formats
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RowFormat:
    """One horizontal row's vertical format.

    `u` is what the row costs in rack units -- the only number that matters for
    stacking. `panel` is how tall its panels actually are, which is smaller, and
    is only needed when drawing a module rather than placing a rail.
    """

    name: str
    u: float
    panel: float

    @property
    def slot_span(self) -> float:
        """Centre-to-centre distance between this row's two rail slots."""
        return self.u * RACK_U - SLOT_DEDUCT


#: The standard 3U row. Everything else in Eurorack is an accessory to this.
ROW_3U = RowFormat("3U", 3.0, PANEL_3U)

#: The two mutually incompatible 1U tile standards. There is no neutral "1U":
#: a Pulp Logic tile in a lipped rail pushes the row past 44.45 and the case
#: stops closing. See docs/eurorack.md section 7.
ROW_1U_INTELLIJEL = RowFormat("1U-intellijel", 1.0, 39.65)
ROW_1U_PULPLOGIC = RowFormat("1U-pulplogic", 1.0, 43.18)

ROW_FORMATS = {f.name: f for f in (ROW_3U, ROW_1U_INTELLIJEL, ROW_1U_PULPLOGIC)}

# --------------------------------------------------------------------------
# widths
# --------------------------------------------------------------------------

#: Doepfer's published front panel widths. The key is HP; the value is the
#: actual panel width, which is a few tenths under `hp * HP` so that panels do
#: not bind against each other when a row is assembled.
#:
#: There is no closed form: the deduction wanders between 0.08 and 0.48 mm.
#: The widely repeated `hp * 5.08 - 0.3` is a bad fit that cuts *too wide* on
#: 11 of these 16 sizes, worst at 6 HP.
PANEL_WIDTHS = {
    1: 5.00, 1.5: 7.50, 2: 9.80, 4: 20.00, 6: 30.00, 8: 40.30,
    10: 50.50, 12: 60.60, 14: 70.80, 16: 80.90, 18: 91.30, 20: 101.30,
    21: 106.30, 22: 111.40, 28: 141.90, 42: 213.00,
}

#: Largest deduction in that table (6 HP). Anything at least this big is a
#: width no published panel exceeds, which is what makes the fallback safe.
_MAX_PANEL_DEDUCT = 0.5


def panel_width(hp: float) -> float:
    """Width of a *module panel* of `hp`, in mm.

    Uses Doepfer's table where it has an entry and a never-over-cut fallback
    where it does not. This is for modules only -- a frame that holds them uses
    `frame_width`.
    """
    if hp in PANEL_WIDTHS:
        return PANEL_WIDTHS[hp]
    return hp * HP - _MAX_PANEL_DEDUCT


def frame_width(hp: float) -> float:
    """Width of the *frame* that holds `hp` of modules, in mm -- nominal grid.

    Deliberately not `panel_width`. That table's deduction is per-module
    assembly clearance, and applying it to the rail would make the case
    narrower than the modules it has to hold. Suppliers agree: f&w cut their
    rails at 84 TE = 427 mm, which is `84 * 5.08 = 426.72` rounded up, not
    426.72 minus anything.
    """
    return hp * HP


def hp_of(width_mm: float) -> float:
    """How many HP a width is. Inverse of `frame_width`."""
    return width_mm / HP


# --------------------------------------------------------------------------
# heights
# --------------------------------------------------------------------------


def slot_span(u: float) -> float:
    """Centre-to-centre distance between the outermost rail slots over `u`.

    3U -> 122.5, which is the number every Eurorack panel is drilled to.
    """
    return u * RACK_U - SLOT_DEDUCT


def rack_height(rows: list[RowFormat]) -> float:
    """Total rack allocation of a stack of rows, in mm."""
    return sum(r.u for r in rows) * RACK_U
