"""The cut list as a build sheet: what a layered case is held together with,
and the order its sheets go on the bench."""

from __future__ import annotations

import yaml
import pytest

from hwcase import case as case_mod
from hwcase import export
from hwcase.api import _starter_text
from hwcase.fasteners import fastener
from hwcase.library import PartLibrary
from hwcase.scene import resolve
from hwcase.schema import Scene

LIB = PartLibrary.load()

#: a board on the floor of a small stack, screwed up from below
PI = {"id": "pico", "part": "rpi-3b", "pos": [0.0, 0.0, 0.0],
        "support": "from_floor"}


def _scene(placements=(), **case) -> Scene:
    raw = yaml.safe_load(_starter_text("s", "parts"))
    raw["placements"] = list(placements)
    raw["case"].update(case)
    return Scene.model_validate(raw)


def _build(scene):
    res = resolve(scene, LIB)
    return res, case_mod.build(res)


def test_a_new_layered_case_is_bolted_at_its_corners():
    scene = _scene()
    assert scene.case.case_screws.value == "corners"


def test_old_scenes_keep_no_bolts_unless_they_ask():
    from hwcase.schema import CaseSpec
    assert CaseSpec().case_screws.value == "none"


def test_the_bom_counts_the_case_bolts_at_a_stocked_length_and_their_nuts():
    _res, model = _build(_scene([PI]))
    bom = model.bill_of_materials()
    m3 = fastener("M3")
    bolts = {k: v for k, v in bom.items() if k.startswith("M3x")}
    assert len(bolts) == 1 and list(bolts.values()) == [4]
    length = float(next(iter(bolts)).split("x")[1].split()[0])
    assert length in m3.lengths
    floor, lid = model.layers[0], model.layers[-1]
    needed = lid.z0 - floor.z1 + m3.nut_t + m3.thread_past_nut
    assert length >= needed
    shorter = [x for x in m3.lengths if x < length]
    assert not shorter or shorter[-1] < needed      # the shortest that fits
    assert bom["M3 hex nut"] == 4


def test_the_bom_counts_a_screw_and_a_nut_per_board_hole():
    res, model = _build(_scene([PI]))
    holes = [s for s in res.supports if s.placement == "pico"]
    assert holes
    bom = model.bill_of_materials()
    screws = sum(v for k, v in bom.items() if k.endswith(" screw"))
    assert screws == len(holes)


def test_the_cut_list_carries_the_fixings():
    _res, model = _build(_scene([PI]))
    cut = dict(export.sheet_files(model, "svg", "s"))["s-cutlist.txt"]
    assert "fixings:" in cut
    assert "4 x M3 hex nut" in cut
    assert "nut in the floor's hex trap" in cut


# --------------------------------------------------------------------------
# the stacking sequence
# --------------------------------------------------------------------------


def test_the_cut_list_numbers_the_stack_from_the_floor_up():
    res, model = _build(_scene([PI]))
    steps = model.assembly
    assert steps[0] == "1. layer 00 floor, outside face down"
    assert steps[1].startswith("2. press the 4 M3 nuts into the floor")
    assert steps[-1].endswith("bolts down through the lid into the nuts")
    numbers = [int(s.split(".")[0]) for s in steps]
    assert numbers == list(range(1, len(steps) + 1))
    # every layer, in z order
    order = [s for s in steps if s.split(". ", 1)[1].startswith("layer ")]
    assert [s.split()[2] for s in order] == [
        f"{l.index:02d}" for l in model.layers]
    cut = dict(export.sheet_files(model, "svg", "s"))["s-cutlist.txt"]
    assert "assembly order:" in cut and steps[-1] in cut


def test_a_board_goes_in_after_the_last_layer_below_it():
    res, model = _build(_scene([PI]))
    steps = [s.split(". ", 1)[1] for s in model.assembly]
    at = next(i for i, s in enumerate(steps) if s.startswith("insert pico"))
    assert "(held by screws from below)" in steps[at]
    board = min(s.z[0] for s in res.solids
                if s.placement == "pico" and s.name == "pcb")
    before = [s for s in steps[:at] if s.startswith("layer ")]
    after = [s for s in steps[at:] if s.startswith("layer ")]
    by_index = {f"{l.index:02d}": l for l in model.layers}
    assert all(by_index[s.split()[1]].z1 <= board + 1e-6 for s in before)
    assert by_index[after[0].split()[1]].z1 > board


# --------------------------------------------------------------------------
# a frame's fixings
# --------------------------------------------------------------------------

from hwcase import rack                                    # noqa: E402
from hwcase.eurorack import ROW_FORMATS                     # noqa: E402
from hwcase.schema import CaseSpec, Material               # noqa: E402

SIX_U = [ROW_FORMATS["3U"], ROW_FORMATS["3U"]]
WIDE = CaseSpec(plates=[(600.0, 400.0)])


@pytest.mark.parametrize("t,length", [(4.0, 16.0), (5.0, 16.0), (6.0, 16.0)])
def test_the_m5_is_the_panel_plus_its_thread_rounded_to_stock(t, length):
    model = rack.build(SIX_U, 42.0, style="laser",
                       material=Material(name="ply", thickness=t))
    assert model.rail_screw_length() == length
    assert length >= t + rack.M5_ENGAGEMENT
    assert length in fastener("M5").lengths
    assert model.bill_of_materials()["M5 screw length mm"] == length
    import dataclasses
    thick = dataclasses.replace(model, panel_t=8.0)
    assert thick.rail_screw_length() == 20.0          # 18 is not stocked


def test_the_frame_cut_list_says_tap_m5_and_how_long_the_screw_is():
    model = rack.build(SIX_U, 68.0)
    cut = export.rack_sheet_files(model, "svg", "f", WIDE)[-1][1]
    assert "8 x M5 screw, 16 mm long" in cut
    assert "tap both ends of every rail M5" in cut and "4.2 mm" in cut


def test_a_frame_lists_no_module_fixings_only_the_rails_m5s():
    """The modules are whatever the user buys later: no M3s, no sliding nuts."""
    model = rack.build(SIX_U, 84.0)
    bom = model.bill_of_materials()
    assert not any("M3" in k or "sliding" in k for k in bom)
    assert bom["M5 screws"] == 2 * bom["rails"]
    cut = export.rack_sheet_files(model, "svg", "f", WIDE)[-1][1]
    assert "module fixings" not in cut and "sliding nut" not in cut
    assert "M5 screw, 16 mm long" in cut


# --------------------------------------------------------------------------
# one sheet, one kerf, whichever way a frame is exported
# --------------------------------------------------------------------------

import re                                                  # noqa: E402


def _svg_sizes(svg: str) -> list[tuple[float, float]]:
    return sorted(tuple(sorted((float(a), float(b)))) for a, b in re.findall(
        r"<title>[^<]*?, ([\d.]+) x ([\d.]+) mm -- outer edge", svg))


def _cut_sizes(cut: str) -> list[tuple[float, float]]:
    """The packed sizes -- the sheets' part of the cut list, not the plates
    measured nominal further down."""
    cut = cut.split("\nframe --")[0]
    return sorted(tuple(sorted((float(a), float(b)))) for a, b in re.findall(
        r"^  \S+\s+\S+\s+([\d.]+) x\s+([\d.]+) mm", cut, re.M))


@pytest.mark.parametrize("style", ["u_channel", "laser"])
def test_the_plate_svg_and_the_sheets_cut_the_same_sheet_at_the_same_kerf(style):
    mat = Material(name="ply-5mm", thickness=5.0, kerf=0.4)
    model = rack.build(SIX_U, 42.0, style=style,
                       material=mat if style == "laser" else None)
    svg = export.rack_panels_svg(model)
    files = export.rack_sheet_files(model, "svg", "f", WIDE)
    sheets = "".join(c for _n, c in files[:-1])
    want = mat if style == "laser" else rack.U_PANEL_MATERIAL
    assert f'data-material="{want.name}"' in svg
    assert f'data-material="{want.name}"' in sheets
    got = _svg_sizes(svg)
    packed = _cut_sizes(files[-1][1])
    assert got and len(got) == len(packed)
    for a, b in zip(got, packed):          # both printed to 0.1 mm
        assert a == pytest.approx(b, abs=0.11)


def test_the_build_names_the_frames_sheet_for_the_editor():
    u = export.rack_to_json(rack.build(SIX_U, 42.0))
    assert u["sheet"]["name"] == rack.U_PANEL_MATERIAL.name
    assert u["laser_sheet"]["name"] == rack.LASER_MATERIAL.name
    assert u["laser_sheet"]["thickness"] == rack.LASER_MATERIAL.thickness

# --------------------------------------------------------------------------
# a frame's issues
# --------------------------------------------------------------------------

from fastapi.testclient import TestClient                  # noqa: E402

from hwcase import api                                     # noqa: E402
from hwcase.schema import RackSpec                         # noqa: E402


def _codes(spec: RackSpec, stock=WIDE) -> dict[str, object]:
    model = rack.build_spec(spec)
    return {i.code: i for i in rack.frame_issues(spec, model, stock)}


def test_a_sensible_frame_has_no_issues():
    assert _codes(RackSpec(hp=42)) == {}


def test_a_raised_margin_is_a_warning():
    got = _codes(RackSpec(hp=42, panel={"bottom": 0.0}))
    assert got["margin_raised"].level == "warning"
    assert "bottom margin raised from 0.00" in got["margin_raised"].message


def test_a_laser_sheet_thinner_than_its_t_slot_needs_is_a_warning():
    thin = Material(name="ply-2mm", thickness=2.0)
    got = _codes(RackSpec(hp=42, style="laser", material=thin, module_depth=40))
    assert got["sheet_thin"].level == "warning"
    assert "2.75 mm" in got["sheet_thin"].message
    ok = _codes(RackSpec(hp=42, style="laser", joint="finger", material=thin,
                         module_depth=40))
    assert "sheet_thin" not in ok


def test_a_module_depth_under_a_skiff_is_a_warning():
    got = _codes(RackSpec(hp=42, module_depth=20.0))
    assert got["module_depth_shallow"].level == "warning"


def test_a_plate_bigger_than_the_stock_is_an_error():
    spec = RackSpec(hp=84)
    got = [i for i in rack.frame_issues(spec, rack.build_spec(spec),
                                        CaseSpec(plates=[(150.0, 150.0)]))
           if i.code == "plate_too_big"]
    assert [i.level for i in got] == ["error", "error"]
    assert "side-left" in got[0].message and "side-right" in got[1].message


def test_the_build_route_carries_a_frames_issues():
    scene = Scene.model_validate({
        "name": "f", "kind": "rack",
        "rack": {"hp": 42, "panel": {"bottom": 0.0}},
        "case": {"plates": [[150.0, 150.0]]}})
    r = TestClient(api.app).post("/api/build",
                                 json=scene.model_dump(mode="json"))
    assert r.status_code == 200, r.text
    codes = {i["code"]: i["level"] for i in r.json()["issues"]}
    assert codes == {"margin_raised": "warning", "plate_too_big": "error"}
