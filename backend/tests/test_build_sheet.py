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


def test_module_fixings_are_sized_by_hp_and_called_an_estimate():
    model = rack.build(SIX_U, 84.0)
    fix = model.module_fixings()
    assert [f["row"] for f in fix] == [1, 2]
    assert all(f["screws"] == 2 * 11 and f["screw"] == "M3x6" for f in fix)
    bom = model.bill_of_materials()
    assert bom["M3x6 module screws (estimate)"] == 44
    assert bom["M3 sliding nuts (estimate)"] == 44
    cut = export.rack_sheet_files(model, "svg", "f", WIDE)[-1][1]
    assert "module fixings (an ESTIMATE" in cut
    assert "row 2 (3U, 84 HP): ~22 x M3x6 screw + ~22 x M3 sliding nut" in cut
