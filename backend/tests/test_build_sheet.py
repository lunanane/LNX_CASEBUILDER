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
