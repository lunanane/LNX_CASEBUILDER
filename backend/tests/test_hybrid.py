"""M10: a Eurorack hybrid -- a rack bay and Adafruit boards in one box.

The fixture is the builder's own final test: the AMYboard as a module in a
28 HP bay, beside two NeoTrellis boards with their elastomer button pads.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate
from shapely.geometry import Point, Polygon, box

from hwcase import case as case_mod
from hwcase import export, flat, rack
from hwcase import joinery as jn
from hwcase.box import BoxModel, box_to_json
from hwcase.library import PartLibrary, load_scene
from hwcase.scene import check, resolve

LIB = PartLibrary.load()
SCENE = Path(__file__).resolve().parents[1] / "scenes" / "hybrid-trellis-amyboard.yaml"


def _scene(**changes):
    scene = load_scene(SCENE)
    return scene.model_copy(update=changes) if changes else scene


@pytest.fixture(scope="module")
def res():
    return resolve(_scene(), LIB)


@pytest.fixture(scope="module")
def model(res) -> BoxModel:
    return case_mod.build(res)


def _errors(scene):
    res = resolve(scene, LIB)
    return [i for i in check(res, LIB) if i.level == "error"]


def _round_holes(geom, diameter, tol=0.05):
    """Centres of the round holes of one diameter in a plate's outline."""
    out = []
    for ring in geom.interiors:
        hole = Polygon(ring)
        c = hole.centroid
        r = max(Point(p).distance(c) for p in ring.coords)
        if abs(2 * r - diameter) < tol and hole.area > 0.9 * np.pi * r * r:
            out.append((c.x, c.y))
    return out


# --------------------------------------------------------------------------
# the plan's acceptance rows
# --------------------------------------------------------------------------


def test_row1_the_hybrid_builds_with_no_errors(res, model):
    assert isinstance(model, BoxModel)
    assert {p.name for p in model.plates} == {
        "deck", "floor", "front", "back", "left", "right",
        "bay-cheek-left", "bay-cheek-right"}
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    assert not [n for n in model.notes if "not jointed" in n]


def test_row2_the_module_faces_are_the_upper_surface(res, model):
    """D-3: the rails sit one module-panel thickness under the deck top,
    and the module's own face lands on the deck top."""
    deck = model.plate("deck")
    deck_top = deck.origin[2] + deck.material.thickness
    for rail in model.members:
        assert deck_top - rail.origin[2] == pytest.approx(2.0, abs=0.01)
    face = max(s.z[1] for s in res.solids if s.placement == "amy_panel")
    assert face == pytest.approx(deck_top, abs=0.01)


def test_row3_a_board_dropped_into_the_module_space_is_an_error():
    scene = _scene()
    moved = [p.model_copy(update={"pos": (60.0, 36.0, 0.0)})
             if p.id == "trellis_b" else p for p in scene.placements]
    errors = _errors(scene.model_copy(update={"placements": moved}))
    hits = [e for e in errors if e.code == "rack_bay"]
    assert hits and "trellis_b" in hits[0].refs
    assert "module space" in hits[0].message


def test_row4_cheek_m5s_line_up_with_the_rail_ends(model):
    """Every rail end has an M5 hole through a cheek, at the rail's mid
    height and the bore's depth, to a hundredth."""
    ends = []
    for rail in model.members:
        y = rail.origin[1] + rack.RAIL_H / 2
        z = rail.origin[2] - rack.rail_bore_depth()
        ends.append((round(y, 6), round(z, 6)))
    for side in ("left", "right"):
        cheek = model.plate(f"bay-cheek-{side}")
        holes = _round_holes(cheek.geom, rack.M5_CLEAR)
        world = sorted((cheek.to_world(u, v)[1], cheek.to_world(u, v)[2])
                       for u, v in holes)
        assert len(world) == len(ends) == 2
        for (y1, z1), (y2, z2) in zip(world, sorted(ends)):
            assert y1 == pytest.approx(y2, abs=0.01)
            assert z1 == pytest.approx(z2, abs=0.01)


def test_row5_every_joint_validates_and_every_plate_fits_the_stock(model):
    for a, b, j in model.joints:
        pa, pb = model.plate(a), model.plate(b)
        problems = [v for v in jn.validate_joint(
            j, jn.Plate(pa.geom, pa.material), jn.Plate(pb.geom, pb.material))
            if v.rule != "keep_clear"]
        assert problems == [], (a, b, problems)
    cheek_joints = [(a, b) for a, b, _j in model.joints if "cheek" in b]
    assert cheek_joints == [("floor", "bay-cheek-left"),
                            ("deck", "bay-cheek-left"),
                            ("floor", "bay-cheek-right"),
                            ("deck", "bay-cheek-right")]
    sheets = export.pack_sheets(flat.from_box(model), stock=model.spec)
    placed = {pl.part.name for s in sheets for pl in s.placements}
    assert placed == {p.name for p in model.plates}


def test_row6_the_cut_list_has_the_rails_cheeks_and_deck(model):
    files = dict(export.sheet_files(model, "svg", "hybrid"))
    cut = files["hybrid-cutlist.txt"]
    for word in ("deck", "bay-cheek-left", "bay-cheek-right", "floor"):
        assert word in cut
    assert "2 x rails" in cut and "rail: 284.48 mm" in cut
    assert "4 x M5 screw" in cut
    assert "bay bay: 28 HP" in cut
    assert cut.count(" through the deck ") == 16      # 8 per NeoTrellis


# --------------------------------------------------------------------------
# what the builder asked for: buttons, a module, one surface
# --------------------------------------------------------------------------


def test_the_deck_opens_for_all_32_buttons(res, model):
    x0, y0, _x1, _y1 = model.outer.bounds
    deck = translate(model.plate("deck").geom, x0, y0)
    pads = [s.poly for s in res.solids
            if s.placement in ("trellis_a", "trellis_b") and s.name == "pcb"]
    buttons = [Polygon(r) for r in deck.interiors
               if Polygon(r).area > 80.0
               and any(p.contains(Polygon(r).centroid) for p in pads)]
    assert len(buttons) == 32


def test_the_module_drops_through_the_bay_aperture(res, model):
    x0, y0, _x1, _y1 = model.outer.bounds
    deck = translate(model.plate("deck").geom, x0, y0)
    panel = next(s.poly for s in res.solids if s.placement == "amy_panel"
                 and s.name == "pcb")
    assert deck.intersection(panel).area < 0.01


def test_a_modules_ports_never_open_the_box(model):
    assert not [n for n in model.notes if "amy." in n]


def test_the_floor_gives_the_module_its_depth(model):
    """Module depth is clear room behind the module panel -- from the rails'
    front face, as on a frame -- so the deck top is the depth plus the
    panel's 2 mm above the floor."""
    floor = model.plate("floor")
    deck = model.plate("deck")
    floor_top = floor.origin[2] + floor.material.thickness
    deck_top = deck.origin[2] + deck.material.thickness
    rail_face = max(m.origin[2] for m in model.members)
    assert rail_face - floor_top == pytest.approx(40.0, abs=0.01)
    assert deck_top - floor_top == pytest.approx(42.0, abs=0.01)


def test_module_depth_means_the_same_on_a_bay_and_a_frame():
    """A frame's case depth is its module depth behind the rail face plus
    the web; a bay's floor is its module depth behind the rail face."""
    assert rack.case_depth(40.0) - rack.BODY_T == pytest.approx(40.0)


def test_a_module_deeper_than_the_bay_is_an_error():
    scene = _scene()
    bay = scene.racks[0].model_copy(update={"module_depth": 20.0})
    errors = _errors(scene.model_copy(update={"racks": [bay]}))
    assert any(e.code == "module_depth" and "amy" in e.refs for e in errors)


def test_a_bay_needs_a_box():
    scene = _scene()
    layered = scene.case.model_copy(update={"style": "layered"})
    errors = _errors(scene.model_copy(update={"case": layered}))
    assert any(e.code == "rack_bay" and "box" in e.message for e in errors)


def test_a_module_that_is_not_a_placement_is_named():
    scene = _scene()
    bay = scene.racks[0].model_copy(update={"modules": ["amy", "ghost"]})
    errors = _errors(scene.model_copy(update={"racks": [bay]}))
    assert any("ghost" in e.message for e in errors)


@pytest.mark.parametrize("joint", ["tslot", "finger"])
def test_both_joint_types_build_the_hybrid(joint):
    scene = _scene()
    case = scene.case.model_copy(update={"box_joint": joint})
    model = case_mod.build(resolve(scene.model_copy(update={"case": case}), LIB))
    assert len(model.plates) == 8
    # interior joints need slots: a finger box still bolts its cheeks to
    # the floor; the deck meets them at the aperture's edge, a box corner
    kinds = {(a, j.kind) for a, b, j in model.joints if "cheek" in b}
    assert kinds == {("floor", "tslot"), ("deck", "finger")}


def test_the_editor_gets_plates_rails_and_the_bay(model):
    data = box_to_json(model)
    assert len(data["plates"]) == 8
    assert [m["role"] for m in data["members"]] == ["rail", "rail"]
    assert data["extras"]["bays"][0]["modules"] == ["amy", "amy_panel"]
    for p in data["plates"] + data["members"]:
        m = np.array(p["matrix"], float).reshape(4, 4)[:3, :3]
        assert np.linalg.det(m) == pytest.approx(1.0)


def test_the_command_line_builds_the_hybrid(tmp_path, capsys):
    from hwcase import cli

    assert cli.main(["build", str(SCENE), "-o", str(tmp_path)]) == 0
    names = [p.name for p in tmp_path.iterdir()]
    assert any(n.endswith(".svg") for n in names)
    assert any(n.endswith(".dxf") for n in names)
    assert "hybrid-trellis-amyboard-cutlist.txt" in names
    out = capsys.readouterr().out
    assert "8 plates" in out


# --------------------------------------------------------------------------
# the cheeks hold the deck, not just the rails
# --------------------------------------------------------------------------


def _cheek_world_z(cheek):
    """World z of a cheek's top and bottom edges."""
    _u0, _v0, u1, _v1 = cheek.geom.bounds
    return cheek.to_world(0.0, 0.0)[2], cheek.to_world(u1, 0.0)[2]


def test_the_cheeks_are_jointed_into_the_deck_along_the_aperture(model):
    """Each cheek stands up to the deck's top face beside the rails, where
    its fingers and the deck's interlock; the deck's fingers are cut into
    the aperture's edge."""
    deck = model.plate("deck")
    deck_top = deck.origin[2] + deck.material.thickness
    for side in ("left", "right"):
        cheek = model.plate(f"bay-cheek-{side}")
        top, bottom = _cheek_world_z(cheek)
        assert top == pytest.approx(deck_top, abs=0.01)
        assert bottom == pytest.approx(model.z0, abs=0.01)
        j = next(j for a, b, j in model.joints
                 if a == "deck" and b == cheek.name)
        assert j.kind == "finger" and j.edge.length > 120.0


def test_panels_thicker_than_the_deck_never_leave_it_floating():
    """4 mm panels put the rails a millimetre under a 3 mm deck: the cheeks
    are carried up to meet it, and the scene says so."""
    scene = _scene()
    bay = scene.racks[0].model_copy(update={"module_panel_t": 4.0})
    scene = scene.model_copy(update={"racks": [bay]})
    res = resolve(scene, LIB)
    issues = [i for i in check(res, LIB) if i.code == "rack_bay"]
    assert any(i.level == "warning" and "thicker than the 3 mm deck"
               in i.message for i in issues)
    model = case_mod.build(res)
    deck = model.plate("deck")
    under = deck.origin[2]
    cheek = model.plate("bay-cheek-left")
    # under the solid deck -- the margin below the rails -- the cheek's top
    # edge is the deck's underside
    probe = cheek.geom.intersection(box(-1.0, 0.0, 10.0, 3.0))
    assert cheek.to_world(probe.bounds[0], 0.0)[2] == pytest.approx(
        under, abs=0.01)
    rail_face = max(m.origin[2] for m in model.members)
    assert deck.origin[2] + 3.0 - rail_face == pytest.approx(4.0, abs=0.01)


# --------------------------------------------------------------------------
# where a bay may go: issues, never exceptions
# --------------------------------------------------------------------------


def _with_bays(*bays):
    scene = _scene()
    return scene.model_copy(update={"racks": list(bays)})


def test_two_bays_side_by_side_are_an_issue_not_a_crash():
    """Rails end to end: the second bay's left cheek stands where the first
    bay's right cheek is."""
    scene = _scene()
    a = scene.racks[0]
    b = a.model_copy(update={"id": "bay2", "modules": [],
                             "at": (a.at[0] + 28 * 5.08, a.at[1])})
    issues = check(resolve(_with_bays(a, b), LIB), LIB)
    hits = [i for i in issues if i.code == "rack_bay" and i.level == "error"
            and "overlap" in i.message]
    assert hits and set(hits[0].refs) == {"bay", "bay2"}


def test_two_bays_too_close_for_their_floor_slots_are_named():
    scene = _scene()
    a = scene.racks[0]
    b = a.model_copy(update={"id": "bay2", "modules": [],
                             "at": (a.at[0] + 28 * 5.08 + 3.0 + 3.0 + 1.0,
                                    a.at[1])})
    issues = check(resolve(_with_bays(a, b), LIB), LIB)
    assert any(i.code == "rack_bay" and "mm apart" in i.message
               for i in issues)


def test_two_bays_far_enough_apart_build():
    scene = _scene()
    a = scene.racks[0]
    b = a.model_copy(update={"id": "bay2", "modules": [], "hp": 8,
                             "at": (a.at[0], a.at[1] - 200.0)})
    scene = _with_bays(a, b)
    res = resolve(scene, LIB)
    assert not [i for i in check(res, LIB)
                if i.code == "rack_bay" and i.level == "error"]


def test_an_unknown_row_format_is_an_issue():
    scene = _scene()
    bay = scene.racks[0].model_copy(update={
        "rows": [scene.racks[0].rows[0].model_copy(update={"format": "5U"})]})
    issues = check(resolve(_with_bays(bay), LIB), LIB)
    assert any(i.code == "rack_bay" and "5U" in i.message for i in issues)


def test_two_bays_with_one_id_are_named():
    scene = _scene()
    a = scene.racks[0]
    b = a.model_copy(update={"modules": [], "at": (a.at[0], -250.0)})
    issues = check(resolve(_with_bays(a, b), LIB), LIB)
    assert any("two bays are called bay" in i.message for i in issues)
