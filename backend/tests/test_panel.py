"""M8: a Eurorack module panel with real boards behind it."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hwcase import case as case_mod
from hwcase import export, panel
from hwcase.api import _starter_text
from hwcase.eurorack import HP
from hwcase.library import PartLibrary
from hwcase.scene import check, resolve
from hwcase.schema import CaseSpec, Scene

LIB = PartLibrary.load()
REPO = Path(__file__).resolve().parents[2]

#: the quad encoder, its four shafts through the panel, screwed up behind it
ENCODER = {"id": "enc", "part": "adafruit-5752-quad-encoder",
           "pos": [2.35, 50.0, 0.0], "on_panel": "main",
           "panel_ref": "bushings", "support": "from_lid"}


def _scene(hp=16.0, depth=40.0, placements=(), engravings=()) -> Scene:
    return Scene.model_validate({
        "name": "p",
        "panels": [{"name": "main", "z": 0.0}],
        "placements": list(placements),
        "engravings": list(engravings),
        "case": {"materials": [{"name": "aluminium-2mm", "thickness": 2.0}],
                 "eurorack_panel": {"hp": hp, "module_depth": depth}},
    })


def _build(scene):
    res = resolve(scene, LIB)
    return res, case_mod.build(res)


def _rings(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    return sum(len(p.interiors) for p in polys)


# --------------------------------------------------------------------------
# the format
# --------------------------------------------------------------------------


def test_row1_a_10_hp_3u_panel_and_its_rail_holes():
    _res, model = _build(_scene(hp=10.0))
    assert len(model.layers) == 1
    lid = model.layers[0]
    assert lid.geom.bounds == pytest.approx((0.0, 0.0, 50.5, 128.5))
    holes = panel.rail_holes(10.0)
    assert sorted({y for _x, y, _s in holes}) == [3.0, 125.5]
    assert {x for x, _y, _s in holes} == {panel.HOLE_X}
    assert _rings(lid.geom) == 2
    assert lid.z1 == pytest.approx(0.0)                # the face is the plane
    assert lid.z1 - lid.z0 == pytest.approx(2.0)


def test_row2_a_12_hp_panel_has_four_rail_holes_on_the_hp_grid():
    holes = panel.rail_holes(12.0)
    assert len(holes) == 4
    xs = sorted({x for x, _y, _s in holes})
    assert xs[0] == pytest.approx(7.5)
    gap = xs[1] - xs[0]
    assert gap / HP == pytest.approx(round(gap / HP))  # a whole number of HP
    width = 60.6                                       # Doepfer's 12 HP
    assert xs[1] == pytest.approx(width - 7.5, abs=HP / 2)


@pytest.mark.parametrize("hp", [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 28, 42])
def test_every_rail_hole_is_inside_its_panel_with_material_around_it(hp):
    out = panel.outline(hp)
    for _x, _y, shape in panel.rail_holes(hp):
        assert out.buffer(-0.5).contains(shape)


def test_rail_holes_are_ovals_a_rail_screw_can_slide_in():
    _x, _y, shape = panel.rail_holes(10.0)[0]
    x0, y0, x1, y1 = shape.bounds
    assert x1 - x0 == pytest.approx(panel.SLOT_W, abs=0.01)
    assert y1 - y0 == pytest.approx(panel.SLOT_H, abs=0.01)


def test_a_1u_panel_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="only 3U"):
        panel.outline(10, "1U-intellijel")


def test_the_width_is_the_table_not_the_formula():
    """6 HP is where `hp * 5.08 - 0.3` over-cuts most."""
    assert panel.outline(6).bounds[2] == pytest.approx(30.0)


# --------------------------------------------------------------------------
# boards behind the panel
# --------------------------------------------------------------------------


def test_controls_come_through_and_each_board_screw_gets_a_standoff():
    _res, model = _build(_scene(placements=[ENCODER]))
    lid = model.layers[0]
    # 4 shafts + 4 screw clearances + 4 rail holes
    assert _rings(lid.geom) == 12
    standoffs = [n for n in lid.notes if "standoff" in n]
    assert len(standoffs) == 4
    assert all("enc:" in n and "(M2)" in n for n in standoffs)


def test_the_exported_cut_list_gives_every_board_its_standoff():
    """The starter promises it: the paper that goes to the bench carries the
    standoffs, not only the layer's notes in the editor."""
    _res, model = _build(_scene(placements=[ENCODER]))
    files = dict(export.sheet_files(model, "svg", "p"))
    cut = files["p-cutlist.txt"]
    lines = [ln for ln in cut.splitlines() if "standoff" in ln]
    assert len(lines) == 4
    assert all("enc:" in ln and "(M2)" in ln for ln in lines)
    for note in model.layers[0].notes:
        assert note in cut


def test_row3_a_part_deeper_than_the_module_depth_is_an_error():
    res = resolve(_scene(placements=[ENCODER], depth=10.0), LIB)
    errors = [i for i in check(res, LIB) if i.code == "module_depth"]
    assert errors and errors[0].level == "error"
    assert errors[0].refs == ["enc"]

    res = resolve(_scene(placements=[ENCODER], depth=40.0), LIB)
    assert not [i for i in check(res, LIB) if i.code == "module_depth"]


def test_a_power_headers_ribbon_counts_against_the_depth():
    header = {"id": "pwr", "part": "eurorack-power-header-10",
              "pos": [30.0, 10.0, -20.0], "mount": "manual", "tilt": 180}
    res = resolve(_scene(placements=[header], depth=40.0), LIB)
    refs = [i.refs for i in check(res, LIB) if i.code == "module_depth"]
    assert refs == [["pwr"]]


# --------------------------------------------------------------------------
# getting it cut
# --------------------------------------------------------------------------


def test_row4_one_panel_holes_first_labels_on_the_engraving_layer():
    label = {"name": "title", "pattern": "text", "text": "LNX",
             "at": [40.0, 110.0], "size": [40.0, 12.0], "text_size": 6.0}
    scene = _scene(placements=[ENCODER], engravings=[label])
    scene.case.plates = [(350.0, 350.0)]
    _res, model = _build(scene)
    files = dict(export.sheet_files(model, "svg", "p", stock=scene.case))
    sheets = [n for n in files if n.endswith(".svg")]
    assert len(sheets) == 1
    svg = files[sheets[0]]
    assert svg.index('data-pass="holes"') < svg.index('data-pass="outline"')
    assert "engrav" in svg.lower()
    dxf = dict(export.sheet_files(model, "dxf", "p", stock=scene.case))
    assert any("_ENGRAVE" in t for n, t in dxf.items() if n.endswith(".dxf"))


def test_row5_no_document_or_part_repeats_the_rail_spacing_error():
    needle = "123" + ".5"                   # so this file does not match itself
    hits = []
    for root in ("docs", "backend"):
        for path in (REPO / root).rglob("*"):
            if path.suffix not in (".md", ".yaml", ".py", ".html", ".txt"):
                continue
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8",
                                                    errors="replace")
                                     .splitlines(), 1):
                if needle in line:
                    hits.append(f"{path.relative_to(REPO)}:{n}")
    assert hits == []


def test_the_amyboard_panels_rail_holes_are_122_5_apart():
    p = LIB["shorepine-amyboard-panel"]
    ys = sorted({h.at[1] for h in p.holes})
    assert ys == [3.0, 125.5]
    assert ys[1] - ys[0] == pytest.approx(122.5)


def test_row6_a_case_without_a_panel_is_the_layered_case_as_before():
    assert CaseSpec().eurorack_panel is None


def test_the_starter_is_a_panel_project_that_builds():
    raw = yaml.safe_load(_starter_text("my-panel", "panel"))
    scene = Scene.model_validate(raw)
    assert scene.kind == "parts"
    assert scene.case.eurorack_panel.hp == 10
    _res, model = _build(scene)
    assert model.layers[0].geom.bounds == pytest.approx((0, 0, 50.5, 128.5))
