"""Materials, fasteners and the joints cut from them (milestone M6)."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from hwcase.export import _material_json
from hwcase.fasteners import FASTENERS, fastener
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
