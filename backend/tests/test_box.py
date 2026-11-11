"""M9: a parts scene as a box of jointed plates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import translate
from shapely.geometry import Point, Polygon

from hwcase import case as case_mod
from hwcase import export, flat
from hwcase import joinery as jn
from hwcase.box import BoxModel, box_to_json
from hwcase.library import PartLibrary, load_scene
from hwcase.scene import resolve
from hwcase.schema import CaseSpec, VolumeKind

LIB = PartLibrary.load()
SCENE = Path(__file__).resolve().parents[1] / "scenes" / "soundmachine-v0.yaml"
WIDE = CaseSpec(plates=[(600.0, 400.0)])


@pytest.fixture(scope="module")
def res():
    return resolve(load_scene(SCENE), LIB)


def _box(res, joint="tslot", t=3.0) -> BoxModel:
    spec = res.scene.case
    mats = [m.model_copy(update={"thickness": t}) for m in spec.materials]
    spec = spec.model_copy(update={"style": "box", "box_joint": joint,
                                   "materials": mats})
    return case_mod.build(res, spec)


def _wall_uv(plate, p):
    """World point -> the plate's own (u, v)."""
    o = np.array(plate.origin, float)
    u, v, _w = (np.array(a, float) for a in plate.axes)
    d = np.array(p, float) - o
    return float(d @ u), float(d @ v)


BOXES = [("tslot", 3.0), ("finger", 3.0), ("finger", 6.0)]


@pytest.mark.parametrize("joint,t", BOXES)
def test_row1_the_deck_has_every_opening_the_layered_lid_has(res, joint, t):
    """Support screws aside: a layered lid bores those at head size (a well
    down to the seat), a box's deck is the seat and takes a clearance hole.
    The lid compared is cut from the same sheet: a thicker slab reaches
    further down and meets different hardware."""
    mats = [m.model_copy(update={"thickness": t})
            for m in res.scene.case.materials]
    lid = case_mod.build(
        res, res.scene.case.model_copy(update={"materials": mats})).layers[-1]
    model = _box(res, joint, t)
    x0, y0, _x1, _y1 = model.outer.bounds
    deck = translate(model.plate("deck").geom, x0, y0)
    deck_holes = [Polygon(r) for r in deck.interiors]
    screws = [Point(*s.at) for s in res.supports]
    checked = 0
    for ring in lid.geom.interiors:
        hole = Polygon(ring)
        if any(hole.contains(p) for p in screws):
            continue
        checked += 1
        assert any(hole.hausdorff_distance(d) <= 0.1 for d in deck_holes), \
            f"lid opening at {hole.centroid} is missing from the deck"
    # the scene's own 3 mm sheet has dozens; a 6 mm slab merges its openings
    # into a few large ones that break the outline, so fewer are interior
    assert checked >= (40 if t == 3.0 else 1)


@pytest.mark.parametrize("joint,t", BOXES)
def test_row2_every_port_opens_the_wall_it_faces(res, joint, t):
    model = _box(res, joint, t)
    walls = {(0, -1): "front", (0, 1): "back", (-1, 0): "left", (1, 0): "right"}
    seen = 0
    for wc in res.connectors:
        if not wc.cuts_the_wall or wc.policy.value != "per_connector":
            continue
        wall = walls.get((round(wc.face_normal[0]), round(wc.face_normal[1])))
        if wall is None:
            continue
        plate = model.plate(wall)
        u, v = _wall_uv(plate, wc.at)
        assert not plate.geom.contains(Point(u, v)), \
            f"{wc.ref} is behind solid {wall} wall"
        seen += 1
    assert seen >= 3


def _final(model, name):
    p = model.plate(name)
    return jn.Plate(p.geom, p.material)


@pytest.mark.parametrize("joint,t", BOXES)
def test_row3_joints_validate_and_every_plate_fits_the_stock(res, joint, t):
    model = _box(res, joint, t)
    assert model.joints
    for flat_name, wall, j in model.joints:
        problems = [v for v in jn.validate_joint(j, _final(model, flat_name),
                                                 _final(model, wall))
                    if v.rule != "keep_clear"]
        assert problems == [], (flat_name, wall, problems)
    parts = flat.from_box(model)
    sheets = export.pack_sheets(parts, stock=WIDE)
    assert {pl.part.name for s in sheets for pl in s.placements} == \
        {"deck", "floor", "front", "back", "left", "right"}
    for p in model.plates:
        assert p.geom.geom_type == "Polygon" and p.geom.is_valid


def test_row4_a_case_spec_still_defaults_to_layers():
    assert CaseSpec().style == "layered"


def test_row5_every_supported_board_has_a_standoff_in_the_cut_list(res):
    model = _box(res)
    assert len(model.standoffs) == len(res.supports)
    for s in model.standoffs:
        assert s.length > 0
    files = dict(export.sheet_files(model, "svg", "sm", stock=WIDE))
    cut = files["sm-cutlist.txt"]
    assert cut.count(" through the ") == len(res.supports)
    assert "M3 hex nut" in cut


def test_the_deck_is_the_panel_plane_and_the_floor_clears_the_hardware(res):
    model = _box(res)
    assert model.z1 == pytest.approx(max(res.panels.values()))
    floor = model.plate("floor")
    t = floor.material.thickness
    lowest = min(s.z[0] for s in res.solids)
    assert floor.origin[2] + t <= lowest + 1e-9


def test_every_plate_matrix_is_a_rotation_not_a_mirror(res):
    for p in _box(res).plates:
        m = np.array(p.matrix, float).reshape(4, 4)[:3, :3]
        assert np.linalg.det(m) == pytest.approx(1.0)


def test_walls_stand_inside_the_deck_by_a_sheet_for_t_slots(res):
    model = _box(res, "tslot", 3.0)
    x0, y0, _x1, _y1 = model.outer.bounds
    front = model.plate("front")
    assert front.origin[0] == pytest.approx(x0 + 3.0)
    assert front.origin[1] - 3.0 == pytest.approx(y0 + 3.0)


def test_a_wall_too_full_of_ports_for_a_thick_t_slot_is_refused(res):
    with pytest.raises(ValueError, match="left wall cannot be jointed"):
        _box(res, "tslot", 6.0)


def test_unjointed_stretches_are_reported_not_hidden(res):
    model = _box(res, "finger", 6.0)
    assert any("not jointed" in n for n in model.notes)


def test_the_editor_gets_each_plate_with_its_placement(res):
    data = box_to_json(_box(res))
    assert data["kind"] == "box"
    assert len(data["plates"]) == 6
    for p in data["plates"]:
        assert len(p["matrix"]) == 16 and p["rings"]
    assert data["standoffs"]


def test_the_command_line_builds_a_box_into_sheets(res, tmp_path, capsys):
    from hwcase import cli

    scene = tmp_path / "boxed.yaml"
    text = SCENE.read_text(encoding="utf-8").replace(
        "  style: layered", "  style: box")
    assert "style: box" in text
    scene.write_text(text, encoding="utf-8")
    assert cli.main(["build", str(scene), "-o", str(tmp_path / "out")]) == 0
    written = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert any(n.endswith(".svg") for n in written)
    assert any(n.endswith(".dxf") for n in written)
    assert any(n.endswith("cutlist.txt") for n in written)
    assert "box:" in capsys.readouterr().out


def test_the_box_starter_is_a_parts_project_whose_case_is_a_box():
    """The project chooser offers a box case on its own, so a new one has to
    start as one -- not as a layered case the user then has to find the
    construction dropdown to change."""
    import yaml
    from hwcase.api import STARTERS, _starter_text
    from hwcase.schema import Scene

    assert "box" in STARTERS
    scene = Scene.model_validate(yaml.safe_load(_starter_text("my-box", "box")))
    assert scene.kind == "parts"
    assert scene.case.style == "box"
    assert scene.case.eurorack_panel is None
