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
    assert "2 x Eurorack rail, 142.24 mm (28 HP)" in cut
    assert "rail mm" not in cut
    assert "4 x M5x12 screw" in cut
    assert "tapped" in cut or "tap each rail" in cut
    assert "M5 tap drill 4.2 mm" in cut
    # the AMYboard is 10 HP: one column of holes, two screws and nuts
    assert "2 x M3x6 module screw (estimate)" in cut
    assert "2 x M3 sliding nut (estimate)" in cut
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
    assert data["extras"]["bays"][0]["modules"] == ["amy"]
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



# --------------------------------------------------------------------------
# modules: by slot, on the grid
# --------------------------------------------------------------------------

from hwcase.schema import BayModule  # noqa: E402


def _panel_box(res, pid="amy_panel"):
    return next(s.poly.bounds for s in res.solids
                if s.placement == pid and s.name == "pcb")


def _bay_errors(scene):
    return [e for e in _errors(scene) if e.code == "rack_bay"]


def _modules(*entries, **bay_changes):
    scene = _scene()
    bay = scene.racks[0].model_copy(update={"modules": list(entries),
                                            **bay_changes})
    return scene.model_copy(update={"racks": [bay]})


def _second_amy(scene, pos=(0.0, 0.0, 0.0)):
    amy = next(p for p in scene.placements if p.id == "amy")
    panel = next(p for p in scene.placements if p.id == "amy_panel")
    extra = [amy.model_copy(update={"id": "amy2", "pos": pos}),
             panel.model_copy(update={"id": "amy2_panel", "parent": "amy2"})]
    return scene.model_copy(update={"placements": scene.placements + extra})


def test_a_module_is_placed_by_its_hp_slot():
    """Slot 10 puts the panel's left edge 10 HP from the rails' left end and
    centres it on the row's rails; its board comes along, however the scene
    had it placed."""
    res = resolve(_modules(BayModule(id="amy", hp_slot=10)), LIB)
    x0, y0, _x1, y1 = _panel_box(res)
    assert x0 == pytest.approx(10 * 5.08, abs=1e-6)
    # 3U holes are 122.5 apart and 3.0 in from the panel's edges, so the
    # panel's bottom edge is 2.0 under the bottom rail's slot centre (5.0)
    assert y0 == pytest.approx(2.0, abs=1e-6)
    assert y1 - y0 == pytest.approx(128.5, abs=1e-6)
    board = _panel_box(res, "amy")
    assert board[0] - x0 == pytest.approx(1.15, abs=1e-6)
    assert [e for e in check(res, LIB) if e.level == "error"] == []


def test_a_module_in_a_row_the_bay_does_not_have_is_named():
    errors = _bay_errors(_modules(BayModule(id="amy", row=1)))
    assert any("row 1" in e.message and "1 row" in e.message for e in errors)


def test_a_module_running_past_the_rails_is_named():
    errors = _bay_errors(_modules(BayModule(id="amy", hp_slot=20)))
    assert any("past the end" in e.message and "amy" in e.refs
               for e in errors)


def test_two_modules_in_one_slot_overlap():
    scene = _second_amy(_modules(BayModule(id="amy", hp_slot=0),
                                 BayModule(id="amy2", hp_slot=5)))
    errors = _bay_errors(scene)
    assert any("overlap" in e.message and {"amy", "amy2"} <= set(e.refs)
               for e in errors)


def test_a_full_row_is_fine_and_one_more_hp_is_not():
    scene = _second_amy(_modules(BayModule(id="amy", hp_slot=0),
                                 BayModule(id="amy2", hp_slot=10), hp=20))
    assert _bay_errors(scene) == []
    scene = _second_amy(_modules(BayModule(id="amy", hp_slot=0),
                                 BayModule(id="amy2", hp_slot=10), hp=19))
    assert any("past the end" in e.message for e in _bay_errors(scene))


def test_modules_listed_by_id_still_load_and_are_checked():
    """The old form -- bare ids, the position typed by hand -- loads; on the
    grid it is fine, 1.3 mm off it or out of the aperture it is an error."""
    def free(dx=0.0, dy=0.0):
        scene = _modules("amy", "amy_panel")
        moved = [p.model_copy(update={"pos": (1.15 + dx, 13.35 + dy, 0.0)})
                 if p.id == "amy" else p for p in scene.placements]
        return scene.model_copy(update={"placements": moved})

    assert _bay_errors(free()) == []
    off = _bay_errors(free(dx=1.3))
    assert any("1.30 mm off" in e.message and "hp_slot: 0" in e.message
               for e in off)
    high = _bay_errors(free(dx=5.08, dy=0.4))
    assert any("off row 0's rails" in e.message for e in high)
    # under the solid deck, beside the bay: nothing else catches this
    gone = _bay_errors(free(dx=0.0, dy=-140.0))
    assert any("not inside" in e.message for e in gone)


def test_a_module_in_two_bays_is_named():
    scene = _scene()
    a = scene.racks[0]
    b = a.model_copy(update={"id": "bay2", "at": (a.at[0], -250.0)})
    errors = _bay_errors(scene.model_copy(update={"racks": [a, b]}))
    assert any("more than one bay" in e.message for e in errors)


def test_a_panel_thinner_than_the_bay_expects_is_a_warning():
    """The AMYboard's 2 mm panel in a bay set for 3 mm panels: with its face
    flush with the deck, its back stands a millimetre off the rails."""
    res = resolve(_modules(BayModule(id="amy"), module_panel_t=3.0), LIB)
    assert any(i.level == "warning" and "1.00 mm above the rails"
               in i.message for i in check(res, LIB))


def test_the_m5_reaches_through_the_cheek_into_the_rail():
    from hwcase.hybrid import m5_length

    assert m5_length(3.0) == 12.0          # 3 + 8 of thread
    assert m5_length(5.0) == 16.0


def test_a_wide_module_counts_four_screws():
    """12 HP and up: two columns of holes, four screws and sliding nuts."""
    from hwcase.hybrid import bay_bom
    from hwcase.panel import hole_columns

    assert len(hole_columns(10)) == 1 and len(hole_columns(12)) == 2
    assert bay_bom(resolve(_scene(), LIB), [], [], 3.0) == {}


def test_a_build_with_errors_writes_its_files_and_exits_1(tmp_path, capsys):
    """A board in the module space: the files are still written, to look
    at, but the command does not report success."""
    import yaml

    from hwcase import cli

    text = SCENE.read_text(encoding="utf-8").replace(
        "pos: [220.0, 36.0, 0.0]", "pos: [60.0, 36.0, 0.0]")
    assert "pos: [60.0, 36.0, 0.0]" in text
    scene = tmp_path / "bad.yaml"
    scene.write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["name"]
    assert cli.main(["build", str(scene), "-o", str(tmp_path / "o")]) == 1
    assert any((tmp_path / "o").iterdir())
    assert "errors -- run `check`" in capsys.readouterr().out


def test_a_build_the_builder_refuses_exits_1_not_a_traceback(tmp_path, capsys):
    """Two bays on top of each other: the builder cannot joint the cheeks,
    and the command says why instead of raising."""
    import yaml

    from hwcase import cli

    data = yaml.safe_load(SCENE.read_text(encoding="utf-8"))
    second = dict(data["racks"][0], id="bay2", modules=[],
                  at=[28 * 5.08, 0.0])
    data["racks"].append(second)
    scene = tmp_path / "two.yaml"
    scene.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert cli.main(["build", str(scene), "-o", str(tmp_path / "o")]) == 1
    out = capsys.readouterr().out
    assert "overlap" in out and "[X] build" in out


# --------------------------------------------------------------------------
# the box starter
# --------------------------------------------------------------------------


def _starter_scene(uncomment_bay: bool):
    import yaml

    from hwcase.api import _starter_text
    from hwcase.schema import Scene

    text = _starter_text("new-box", "box")
    if uncomment_bay:
        lines = text.splitlines()
        i = lines.index("# racks:")
        j = i
        while j < len(lines) and lines[j].startswith("#"):
            j += 1
        lines[i:j] = [ln[2:] for ln in lines[i:j]]
        text = "\n".join(lines)
    return Scene.model_validate(yaml.safe_load(text))


def test_the_box_starter_builds_with_nothing_placed():
    model = case_mod.build(resolve(_starter_scene(False), LIB))
    assert len(model.plates) == 6 and model.height > 20.0


def test_the_box_starters_sample_bay_builds_when_uncommented():
    scene = _starter_scene(True)
    assert scene.racks[0].hp == 28 and scene.placements == []
    res = resolve(scene, LIB)
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    model = case_mod.build(res)
    assert {"bay1-cheek-left", "bay1-cheek-right"} <= {p.name for p in model.plates}
