"""The hybrid case: a laser-cut Eurorack frame with parts sections.

The fixture is the user's own: 2 x 3U rows, 60 HP, and a 300 mm parts
section below the rack holding NeoTrellis buttons, encoders and an OLED.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml
from shapely.geometry import Point, Polygon

from hwcase import case as case_mod
from hwcase import export, flat, rack
from hwcase import joinery as jn
from hwcase import hybrid
from hwcase.box import BoxModel, box_to_json
from hwcase.library import PartLibrary, load_scene
from hwcase.scene import check, resolve
from hwcase.schema import HybridSection, Scene

LIB = PartLibrary.load()
SCENES = Path(__file__).resolve().parents[1] / "scenes"
SCENE = SCENES / "hybrid-6u-60hp.yaml"
HP = 5.08


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
    return [i for i in check(resolve(scene, LIB), LIB) if i.level == "error"]


def _moved(scene, pid, pos):
    return scene.model_copy(update={"placements": [
        p.model_copy(update={"pos": pos}) if p.id == pid else p
        for p in scene.placements]})


def _round_holes(geom, diameter, tol=0.05):
    out = []
    for ring in geom.interiors:
        hole = Polygon(ring)
        c = hole.centroid
        r = max(Point(p).distance(c) for p in ring.coords)
        if abs(2 * r - diameter) < tol and hole.area > 0.9 * np.pi * r * r:
            out.append((c.x, c.y))
    return out


def _validate(m):
    for a, b, j in m.joints:
        pa, pb = m.plate(a), m.plate(b)
        problems = [v for v in jn.validate_joint(
            j, jn.Plate(pa.geom, pa.material), jn.Plate(pb.geom, pb.material))
            if v.rule != "keep_clear"]
        assert problems == [], (a, b, problems)


# --------------------------------------------------------------------------
# the user's example
# --------------------------------------------------------------------------


def test_the_example_builds_with_no_errors(res, model):
    assert model.kind == "hybrid"
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    assert {p.name for p in model.plates} == {
        "side-left", "side-right", "deck-below1", "floor-below1",
        "floor-rack", "end-bottom", "end-top", "step-below1-rack"}


def test_the_rail_span_is_the_width_of_the_case(model):
    """No left or right walls: the side panels stand on the rails' ends,
    and every plate runs from one side panel's outer face to the other's."""
    width = 60 * HP
    t = 5.0
    assert [m.role for m in model.members] == ["rail"] * 4
    for m in model.members:
        assert m.material.thickness == pytest.approx(width)
    assert model.plate("side-left").origin[0] == pytest.approx(-t)
    assert model.plate("side-right").origin[0] == pytest.approx(width)
    for p in model.plates:
        if p.role != "side_panel":
            x0, _y0, x1, _y1 = p.geom.bounds
            assert x1 - x0 == pytest.approx(width + 2 * t, abs=0.01), p.name
    assert not [p for p in model.plates if p.name in ("left", "right")]


def test_the_deck_top_is_the_module_face(res, model):
    """D-3: the rails' front face sits module_panel_t under the deck top,
    which is the panel plane the parts are flush with."""
    deck = model.plate("deck-below1")
    top = deck.origin[2] + deck.material.thickness
    assert top == pytest.approx(res.panels["main"])
    for rail in model.members:
        assert top - rail.origin[2] == pytest.approx(2.0)


def test_the_side_panel_follows_the_stepped_profile(model):
    """The rack's floor is 42 mm under the deck, the section's 40: the side
    panel steps between them, and reaches past every plate by the overhang
    (a sheet with T-slots; 10.5 past the end walls)."""
    hy = model.extras["hybrid"]
    zones = {z["name"]: z for z in hy["zones"]}
    assert zones["rack"]["depth"] == pytest.approx(42.0)
    assert zones["below1"]["depth"] == pytest.approx(40.0)
    cheek = model.plate("side-left").geom
    t = 5.0
    y0, z0, y1, z1 = cheek.bounds
    assert y0 == pytest.approx(zones["below1"]["y0"] - 10.5, abs=0.01)
    assert y1 == pytest.approx(zones["rack"]["y1"] + 10.5, abs=0.01)
    assert z1 == pytest.approx(t, abs=0.01)                 # front: a sheet
    assert z0 == pytest.approx(-42.0 - t - t, abs=0.01)     # rack floor + back
    # under the section the panel stops a sheet past the shallower floor
    mid = zones["below1"]["y0"] + 150.0
    column = cheek.intersection(Polygon([(mid - 0.1, -200), (mid + 0.1, -200),
                                         (mid + 0.1, 200), (mid - 0.1, 200)]))
    assert column.bounds[1] == pytest.approx(-40.0 - t - t, abs=0.01)
    # and the front corners are rounded, the back ones square
    assert not cheek.buffer(1e-6).covers(Point(y0 + 0.5, z1 - 0.5))
    assert cheek.buffer(1e-6).covers(Point(y0 + 0.5, -40.0 - 2 * t + 0.5))


def test_the_m5s_meet_the_rail_ends(model):
    """Every rail end has an M5 hole through both side panels, at the rail's
    mid height and the bore's depth."""
    ends = sorted((round(m.origin[1] + rack.RAIL_H / 2, 6),
                   round(m.origin[2] - rack.rail_bore_depth(), 6))
                  for m in model.members)
    for side in ("left", "right"):
        p = model.plate(f"side-{side}")
        holes = sorted(p.to_world(u, v)[1:]
                       for u, v in _round_holes(p.geom, rack.M5_CLEAR))
        assert len(holes) == len(ends) == 4
        for (y1, z1), (y2, z2) in zip(holes, ends):
            assert y1 == pytest.approx(y2, abs=0.01)
            assert z1 == pytest.approx(z2, abs=0.01)


def test_joints_validate_and_plates_fit_the_stock(model):
    _validate(model)
    jointed = {(a, b) for a, b, _j in model.joints}
    for p in model.plates:
        if p.role != "side_panel":
            assert {("side-left", p.name), ("side-right", p.name)} <= jointed
    sheets = export.pack_sheets(flat.from_box(model), stock=model.spec)
    placed = {pl.part.name for s in sheets for pl in s.placements}
    assert placed == {p.name for p in model.plates}


def test_the_parts_cut_the_deck_and_get_standoffs(res, model):
    from shapely.affinity import translate

    deck = model.plate("deck-below1")
    world = translate(deck.geom, deck.origin[0], deck.origin[1])
    pads = [s.poly for s in res.solids
            if s.placement in ("trellis_a", "trellis_b") and s.name == "pcb"]
    buttons = [Polygon(r) for r in world.interiors
               if Polygon(r).area > 80.0
               and any(p.contains(Polygon(r).centroid) for p in pads)]
    assert len(buttons) == 32
    through = {s.through for s in model.standoffs}
    assert through == {"deck", "floor-below1"}


def test_exports_svg_dxf_and_the_cut_list(model):
    for fmt in ("svg", "dxf"):
        files = dict(export.sheet_files(model, fmt, "h"))
        assert any(n.endswith("." + fmt) for n in files)
        cut = files["h-cutlist.txt"]
    assert "hybrid -- 60 HP of 3U + 3U" in cut
    for word in ("side-left", "side-right", "deck-below1", "floor-rack",
                 "end-bottom", "end-top", "step-below1-rack", "standoffs:"):
        assert word in cut
    assert "4 x Eurorack rail, 304.80 mm (60 HP)" in cut
    assert "8 x M5x16 screw" in cut
    assert "tap both ends of every rail M5" in cut
    assert " bolt" in cut and " nut" in cut


def test_the_editor_gets_plates_rails_and_the_zones(model):
    data = box_to_json(model)
    assert data["kind"] == "hybrid"
    assert len(data["plates"]) == 8 and len(data["members"]) == 4
    assert [z["name"] for z in data["extras"]["hybrid"]["zones"]] == [
        "below1", "rack"]
    for p in data["plates"] + data["members"]:
        m = np.array(p["matrix"], float).reshape(4, 4)[:3, :3]
        assert np.linalg.det(m) == pytest.approx(1.0)


def test_the_command_line_builds_it(tmp_path, capsys):
    from hwcase import cli

    assert cli.main(["build", str(SCENE), "-o", str(tmp_path)]) == 0
    names = [p.name for p in tmp_path.rglob("*")]
    assert "hybrid-6u-60hp-cutlist.txt" in names
    assert any(n.endswith(".dxf") for n in names)


# --------------------------------------------------------------------------
# every part held to its section
# --------------------------------------------------------------------------


def test_a_part_in_the_racks_space_is_an_error():
    errors = _errors(_moved(_scene(), "oled", (60.0, 50.0, 0.0)))
    assert any(e.code == "in_rack" and e.refs == ["oled"] for e in errors)


def test_a_part_outside_every_section_is_an_error():
    errors = _errors(_moved(_scene(), "oled", (60.0, -400.0, 0.0)))
    assert any(e.code == "outside_section" and e.refs == ["oled"]
               for e in errors)


def test_a_part_deeper_than_its_section_is_an_error():
    shallow = HybridSection(id="below1", side="below", length=300.0,
                            depth=15.0)
    errors = _errors(_scene(sections=[shallow]))
    hits = {r for e in errors if e.code == "too_deep" for r in e.refs}
    assert {"trellis_a", "trellis_b"} <= hits


def test_sections_above_and_below_build():
    sections = [HybridSection(id="below1", side="below", length=300.0,
                              depth=40.0),
                HybridSection(id="top", side="above", length=120.0,
                              depth=30.0),
                HybridSection(id="below2", side="below", length=80.0,
                              depth=60.0)]
    scene = _scene(sections=sections)
    res = resolve(scene, LIB)
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    m = case_mod.build(res)
    names = {p.name for p in m.plates}
    assert {"deck-top", "floor-top", "deck-below2", "floor-below2",
            "step-below2-below1", "step-rack-top"} <= names
    zones = [z["name"] for z in m.extras["hybrid"]["zones"]]
    assert zones == ["below2", "below1", "rack", "top"]
    _validate(m)
    # the side panel runs the whole stack, one piece
    cheek = m.plate("side-left").geom
    assert cheek.geom_type == "Polygon"
    hy = m.extras["hybrid"]["zones"]
    assert cheek.bounds[2] - cheek.bounds[0] == pytest.approx(
        hy[-1]["y1"] - hy[0]["y0"] + 2 * 10.5, abs=0.01)


def test_equal_depths_share_a_floor_and_need_no_step():
    sections = [HybridSection(id="below1", side="below", length=300.0,
                              depth=42.0)]
    m = case_mod.build(resolve(_scene(sections=sections), LIB))
    names = {p.name for p in m.plates}
    assert "floor-below1-rack" in names
    assert not [n for n in names if n.startswith("step-")]
    _validate(m)


@pytest.mark.parametrize("joint", ["tslot", "finger"])
def test_both_joint_types_build(joint):
    scene = _scene()
    spec = scene.rack.model_copy(update={"joint": joint})
    m = case_mod.build(resolve(scene.model_copy(update={"rack": spec}), LIB))
    _validate(m)
    # a step wall stands inside the side panel above the shallower floor:
    # an interior joint needs a slot, so it is T-slotted either way
    kinds = {(b.startswith("step-"), j.kind) for _a, b, j in m.joints}
    assert kinds == {(False, joint), (True, "tslot")}


def test_a_radius_that_eats_the_joints_is_refused():
    scene = _scene()
    panel = scene.rack.panel.model_copy(update={"r_bottom_back": 30.0})
    spec = scene.rack.model_copy(update={"panel": panel})
    with pytest.raises(ValueError, match="corner radius"):
        case_mod.build(resolve(scene.model_copy(update={"rack": spec}), LIB))


# --------------------------------------------------------------------------
# the starter, and what moved
# --------------------------------------------------------------------------


def test_the_starter_builds_with_nothing_placed():
    from hwcase.api import _starter_text

    scene = Scene.model_validate(yaml.safe_load(
        _starter_text("new-hybrid", "hybrid")))
    assert scene.kind == "hybrid" and scene.placements == []
    assert scene.rack.hp == 60 and len(scene.rack.rows) == 2
    assert [(s.side, s.length) for s in scene.sections] == [("below", 300.0)]
    res = resolve(scene, LIB)
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    m = case_mod.build(res)
    assert {"side-left", "side-right", "deck-below1"} <= {
        p.name for p in m.plates}
    files = dict(export.sheet_files(m, "svg", "s"))
    assert "s-cutlist.txt" in files


def test_an_old_box_with_rack_bays_loads_and_says_where_they_went():
    data = yaml.safe_load((SCENES / "hybrid-trellis-28hp.yaml").read_text(
        encoding="utf-8"))
    data.update(kind="parts", case={"style": "box"},
                racks=[{"id": "bay", "at": [0.0, 0.0], "hp": 28,
                        "modules": [{"id": "amy", "hp_slot": 0}]}])
    data.pop("sections")
    scene = Scene.model_validate(data)
    issues = check(resolve(scene, LIB), LIB)
    notes = [i for i in issues if i.code == "rack_bay"]
    assert len(notes) == 1 and notes[0].level == "warning"
    assert "hybrid case" in notes[0].message
    model = case_mod.build(resolve(scene, LIB))
    assert model.kind == "box" and not model.members
    assert "racks" not in Scene().model_dump(mode="json")


def test_the_trellis_scene_builds():
    res = resolve(load_scene(SCENES / "hybrid-trellis-28hp.yaml"), LIB)
    assert [i for i in check(res, LIB) if i.level == "error"] == []
    m = case_mod.build(res)
    _validate(m)
    assert len(m.members) == 2


def test_the_build_route_returns_the_hybrid():
    from fastapi.testclient import TestClient

    from hwcase.api import app

    body = _scene().model_dump(mode="json")
    client = TestClient(app)
    r = client.post("/api/build", json=body)
    assert r.status_code == 200 and r.json()["kind"] == "hybrid"
    r = client.post("/api/export/sheets/svg?as_json=true", json=body)
    assert r.status_code == 200 and r.json()["count"] >= 1
    r = client.get("/api/scene/starter?kind=hybrid")
    assert r.status_code == 200 and "kind: hybrid" in r.text
