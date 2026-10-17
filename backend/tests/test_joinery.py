"""Materials, fasteners and the joints cut from them (milestone M6)."""

import dataclasses
import math
import re
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Point, Polygon, box

from hwcase.case import kerf_compensated
from hwcase.export import _material_json
from hwcase.fasteners import FASTENERS, fastener
from hwcase.joinery import (Edge, JointError, Plate, Segment, finger_joint,
                            tslot_joint, validate_joint)
from hwcase.schema import CaseSpec, Finish, Material

WEB = Path(__file__).resolve().parents[2] / "web"


# ---------------------------------------------------------------------------
# materials (D-2: every number a joint reads is the user's)
# ---------------------------------------------------------------------------

def test_material_defaults_leave_existing_scenes_as_they_were():
    m = Material()
    assert (m.name, m.thickness, m.kerf) == ("plywood-3mm", 3.0, 0.15)
    assert m.joint_clearance == pytest.approx(0.1)
    assert m.finish is None, "an unset finish means: read it off the name"


@pytest.mark.parametrize("t", [2.0, 2.7, 3.0, 3.2, 4.0, 5.0, 6.0, 8.0])
def test_material_thickness_kerf_and_clearance_are_user_set(t):
    m = Material(name="birch", thickness=t, kerf=0.2, joint_clearance=0.05,
                 finish="plywood")
    assert (m.thickness, m.kerf, m.joint_clearance) == (t, 0.2, 0.05)
    assert m.finish is Finish.plywood


@pytest.mark.parametrize("field,value", [
    ("thickness", 0.0), ("thickness", -3.0),
    ("kerf", -0.1), ("joint_clearance", -0.05),
    ("finish", "chrome"),
])
def test_impossible_material_numbers_are_refused(field, value):
    with pytest.raises(ValidationError):
        Material(**{field: value})


def test_material_round_trips_through_a_scene_case():
    spec = CaseSpec(materials=[Material(thickness=2.7, joint_clearance=0.2,
                                        finish="acrylic")])
    again = CaseSpec.model_validate(spec.model_dump(mode="json"))
    assert again.materials[0] == spec.materials[0]


def test_case_json_writes_new_material_fields_only_when_set():
    # byte-identical exports for every scene that never heard of them
    plain = _material_json(Material())
    assert set(plain) == {"name", "thickness", "kerf", "sheet", "color"}
    set_ = _material_json(Material(joint_clearance=0.2, finish="mdf"))
    assert set_["joint_clearance"] == 0.2 and set_["finish"] == "mdf"


def test_finish_names_match_the_browser_presets():
    src = (WEB / "finishes.js").read_text(encoding="utf-8")
    block = src[src.index("export const FINISHES"):src.index("};")]
    keys = set(re.findall(r"^\s*(\w+):\s*\{", block, re.M))
    assert keys == {f.value for f in Finish}


# ---------------------------------------------------------------------------
# fasteners (A-6: sizes are table rows, not constants)
# ---------------------------------------------------------------------------

def test_m3_is_the_iso_4032_nut_and_bolt():
    m3 = fastener("M3")
    assert m3.thread_d == 3.0
    assert m3.nut_af == 5.5 and m3.nut_t == 2.4
    assert m3.clearance_d > m3.thread_d
    assert m3.nut_ac == pytest.approx(6.351, abs=1e-3)


@pytest.mark.parametrize("name", sorted(FASTENERS))
def test_every_fastener_row_is_self_consistent(name):
    f = FASTENERS[name]
    assert f.name == name
    assert f.thread_d < f.clearance_d < f.nut_af < f.nut_ac
    assert f.nut_t > 0 and f.thread_past_nut > 0
    assert list(f.lengths) == sorted(f.lengths) and f.lengths


def test_bolt_length_rounds_up_to_stock_and_refuses_beyond_it():
    m3 = fastener("M3")
    assert m3.bolt_length(9.1) == 10.0
    assert m3.bolt_length(10.0) == 10.0
    with pytest.raises(ValueError, match="longest stocked"):
        m3.bolt_length(m3.lengths[-1] + 1)


def test_unknown_fastener_names_the_ones_there_are():
    with pytest.raises(KeyError, match="M3"):
        fastener("M7")


# ---------------------------------------------------------------------------
# joints: the property tests (plan M6 acceptance rows 1-5)
# ---------------------------------------------------------------------------
#
# Every row runs over the whole thickness range nominal sheet comes in (A-3),
# equal and mixed, and over both joint types (D-1). The checks measure the
# cut plates themselves -- lines through the actual polygons -- rather than
# trusting the joint record that `validate_joint` reads, so a record that
# lies about its geometry cannot pass them.

THICKNESSES = [2.0, 2.7, 3.0, 3.2, 4.0, 5.0, 6.0, 8.0]
PAIRS = [(t, t) for t in THICKNESSES] + [
    (2.0, 8.0), (8.0, 2.0), (2.7, 3.2), (3.0, 6.0), (5.0, 2.0)]
KINDS = ["finger", "tslot"]
CASES = [(k, ta, tb) for k in KINDS for ta, tb in PAIRS]
IDS = [f"{k}-{ta:g}-{tb:g}" for k, ta, tb in CASES]

L = 150.0
TOL = 1e-6


def _plates(kind, ta, tb):
    """`a` drawn along x with the joint on its bottom edge; `b` stood up with
    the joint on its right-hand side, so every test also crosses a rotated
    frame. A T-slot's `a` carries on 12 mm past `b`'s outer face."""
    ma = Material(name=f"a-{ta:g}", thickness=ta, kerf=0.15, joint_clearance=0.1)
    mb = Material(name=f"b-{tb:g}", thickness=tb, kerf=0.2, joint_clearance=0.15)
    a = box(0, -12.0 if kind == "tslot" else 0.0, L, 70)
    b = box(0, 0, 70, L)
    edge = Edge(Segment((0, 0), (L, 0), "left"),
                Segment((70, 0), (70, L), "left"))
    return a, ma, b, mb, edge


def _join(kind, edge, a, b, **kw):
    if kind == "finger":
        return finger_joint(edge, a, b, **kw)
    return tslot_joint(edge, a, b, "M3", **kw)


@lru_cache(maxsize=None)
def joined(kind, ta, tb):
    a, ma, b, mb, edge = _plates(kind, ta, tb)
    return (Plate(a, ma), Plate(b, mb)) + _join(kind, edge, Plate(a, ma), Plate(b, mb))


def _run(geom, seg, s, d, along="s", empty=True):
    """Ends of the empty (or solid) run through (s, d) of the edge frame,
    along s or along d."""
    g = seg.to_edge(geom)
    far = 1e4
    line = LineString([(-far, d), (far, d)]) if along == "s" \
        else LineString([(s, -far), (s, far)])
    pieces = line.difference(g) if empty else line.intersection(g)
    at = Point(s, d)
    for p in getattr(pieces, "geoms", [pieces]):
        if p.distance(at) < 1e-9:
            xs = [x if along == "s" else y for x, y in p.coords]
            return min(xs), max(xs)
    return None


def _as_cut(geom, kerf):
    """Kerf-compensated the way the exporters do it, then cut by the beam."""
    return kerf_compensated(geom, kerf).buffer(-kerf / 2, join_style=2)


def _owned(geom, seg, depth):
    g = seg.to_edge(geom)
    hit = LineString([(0, depth), (L, depth)]).intersection(g)
    out = []
    for p in getattr(hit, "geoms", [hit]):
        if not p.is_empty and p.length > TOL:
            xs = [x for x, _ in p.coords]
            out.append((min(xs), max(xs)))
    return sorted(out)


# -- row 1 ---------------------------------------------------------------

@pytest.mark.parametrize("kind,ta,tb", CASES, ids=IDS)
def test_row1_every_tab_has_a_slot_sized_to_tab_plus_clearance(kind, ta, tb):
    a0, b0, a, b, joint = joined(kind, ta, tb)
    plates = {"a": a, "b": b}
    segs = {"a": joint.edge.a, "b": joint.edge.b}
    other = {"a": "b", "b": "a"}
    c = max(a.material.joint_clearance, b.material.joint_clearance)
    assert joint.clearance == c
    tabs = [f for f in joint.features if f.kind == "tab"]
    assert len(tabs) >= 2
    for tab in tabs:
        mid_s = (tab.span[0] + tab.span[1]) / 2
        # the tab really is that wide, in the cut plate
        lo, hi = _run(plates[tab.plate].geom, segs[tab.plate], mid_s,
                      (tab.depth[0] + tab.depth[1]) / 2, empty=False)
        assert lo == pytest.approx(max(tab.span[0], 0.0), abs=1e-6)
        assert hi == pytest.approx(min(tab.span[1], L), abs=1e-6)
        # ... and the mating plate has an empty run round it of exactly the
        # tab plus the clearance, centred -- except where the run leaves the
        # plate at the end of the joint, which is open by nature
        mate = other[tab.plate]
        depth = plates[tab.plate].t / 2 if kind == "finger" else tb / 2
        lo, hi = _run(plates[mate].geom, segs[mate], mid_s, depth)
        if lo > -TOL:
            assert lo == pytest.approx(tab.span[0] - c / 2, abs=1e-6)
        if hi < L + TOL:
            assert hi == pytest.approx(tab.span[1] + c / 2, abs=1e-6)
        if kind == "tslot":
            # closed on all four sides: across, it is b's sheet plus clearance
            d0, d1 = _run(a.geom, segs["a"], mid_s, tb / 2, along="d")
            assert d1 - d0 == pytest.approx(tb + c, abs=1e-6)
            assert (d0 + d1) / 2 == pytest.approx(tb / 2, abs=1e-6)
            # and the tab is exactly as long as a is thick: flush outside
            assert tab.deep == pytest.approx(ta)


# -- row 2 ---------------------------------------------------------------

@pytest.mark.parametrize("kind,ta,tb", CASES, ids=IDS)
def test_row2_patterns_are_complementary_with_no_overlap_or_gap_after_kerf(kind, ta, tb):
    a0, b0, a, b, joint = joined(kind, ta, tb)
    c = joint.clearance
    own_a = _owned(_as_cut(a.geom, a.material.kerf), joint.edge.a, tb / 2)
    own_b = _owned(_as_cut(b.geom, b.material.kerf), joint.edge.b, ta / 2)
    assert own_a and own_b
    for x0, x1 in own_a:
        for y0, y1 in own_b:
            assert min(x1, y1) - max(x0, y0) <= 1e-4, "both plates claim the corner"
    # walk the corner bar: every stretch neither plate owns is half the fit,
    # unless a bolt passes through it
    cursor, gaps = 0.0, []
    for s0, s1 in sorted(own_a + own_b):
        if s0 > cursor + TOL:
            gaps.append((cursor, s0))
        cursor = max(cursor, s1)
    assert cursor == pytest.approx(L, abs=1e-6), "the joint is owned to its far end"
    bolts = [bolt.s for bolt in joint.bolts]
    for g0, g1 in gaps:
        if any(g0 < s < g1 for s in bolts):
            continue
        assert g1 - g0 <= c + 1e-4, f"gap {g1 - g0:.3f} at {g0:.2f}"
        assert g1 - g0 == pytest.approx(c / 2, abs=1e-4), "half the fit each side"
    if kind == "finger":
        tabs_a = len(joint.of("a", "tab"))
        tabs_b = len(joint.of("b", "tab"))
        assert abs(tabs_a - tabs_b) <= 1 and len(gaps) == tabs_a + tabs_b - 1


# -- row 3 ---------------------------------------------------------------

@pytest.mark.parametrize("kind,ta,tb", CASES, ids=IDS)
def test_row3_each_nut_trap_holds_its_nut_and_the_bolt_hole_lines_up(kind, ta, tb):
    a0, b0, a, b, joint = joined(kind, ta, tb)
    if kind == "finger":
        # a glued joint: nothing to buy
        assert joint.nuts == 0 and joint.bom() == {} and not joint.bolts
        return
    f = fastener("M3")
    c = joint.clearance
    assert joint.nuts == len(joint.bolts) == len(joint.of("b", "nut_trap")) >= 1
    assert joint.bom()["M3 hex nut"] == joint.nuts
    for bolt in joint.bolts:
        trap = next(t for _, t in joint.of("b", "nut_trap")
                    if abs((t.span[0] + t.span[1]) / 2 - bolt.s) < 1e-9)
        mid_d = (trap.depth[0] + trap.depth[1]) / 2
        # across the flats, measured in the cut plate
        lo, hi = _run(b.geom, joint.edge.b, bolt.s, mid_d)
        assert hi - lo >= f.nut_af + c - 1e-6
        assert (lo + hi) / 2 == pytest.approx(bolt.s, abs=0.01)
        # its thickness, beside the shank channel
        s_side = bolt.s + (f.thread_d + c) / 2 + (f.nut_af - f.thread_d) / 4
        d0, d1 = _run(b.geom, joint.edge.b, s_side, mid_d, along="d")
        assert d1 - d0 >= f.nut_t + c - 1e-6
        # the channel carries the whole bolt past a's sheet, from b's shoulder
        d0, d1 = _run(b.geom, joint.edge.b, bolt.s, ta + 0.01, along="d")
        assert d1 >= bolt.length - 1e-6
        assert bolt.length >= trap.depth[1] + f.thread_past_nut - 1e-6
        # the hole in a: same axis, in the middle of b's sheet, bolt-sized
        lo, hi = _run(a.geom, joint.edge.a, bolt.s, tb / 2)
        assert (lo + hi) / 2 == pytest.approx(bolt.s, abs=0.01)
        d0, d1 = _run(a.geom, joint.edge.a, bolt.s, tb / 2, along="d")
        assert (d0 + d1) / 2 == pytest.approx(tb / 2, abs=0.01)
        assert min(hi - lo, d1 - d0) >= f.clearance_d - 1e-6


# -- row 4 ---------------------------------------------------------------

def _clashing_holes(kind, ta, tb):
    """Declared holes placed where the preferred layout would cut, close
    enough to the joint that the layout has to move to keep clear of them."""
    a0, ma, b0, mb, edge = _plates(kind, ta, tb)
    a, b, joint = _join(kind, edge, Plate(a0, ma), Plate(b0, mb), check=False)
    r = 1.5
    if kind == "finger":
        # over the middle notch on each plate
        notches_a = joint.of("a", "slot")
        notches_b = joint.of("b", "slot")
        fa = notches_a[len(notches_a) // 2][1]
        fb = notches_b[len(notches_b) // 2][1]
        ha = Point(edge.a.point((fa.span[0] + fa.span[1]) / 2, tb + r + ta / 2)).buffer(r)
        hb = Point(edge.b.point((fb.span[0] + fb.span[1]) / 2, ta + r + tb / 2)).buffer(r)
        return (ha,), (hb,)
    # beside the first bolt hole, half the keep-clear distance from it
    s = joint.bolts[0].s
    hole_r = fastener("M3").clearance_d / 2
    ha = Point(edge.a.point(s, tb / 2 + hole_r + r + ta / 2)).buffer(r)
    return (ha,), ()


@pytest.mark.parametrize("kind,ta,tb", CASES, ids=IDS)
def test_row4_no_joint_feature_comes_within_keep_clear_of_a_declared_hole(kind, ta, tb):
    a0, ma, b0, mb, edge = _plates(kind, ta, tb)
    holes_a, holes_b = _clashing_holes(kind, ta, tb)
    pa = Plate(a0, ma, holes=holes_a)
    pb = Plate(b0, mb, holes=holes_b)
    # the preferred layout really does clash -- or this test proves nothing
    a, b, preferred = _join(kind, edge, pa, pb, check=False)
    assert any(v.rule == "keep_clear" for v in validate_joint(preferred, a, b))
    a, b, joint = _join(kind, edge, pa, pb)
    assert validate_joint(joint, a, b) == []
    for before, after in ((pa, a), (pb, b)):
        removed = before.geom.difference(after.geom)
        for h in before.holes:
            assert removed.distance(h) >= before.clear - 1e-6
            assert after.geom.contains(h), "the joint never cuts a declared hole"


def test_row4_a_hole_no_layout_can_avoid_is_refused_loudly():
    a0, ma, b0, mb, edge = _plates("tslot", 3.0, 3.0)
    # a row of holes all along the joint: nowhere is far enough away
    holes = tuple(Point(edge.a.point(s, 6.0)).buffer(1.0) for s in range(5, 150, 10))
    with pytest.raises(JointError, match="keep_clear"):
        tslot_joint(edge, Plate(a0, ma, holes=holes), Plate(b0, mb))
    holes_b = tuple(Point(edge.b.point(s, 6.0)).buffer(1.0) for s in range(5, 150, 10))
    with pytest.raises(JointError, match="keep_clear"):
        finger_joint(edge, Plate(box(0, 0, L, 70), ma), Plate(b0, mb, holes=holes_b))


# -- row 5 ---------------------------------------------------------------

@pytest.mark.parametrize("kind,ta,tb", CASES, ids=IDS)
def test_row5_each_plate_stays_one_piece(kind, ta, tb):
    a0, b0, a, b, joint = joined(kind, ta, tb)
    for before, after in ((a0, a), (b0, b)):
        assert isinstance(after.geom, Polygon) and after.geom.is_valid
        assert after.geom.area < before.geom.area, "the joint cut something"
        assert before.geom.covers(after.geom), "and only ever took material away"
    assert validate_joint(joint, a, b) == []


# ---------------------------------------------------------------------------
# joints: the rest of the contract
# ---------------------------------------------------------------------------

def test_validate_joint_reports_every_broken_rule_at_once():
    a0, b0, a, b, joint = joined("tslot", 3.0, 3.0)
    feats = list(joint.features)
    # a slot one clearance too narrow, a nut trap too small, a bolt hole
    # moved off its nut, and a declared hole right on the joint
    i_slot = next(i for i, f in enumerate(feats) if f.kind == "slot")
    s = feats[i_slot]
    feats[i_slot] = dataclasses.replace(s, span=(s.span[0] + 0.05, s.span[1] - 0.05))
    i_trap = next(i for i, f in enumerate(feats) if f.kind == "nut_trap")
    t = feats[i_trap]
    feats[i_trap] = dataclasses.replace(t, span=(t.span[0] + 0.5, t.span[1] - 0.5))
    i_hole = next(i for i, f in enumerate(feats) if f.kind == "bolt_hole")
    h = feats[i_hole]
    feats[i_hole] = dataclasses.replace(h, span=(h.span[0] + 1, h.span[1] + 1))
    broken = dataclasses.replace(joint, features=tuple(feats))
    near = Point(joint.edge.a.point(joint.bolts[0].s, 6.0)).buffer(1.0)
    a_holed = dataclasses.replace(a, holes=(near,))
    problems = validate_joint(broken, a_holed, b)
    rules = {v.rule for v in problems}
    assert {"tab_slot_fit", "nut_trap", "keep_clear"} <= rules
    assert len([v for v in problems if v.rule == "nut_trap"]) >= 2
    assert all(v.message for v in problems)


def test_a_face_plate_without_room_past_the_joint_fails_the_web_rule():
    # a T-slot's `a` ending flush with b's outer face would have its slots and
    # bolt holes open at the edge
    a0, ma, b0, mb, edge = _plates("tslot", 3.0, 3.0)
    flush = Plate(box(0, 0, L, 70), ma)
    a, b, joint = tslot_joint(edge, flush, Plate(b0, mb), check=False)
    assert "web" in {v.rule for v in validate_joint(joint, a, b)}
    with pytest.raises(JointError, match="web"):
        tslot_joint(edge, flush, Plate(b0, mb))


def test_a_kerf_wider_than_a_feature_is_reported():
    a0, ma, b0, mb, edge = _plates("tslot", 3.0, 3.0)
    fat = Material(thickness=3.0, kerf=3.6)     # wider than the bolt hole
    a, b, joint = tslot_joint(edge, Plate(a0, fat), Plate(b0, mb), check=False)
    assert "kerf" in {v.rule for v in validate_joint(joint, a, b)}


def test_changing_the_thickness_changes_the_cut_and_still_validates():
    # the M7 acceptance in miniature: 3 mm to 6 mm stock re-cuts the slots
    for kind in KINDS:
        thin = joined(kind, 3.0, 3.0)[4]
        thick = joined(kind, 6.0, 6.0)
        assert {f.deep for f in thin.features if f.kind == "slot"} != \
            {f.deep for f in thick[4].features if f.kind == "slot"}
        assert validate_joint(thick[4], thick[2], thick[3]) == []


@pytest.mark.parametrize("size", sorted(FASTENERS))
def test_every_fastener_in_the_table_makes_a_valid_tslot(size):
    a0, ma, b0, mb, edge = _plates("tslot", 4.0, 4.0)
    a, b, joint = tslot_joint(edge, Plate(a0, ma), Plate(b0, mb), size)
    assert validate_joint(joint, a, b) == []
    f = fastener(size)
    for _, trap in joint.of("b", "nut_trap"):
        assert trap.width == pytest.approx(f.nut_af + joint.clearance)
    assert all(bolt.length in f.lengths for bolt in joint.bolts)


def test_edges_of_different_lengths_are_refused():
    with pytest.raises(JointError, match="same length"):
        Edge(Segment((0, 0), (100, 0)), Segment((0, 0), (99, 0)))


def test_a_finger_joint_must_run_along_both_outlines():
    a0, ma, b0, mb, edge = _plates("finger", 3.0, 3.0)
    inside = Edge(Segment((0, 10), (L, 10)), edge.b)       # through a's middle
    with pytest.raises(JointError, match="outline"):
        finger_joint(inside, Plate(a0, ma), Plate(b0, mb))
    wrong_side = Edge(Segment((0, 0), (L, 0), "right"), edge.b)
    with pytest.raises(JointError, match="outline"):
        finger_joint(wrong_side, Plate(a0, ma), Plate(b0, mb))


def test_too_short_an_edge_is_refused():
    m = Material(thickness=3.0)
    tiny = Edge(Segment((0, 0), (5, 0)), Segment((0, 0), (5, 0)))
    with pytest.raises(JointError, match="at least"):
        finger_joint(tiny, Plate(box(0, 0, 5, 20), m), Plate(box(0, 0, 5, 20), m))
    with pytest.raises(JointError, match="at least"):
        tslot_joint(tiny, Plate(box(0, -10, 5, 20), m), Plate(box(0, 0, 5, 20), m))


def test_segment_frames_round_trip_and_find_the_material_side():
    seg = Segment((10, 5), (10, 55), "right")
    p = box(1, 2, 3, 4)
    back = seg.to_edge(seg.to_plate(p))
    assert back.symmetric_difference(p).area < 1e-9
    assert seg.point(0, 1) == pytest.approx((11, 5))    # right of +y is +x
    plate = box(10, 0, 40, 60)
    assert Segment.on(plate, (10, 5), (10, 55)).inside == "right"
    assert Segment.on(plate, (10, 55), (10, 5)).inside == "left"
    with pytest.raises(ValueError):
        Segment((0, 0), (0, 0))


def test_a_joint_on_a_slanted_edge_validates():
    # nothing in the joint code assumes an axis-aligned edge
    m = Material(thickness=3.0)
    tri = Polygon([(0, 0), (120, 50), (0, 100)])
    ln = math.dist((0, 0), (120, 50))
    a_seg = Segment.on(tri, (0, 0), (120, 50))
    edge = Edge(a_seg, Segment((0, 0), (ln, 0)))
    a, b, joint = finger_joint(edge, Plate(tri, m), Plate(box(0, 0, ln, 40), m))
    assert validate_joint(joint, a, b) == []
    assert isinstance(a.geom, Polygon)
