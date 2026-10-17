"""The frame generator, held against the one case that physically exists.

The 5U case in `docs/eurorack-frame-stock.md` is the only ground truth this
pipeline has, so most of what is asserted here is "does the arithmetic still
land on the thing somebody actually built". It should, to about a millimetre --
the builder worked in whole millimetres and the model works in rack units.
"""

from __future__ import annotations

import pytest

from hwcase import rack
from hwcase.eurorack import (HP, PANEL_WIDTHS, ROW_1U_INTELLIJEL, ROW_3U,
                             frame_width, panel_width, slot_span)

# The built 5U case: 1U + 3U + 1U, 74 HP.
BUILT_ROWS = [ROW_1U_INTELLIJEL, ROW_3U, ROW_1U_INTELLIJEL]
BUILT_M5_YS = [12.0, 47.0, 57.0, 180.0, 190.0, 225.0]
BUILT_WEB = 216.0
BUILT_LENGTH = 376.0

#: How far the generator may sit from the built case. The builder rounded to
#: whole millimetres across 5U; 1.5 mm is that rounding, and a sliding nut
#: swallows it.
TOL = 1.5

#: The built case stands on its side panels, and that is now the default, so
#: reproducing it takes no extra arguments. Kept as a name so the tests that
#: mean "the case that exists" still say so.
BUILT = {}

#: Both height margins at zero: the panel stops level with the body and the
#: rails are the only thing underneath. Clamped up to whatever the M5 holes
#: need, which is the point of several of these tests.
ON_RAILS = {"plan": rack.PanelPlan(top=0.0, bottom=0.0)}


# --------------------------------------------------------------------------
# the format itself
# --------------------------------------------------------------------------


def test_slot_span_3u_is_the_universal_122_5():
    assert slot_span(3) == pytest.approx(122.5)


def test_frame_width_is_nominal_not_the_panel_table():
    """The frame uses the grid; only module panels get the clearance deduction.

    f&w cut 84 TE at 427 mm, which is 426.72 rounded up -- not reduced.
    """
    assert frame_width(84) == pytest.approx(426.72)
    assert round(frame_width(84)) == 427
    assert frame_width(68) == pytest.approx(345.44)
    assert frame_width(8) > panel_width(8)


def test_panel_width_never_exceeds_a_published_width():
    for hp, published in PANEL_WIDTHS.items():
        assert panel_width(hp) <= published + 1e-9


def test_panel_width_fallback_never_over_cuts():
    """An HP with no table entry must still fit its slot."""
    for hp in (3, 5, 7, 9, 11, 13, 24, 30):
        assert panel_width(hp) < hp * HP


# --------------------------------------------------------------------------
# the built case
# --------------------------------------------------------------------------


def test_web_reproduces_the_built_5u_case_exactly():
    assert rack.web_height(BUILT_ROWS) == pytest.approx(BUILT_WEB, abs=0.01)


def test_side_panel_outline_matches_the_built_blank():
    """237 x 52, the acrylic that was actually ordered."""
    panel, _ = rack.side_panel_section(BUILT_ROWS, 74.0, inserts_per_row=1)
    x0, y0, x1, y1 = panel.bounds
    assert (x1 - x0) == pytest.approx(52.0, abs=0.01)
    assert (y1 - y0) == pytest.approx(237.0, abs=TOL)


def test_panel_height_wraps_the_body_plus_its_margin():
    m = rack.build(BUILT_ROWS, 74.0, **BUILT)
    assert m.panel_height == pytest.approx(
        m.web + m.plan.top + m.plan.bottom, abs=1e-9)
    assert m.panel_height == pytest.approx(237.0, abs=0.01)

    # Asking for 0 gets the smallest margin the M5 holes allow, not 0.
    flush = rack.build(BUILT_ROWS, 74.0, **ON_RAILS)
    lo = rack.min_panel_margin(flush.overhang)
    assert flush.margin == pytest.approx(lo)
    assert flush.panel_height == pytest.approx(flush.web + 2 * lo, abs=1e-9)
    assert flush.stands_on == "rails"


def test_rail_length_matches_the_ordered_profile():
    m = rack.build(BUILT_ROWS, 74.0)
    assert m.width == pytest.approx(BUILT_LENGTH, abs=0.1)


def test_m5_pattern_reproduces_the_built_side_panel():
    """Six holes, in the built positions, derived rather than tabulated."""
    m = rack.build(BUILT_ROWS, 74.0, **BUILT)
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    ys = sorted(h.at[1] for h in panel.holes)
    assert len(ys) == len(BUILT_M5_YS)
    for got, built in zip(ys, BUILT_M5_YS):
        assert got == pytest.approx(built, abs=TOL)


def test_one_m5_per_rail_end_and_two_rails_per_row():
    m = rack.build(BUILT_ROWS, 74.0)
    assert len(m.rails) == 2 * len(BUILT_ROWS)
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    assert len(panel.holes) == len(m.rails)
    assert m.bill_of_materials()["M5 screws"] == 2 * len(m.rails)


def test_there_is_exactly_one_body_however_tall_the_case():
    for rows in ([ROW_3U], BUILT_ROWS, [ROW_3U, ROW_3U], [ROW_3U] * 3):
        m = rack.build(rows, 68.0)
        assert sum(1 for x in m.members if x.kind == "body") == 1


# --------------------------------------------------------------------------
# the 68 HP 6U target
# --------------------------------------------------------------------------


def test_68hp_6u_target():
    # In the built case's own datum, so the hole run is comparable with the
    # 5U panel it was predicted from.
    m = rack.build([ROW_3U, ROW_3U], 68.0, inserts_per_row=1, **BUILT)
    assert m.width == pytest.approx(345.44)
    assert m.web == pytest.approx(260.45, abs=0.01)
    assert len(m.rails) == 4

    panel = [x for x in m.members if x.kind == "side_panel"][0]
    ys = sorted(h.at[1] for h in panel.holes)
    for got, want in zip(ys, [12.0, 135.0, 145.0, 268.0]):
        assert got == pytest.approx(want, abs=TOL)


def test_every_row_spans_its_own_rack_allocation():
    m = rack.build([ROW_1U_INTELLIJEL, ROW_3U, ROW_3U], 68.0)
    for row in m.rows:
        assert row.span == pytest.approx(slot_span(row.fmt.u), abs=1e-9)


def test_mixed_and_pure_stacks_of_equal_u_agree():
    """1U+3U+1U and any other 5U arrangement give the same body."""
    a = rack.web_height(BUILT_ROWS)
    b = rack.web_height([ROW_1U_INTELLIJEL] * 2 + [ROW_3U])
    assert a == pytest.approx(b)


# --------------------------------------------------------------------------
# the insert slots
# --------------------------------------------------------------------------


def test_insert_slot_is_four_hp_and_breaks_the_edge():
    plain, _ = rack.side_panel_section([ROW_3U], 68.0, inserts_per_row=0)
    cut, _ = rack.side_panel_section([ROW_3U], 68.0, inserts_per_row=1)
    assert cut.area < plain.area

    removed = plain.area - cut.area
    expected = 4 * HP * (ROW_3U.panel - rack.PCB_SHORTFALL)
    assert removed == pytest.approx(expected, rel=0.02)

    # breaking the edge means the outline is no longer a plain rectangle
    assert len(cut.exterior.coords) > len(plain.exterior.coords)


def test_inserts_do_not_change_the_rail_span():
    """A side-panel slot adds capacity outboard; it must not widen the frame."""
    a = rack.build([ROW_3U], 68.0, inserts_per_row=0)
    b = rack.build([ROW_3U], 68.0, inserts_per_row=1)
    assert a.width == b.width


def test_one_u_rows_get_no_insert_slot():
    """A 1U tile row is too short to swallow a 3U module sideways."""
    cut, _ = rack.side_panel_section([ROW_1U_INTELLIJEL], 68.0,
                                     inserts_per_row=1)
    plain, _ = rack.side_panel_section([ROW_1U_INTELLIJEL], 68.0,
                                       inserts_per_row=0)
    assert cut.area == pytest.approx(plain.area)


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_build_is_deterministic():
    a = rack.build(BUILT_ROWS, 74.0, inserts_per_row=1)
    b = rack.build(BUILT_ROWS, 74.0, inserts_per_row=1)
    assert [m.name for m in a.members] == [m.name for m in b.members]
    assert [m.length for m in a.members] == [m.length for m in b.members]
    assert [m.origin for m in a.members] == [m.origin for m in b.members]


# --------------------------------------------------------------------------
# how the pieces actually fit together
# --------------------------------------------------------------------------


def _bounds(member):
    """(depth0, height0, depth1, height1) in world terms."""
    d0, h0, d1, h1 = member.section.bounds
    return (member.origin[2] + d0, member.origin[1] + h0,
            member.origin[2] + d1, member.origin[1] + h1)


def test_the_body_flange_seats_inside_the_rail_slot():
    """The point of the sheet slot: the flange has to land in it.

    Checked at both ends, because the upper rail is the same extrusion
    mirrored and a sign error there is invisible until something is cut.
    """
    m = rack.build([ROW_3U], 68.0)
    body = [x for x in m.members if x.kind == "body"][0]
    rails = sorted((x for x in m.members if x.kind == "rail"),
                   key=lambda r: r.origin[1])

    bd0, bh0, bd1, bh1 = _bounds(body)
    for rail, flange_lo, flange_hi in (
            (rails[0], bh0, bh0 + rack.BODY_T),
            (rails[-1], bh1 - rack.BODY_T, bh1)):
        # the slot is the notch in the rail's back face; find its extent
        rd0, rh0, rd1, rh1 = _bounds(rail)
        assert rh0 <= flange_lo + 1e-9 and flange_hi <= rh1 + 1e-9, (
            "flange sits outside the rail's height")
        # and the rail's back reaches past the body's front, or they never meet
        assert rd1 > bd0 + 1e-9, "rail and body do not overlap in depth"


def test_the_body_back_is_the_back_of_the_case():
    m = rack.build([ROW_3U], 68.0, **BUILT)
    body = [x for x in m.members if x.kind == "body"][0]
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    assert _bounds(body)[2] == pytest.approx(_bounds(panel)[2], abs=1e-9)


def test_it_stands_on_the_rails_only_when_the_margin_is_under_the_overhang():
    """The trade-off, pinned down.

    Standing on aluminium means the panel stops short of the rail, and the M5
    sits 5 mm inside the rail's edge -- so there is barely any acrylic left
    below the hole. The default takes the other side of that trade.
    """
    on_rails = rack.build([ROW_3U], 68.0, **ON_RAILS)
    rail = min(x.origin[1] for x in on_rails.members if x.kind == "rail")
    panel = min(x.origin[1] for x in on_rails.members if x.kind == "side_panel")
    body = min(x.origin[1] for x in on_rails.members if x.kind == "body")
    assert rail < panel and rail < body
    assert on_rails.stands_on == "rails"

    default = rack.build([ROW_3U], 68.0)
    assert default.margin > default.overhang
    assert default.stands_on == "side panels"


def test_zero_overhang_puts_the_rail_level_with_the_body():
    m = rack.build([ROW_3U], 68.0, overhang=0.0)
    rail = min(x.origin[1] for x in m.members if x.kind == "rail")
    body = min(x.origin[1] for x in m.members if x.kind == "body")
    assert rail == pytest.approx(body, abs=1e-9)


def test_side_panels_sit_outside_the_rails():
    """A rail spans the width exactly; the panels bolt to its ends."""
    m = rack.build([ROW_3U], 68.0)
    left, right = [x for x in m.members if x.kind == "side_panel"]
    assert left.origin[0] + left.length == pytest.approx(0.0)
    assert right.origin[0] == pytest.approx(m.width)
    assert m.outer_width == pytest.approx(m.width + 2 * rack.SIDE_PANEL_T)


def test_upper_rail_is_the_lower_one_mirrored():
    lo = rack.rail_section(flip=False)
    hi = rack.rail_section(flip=True)
    assert lo.area == pytest.approx(hi.area)
    assert lo.bounds == pytest.approx(hi.bounds)
    # ...but not the same shape, or the mirror did nothing
    assert lo.symmetric_difference(hi).area > 0.1


def test_m5_holes_are_interior_rings_at_every_margin():
    """The bug that turned the frame into an unrenderable blob.

    An M5 hole sits only `body_edge_inset` above the body's edge, which is
    less than its own radius. Let the panel stop level with the body and the
    holes run off it: the outline stops being a rectangle with holes in it and
    becomes a comb, which is neither cuttable nor renderable. The margin is
    clamped so that can't happen, whatever is asked for.
    """
    for overhang in (0.0, 1.0, 2.7, 5.0):
        for asked in (None, 0.0, 1.0, rack.BUILT_PANEL_MARGIN):
            plan = (rack.PanelPlan() if asked is None
                    else rack.PanelPlan(top=asked, bottom=asked))
            poly, holes = rack.side_panel_section(
                BUILT_ROWS, 74.0, overhang=overhang, plan=plan)
            assert len(poly.interiors) == len(holes), (
                f"holes broke the outline at overhang={overhang} "
                f"margin={asked}")


def test_margins_are_raised_to_what_the_holes_need():
    for overhang in (0.0, 2.7, 5.0):
        lo = rack.min_panel_margin(overhang)
        q = rack.PanelPlan(top=0.0, bottom=0.0).resolved(overhang)
        assert q.top == pytest.approx(lo) and q.bottom == pytest.approx(lo)
        big = rack.PanelPlan(top=99.0, bottom=99.0).resolved(overhang)
        assert big.top == 99.0 and big.bottom == 99.0


def test_each_panel_edge_is_independent():
    plan = rack.PanelPlan(top=4.0, bottom=20.0, front=6.0, back=3.0)
    m = rack.build([ROW_3U], 68.0, plan=plan)
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    d0, h0, d1, h1 = panel.section.bounds
    assert (d1 - d0) == pytest.approx(rack.case_depth() + 6.0 + 3.0)
    assert (h1 - h0) == pytest.approx(m.web + 4.0 + 20.0)


def test_corner_radii_are_per_corner():
    square = rack.side_panel_section([ROW_3U], 68.0)[0]
    round1 = rack.side_panel_section(
        [ROW_3U], 68.0, plan=rack.PanelPlan(r_bottom_front=10.0))[0]
    round2 = rack.side_panel_section(
        [ROW_3U], 68.0,
        plan=rack.PanelPlan(r_bottom_front=10.0, r_top_back=10.0))[0]
    # each rounded corner removes about r^2*(1 - pi/4)
    bite = 10.0 ** 2 * (1 - 3.14159265 / 4)
    assert square.area - round1.area == pytest.approx(bite, rel=0.02)
    assert round1.area - round2.area == pytest.approx(bite, rel=0.02)


def test_a_radius_larger_than_its_edge_is_refused():
    with pytest.raises(ValueError, match="exceed"):
        rack.side_panel_section(
            [ROW_3U], 68.0, plan=rack.PanelPlan(r_bottom_front=500.0))


def test_the_overhang_cannot_exceed_the_rail_slot():
    """It is the extrusion, not a setting -- past the slot it is nonsense."""
    with pytest.raises(ValueError, match="not adjustable"):
        rack.body_edge_inset(10.0)


# --------------------------------------------------------------------------
# does it actually fit together
# --------------------------------------------------------------------------
#
# These reconstruct each member's cross-section in the case's own (depth,
# height) plane and ask shapely whether the pieces collide. Every one of them
# exists because a number that was invented rather than derived put two solid
# things in the same place, and the only way that surfaced was somebody
# looking closely at a render.

from shapely.affinity import translate                    # noqa: E402
from shapely.geometry import Point                        # noqa: E402

CONFIGS = [
    ([ROW_3U], 68.0, {}),
    ([ROW_3U, ROW_3U], 68.0, {}),
    (BUILT_ROWS, 74.0, BUILT),
    ([ROW_3U, ROW_3U], 68.0, ON_RAILS),
    ([ROW_3U], 68.0, {"plan": rack.PanelPlan(top=4, bottom=20, front=6, back=3,
                                             r_bottom_front=8, r_top_back=5)}),
    ([ROW_3U], 84.0, {"overhang": 0.0}),
    ([ROW_3U], 84.0, {"overhang": 5.0}),      # the physical maximum
]


def _world(member):
    """The member's cross-section placed in the case's (depth, height) plane."""
    return translate(member.section,
                     xoff=member.origin[2], yoff=member.origin[1])


def _screw_circles(model, diameter):
    """Every M5, as a circle in the same plane the sections live in."""
    panel = [m for m in model.members if m.kind == "side_panel"][0]
    # hole coordinates are panel-local; a front overhang shifts the panel's
    # own origin, so both offsets have to come back in
    return [Point(h.at[0] + panel.origin[2], h.at[1] + panel.origin[1])
            .buffer(diameter / 2, quad_segs=32) for h in panel.holes]


@pytest.mark.parametrize("rows,hp,kw", CONFIGS)
def test_no_m5_passes_through_the_body(rows, hp, kw):
    """The screw and the folded sheet cannot share millimetres.

    The bore used to sit at the back of the rail, which is exactly where the
    sheet slot and the flange behind it are.
    """
    m = rack.build(rows, hp, **kw)
    body = _world([x for x in m.members if x.kind == "body"][0])
    for circle in _screw_circles(m, rack.M5_CLEAR):
        assert not circle.intersects(body), "an M5 runs through the body"


@pytest.mark.parametrize("rows,hp,kw", CONFIGS)
def test_every_bore_is_inside_its_rail(rows, hp, kw):
    """A bore that leaves the extrusion is a screw with nothing to bite."""
    m = rack.build(rows, hp, **kw)
    rails = [_world(x) for x in m.members if x.kind == "rail"]
    for circle in _screw_circles(m, rack.RAIL_END_BORE):
        assert any(r.contains(circle) for r in rails), (
            "an M5 bore is not contained by any rail")


@pytest.mark.parametrize("rows,hp,kw", CONFIGS)
def test_rails_do_not_collide_with_each_other(rows, hp, kw):
    """Adjacent rows sit 10.85 mm apart and the rail is 10.00 tall."""
    m = rack.build(rows, hp, **kw)
    rails = sorted((x for x in m.members if x.kind == "rail"),
                   key=lambda r: r.origin[1])
    for a, b in zip(rails, rails[1:]):
        assert a.origin[1] + rack.RAIL_H <= b.origin[1] + 1e-9, (
            "two rails overlap")


@pytest.mark.parametrize("rows,hp,kw", CONFIGS)
def test_the_body_never_collides_with_a_rail_outside_its_slot(rows, hp, kw):
    """Overlap is allowed only where the sheet slot is."""
    m = rack.build(rows, hp, **kw)
    body = _world([x for x in m.members if x.kind == "body"][0])
    for rail in (x for x in m.members if x.kind == "rail"):
        shared = _world(rail).intersection(body)
        assert shared.area < 0.05, (
            f"{rail.name} and the body overlap by {shared.area:.3f} mm2")


@pytest.mark.parametrize("rows,hp,kw", CONFIGS)
def test_every_member_is_inside_the_side_panel_envelope(rows, hp, kw):
    """Nothing may poke out past the panel except the rails, deliberately."""
    m = rack.build(rows, hp, **kw)
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    pd0, ph0, pd1, ph1 = _world(panel).bounds
    for x in m.members:
        if x.kind == "side_panel":
            continue
        d0, h0, d1, h1 = _world(x).bounds
        assert d0 >= pd0 - 1e-9 and d1 <= pd1 + 1e-9, (
            f"{x.name} sticks out of the panel in depth")
        if x.kind == "rail":
            continue          # rails are allowed past it in height
        assert h0 >= ph0 - 1e-9 and h1 <= ph1 + 1e-9, (
            f"{x.name} sticks out of the panel in height")


# --------------------------------------------------------------------------
# module depth
# --------------------------------------------------------------------------


def test_module_depth_reproduces_the_built_flange():
    """51 mm of module room is the 40 mm channel that was ordered."""
    assert rack.body_flange(51.0) == pytest.approx(40.0)
    assert rack.case_depth(51.0) == pytest.approx(52.0)
    m = rack.build([ROW_3U], 74.0, module_depth=51.0)
    assert m.order_spec()["width_a_mm"] == pytest.approx(40.0)


@pytest.mark.parametrize("depth", [25.0, 40.0, 51.0, 80.0, 120.0])
def test_depth_drives_body_and_panels_together(depth):
    m = rack.build([ROW_3U, ROW_3U], 68.0, module_depth=depth)
    body = [x for x in m.members if x.kind == "body"][0]
    panel = [x for x in m.members if x.kind == "side_panel"][0]
    # the body's flange grows with the depth...
    assert body.section.bounds[2] == pytest.approx(rack.body_flange(depth))
    # ...and the panel wraps exactly the case it belongs to
    assert panel.section.bounds[2] == pytest.approx(rack.case_depth(depth))
    # the back of the body is still the back of the case
    assert (body.origin[2] + body.section.bounds[2]
            == pytest.approx(rack.case_depth(depth)))


@pytest.mark.parametrize("depth", [25.0, 51.0, 120.0])
def test_the_rail_engagement_never_changes_with_depth(depth):
    """The flange enters the slot by the same 7 mm however deep the case is."""
    m = rack.build([ROW_3U], 68.0, module_depth=depth)
    body = [x for x in m.members if x.kind == "body"][0]
    rail = [x for x in m.members if x.kind == "rail"][0]
    overlap = (rail.origin[2] + rail.section.bounds[2]) - body.origin[2]
    assert overlap == pytest.approx(rack.RAIL_SLOT_DEPTH)
    # and the M5 bore stays clear of it
    assert rack.rail_bore_depth() + rack.RAIL_END_BORE / 2 < body.origin[2]


def test_a_case_too_shallow_for_the_rail_is_refused():
    with pytest.raises(ValueError, match="shorter than"):
        rack.build([ROW_3U], 68.0, module_depth=15.0)


# --------------------------------------------------------------------------
# the frame's cut: plates on sheets, and a cut list for everything else
# --------------------------------------------------------------------------

import io                                                  # noqa: E402
from pathlib import Path                                   # noqa: E402

from shapely.affinity import rotate as _rotate            # noqa: E402
from shapely.geometry import Polygon                       # noqa: E402

from hwcase import export, flat                            # noqa: E402
from hwcase.case import kerf_compensated                   # noqa: E402
from hwcase.library import load_scene                      # noqa: E402
from hwcase.schema import CaseSpec                         # noqa: E402

FRAME_6 = Path(__file__).resolve().parent.parent / "scenes" / "frame-6.yaml"


def _frame_6():
    scene = load_scene(FRAME_6)
    return scene, rack.build_spec(scene.rack)


def test_frame_cut_list_names_plates_rails_m5s_and_the_order():
    scene, model = _frame_6()
    files = export.rack_sheet_files(model, "dxf", "frame-6", scene.case)
    names = [n for n, _ in files]
    assert names[-1] == "frame-6-cutlist.txt"
    assert len(names) >= 2, "at least one sheet besides the cut list"
    text = files[-1][1]

    for side in ("side-left", "side-right"):
        assert side in text
    rails = model.rails
    assert len(rails) == 4
    for r in rails:
        assert f"{r.name:<12} {r.length:8.2f} mm" in text
    assert f"{len(rails)} x {model.width:.2f} mm" in text
    assert f"{2 * len(rails)} x M5 screw" in text
    assert model.bill_of_materials()["M5 screws"] == 2 * len(rails)

    order = model.order_spec()
    assert "U-channel (order):" in text
    assert (f"length {order['length_mm']} mm, A / B / C "
            f"{order['width_a_mm']} / {order['width_b_mm']} / "
            f"{order['width_c_mm']} mm") in text


def test_the_cut_list_gives_each_m5_from_the_datum_edge():
    scene, model = _frame_6()
    text = export.rack_sheet_files(model, "svg", "f", scene.case)[-1][1]
    panel = next(m for m in model.members if m.kind == "side_panel")
    for h in panel.holes:
        assert f"{h.at[0]:7.2f} {h.at[1]:7.2f} mm  {h.note}" in text


def _dxf_hole_centres(text: str, layer: str) -> list[tuple[float, float]]:
    """Centres of the small closed rings on one DXF layer: the drilled holes."""
    import ezdxf

    doc = ezdxf.read(io.StringIO(text))
    out = []
    for e in doc.modelspace().query("LWPOLYLINE"):
        if e.dxf.layer != layer:
            continue
        ring = Polygon([(p[0], p[1]) for p in e.get_points()])
        x0, y0, x1, y1 = ring.bounds
        if max(x1 - x0, y1 - y0) < 2 * rack.M5_CLEAR:
            out.append((ring.centroid.x, ring.centroid.y))
    return out


def test_side_panel_hole_centres_in_the_dxf_match_the_model():
    """Holes that meet bought aluminium are held to 0.01 mm.

    Every side panel on every sheet: each M5 the model drills, carried
    through the placement the packer reports (turned if `rotated`, then the
    kerf-compensated plate's corner moved to `x, y`), has a ring in the DXF
    whose centre is within 0.01 mm of it -- and no ring is left over.
    """
    scene, model = _frame_6()
    parts = flat.from_rack(model)
    sheets = export.pack_sheets(parts, stock=scene.case)
    checked = 0
    for sheet in sheets:
        text = export.sheet_to_dxf_text(sheet)
        for pl in sheet.placements:
            part = pl.part
            ref = kerf_compensated(part.outline, part.material.kerf)
            if pl.rotated:
                ref = _rotate(ref, 90, origin=(0, 0))
            dx, dy = pl.x - ref.bounds[0], pl.y - ref.bounds[1]
            want = []
            for h in part.holes:
                x, y = h.at
                if pl.rotated:
                    x, y = -y, x
                want.append((x + dx, y + dy))
            got = _dxf_hole_centres(text, part.labels.cad_layer)
            assert len(got) == len(want) == len(model.rails)
            for wx, wy in want:
                best = min(((gx - wx) ** 2 + (gy - wy) ** 2) ** 0.5
                           for gx, gy in got)
                assert best <= 0.01, f"{part.name}: M5 off by {best:.4f} mm"
                checked += 1
    assert checked == 2 * len(model.rails)


def test_side_panel_hole_spacing_in_the_dxf_matches_the_model():
    """The same check without trusting the placement: every distance
    between two M5s on a cut panel is the model's distance."""
    from itertools import combinations

    scene, model = _frame_6()
    panel = next(m for m in model.members if m.kind == "side_panel")
    want = sorted(((a.at[0] - b.at[0]) ** 2 + (a.at[1] - b.at[1]) ** 2) ** 0.5
                  for a, b in combinations(panel.holes, 2))
    files = export.rack_sheet_files(model, "dxf", "f", scene.case)
    seen = 0
    for _name, text in files[:-1]:
        for layer in ("side-left", "side-right"):
            got = _dxf_hole_centres(text, layer)
            if not got:
                continue
            seen += 1
            dist = sorted(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                          for a, b in combinations(got, 2))
            assert dist == pytest.approx(want, abs=0.01)
    assert seen == 2, "both side panels are on the sheets"


def test_frame_sheets_are_svg_too():
    import xml.etree.ElementTree as ET

    scene, model = _frame_6()
    files = export.rack_sheet_files(model, "svg", "f", scene.case)
    body = "".join(c for _n, c in files[:-1])
    for _n, c in files[:-1]:
        ET.fromstring(c)
    assert 'id="side-left"' in body and 'id="side-right-outline"' in body
    assert 'data-role="side_panel"' in body


def test_a_frame_too_big_for_the_stock_is_a_clear_error():
    _scene, model = _frame_6()
    with pytest.raises(ValueError, match="side-left"):
        export.rack_sheet_files(model, "svg", "f",
                                CaseSpec(plates=[(120.0, 120.0)]))


def test_the_sheet_export_endpoint_takes_a_frame():
    from fastapi.testclient import TestClient

    from hwcase import api

    scene, model = _frame_6()
    client = TestClient(api.app)
    r = client.post("/api/export/sheets/dxf?as_json=true",
                    json=scene.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    got = r.json()
    names = [s["name"] for s in got["sheets"]]
    assert names[-1] == "frame-6-cutlist.txt"
    assert got["count"] == len(names) - 1 >= 1
    assert "U-channel (order):" in got["sheets"][-1]["content"]


def test_the_sheet_export_endpoint_reports_a_bad_frame():
    from fastapi.testclient import TestClient

    from hwcase import api

    scene, _model = _frame_6()
    bad = scene.model_dump(mode="json")
    bad["rack"]["rows"] = [{"format": "7U-nonsense"}]
    r = TestClient(api.app).post("/api/export/sheets/svg", json=bad)
    assert r.status_code == 400
    assert "unknown row format" in r.text


# --------------------------------------------------------------------------
# the command line, for a frame
# --------------------------------------------------------------------------

from hwcase import cli                                     # noqa: E402


def test_check_prints_the_frame_summary(capsys):
    _scene, model = _frame_6()
    assert cli.main(["check", str(FRAME_6), "--strict"]) == 0
    out = capsys.readouterr().out
    assert "frame frame-6: 68 HP, 2 rows (3U + 3U)" in out
    assert f"{model.width:.2f} mm between the panels" in out
    assert f"depth   {model.depth:.2f} mm" in out
    assert f"panel   {model.panel_height:.2f} mm tall" in out
    assert "8 x M5 screws" in out and "4 x rails" in out
    assert "U-channel order: length 345.4 mm" in out
    assert "0 errors, 0 warnings" in out
    # nothing of the layered report: no placements, no panels, no extent
    assert "placements" not in out and "extent" not in out


def _frame_scene(tmp_path, **case):
    scene, _model = _frame_6()
    if case:
        scene.case = scene.case.model_copy(update=case)
    path = tmp_path / "frame-test.yaml"
    import yaml
    path.write_text(yaml.safe_dump(scene.model_dump(mode="json")),
                    encoding="utf-8")
    return path


def test_check_reports_a_frame_that_fits_no_stock(tmp_path, capsys):
    path = _frame_scene(tmp_path, plates=[(120.0, 120.0)])
    assert cli.main(["check", str(path)]) == 0
    assert cli.main(["check", str(path), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "[X] stock" in out and "side-left (side_panel)" in out


def test_check_reports_a_frame_that_cannot_be_built(tmp_path, capsys):
    import yaml

    path = _frame_scene(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["rack"]["rows"] = [{"format": "7U-nonsense"}]
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert cli.main(["check", str(path), "--strict"]) == 1
    assert "unknown row format" in capsys.readouterr().out


def test_build_writes_the_frame_sheets_and_cut_list(tmp_path, capsys):
    _scene, model = _frame_6()
    out = tmp_path / "out"
    assert cli.main(["build", str(FRAME_6), "-o", str(out)]) == 0
    sheets = out / "frame-6-sheets"
    names = sorted(p.name for p in sheets.iterdir())
    assert "frame-6-cutlist.txt" in names
    assert any(n.endswith(".svg") for n in names)
    assert any(n.endswith(".dxf") for n in names)
    cut = (sheets / "frame-6-cutlist.txt").read_text(encoding="utf-8")
    assert "side-left" in cut and "side-right" in cut
    for r in model.rails:
        assert f"{r.name:<12} {r.length:8.2f} mm" in cut
    assert f"{2 * len(model.rails)} x M5 screw" in cut
    assert "U-channel (order):" in cut
    assert (out / "frame-6-frame.json").exists()


def test_build_on_a_frame_never_writes_a_layered_case(tmp_path, monkeypatch):
    """No layer strip, no case JSON, and `case.build` is never even called."""
    from hwcase import case as case_mod

    def refuse(*_a, **_k):
        raise AssertionError("a frame scene reached the layered case builder")

    monkeypatch.setattr(case_mod, "build", refuse)
    monkeypatch.setattr(export, "write_all", refuse)
    out = tmp_path / "out"
    assert cli.main(["build", str(FRAME_6), "-o", str(out)]) == 0
    written = [p.name for p in out.rglob("*") if p.is_file()]
    assert written
    for name in written:
        assert "-layers." not in name and not name.endswith("-case.json")
        assert not name.endswith("-scene.json")


def test_build_fails_loudly_when_the_plates_fit_no_stock(tmp_path, capsys):
    path = _frame_scene(tmp_path, plates=[(120.0, 120.0)])
    assert cli.main(["build", str(path), "-o", str(tmp_path / "o")]) == 1
    assert "fits none of the plates" in capsys.readouterr().out
    assert not list((tmp_path / "o").glob("*-sheets")), \
        "a half-written sheet folder for a frame that cannot be cut"
