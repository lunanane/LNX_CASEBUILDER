"""M4: parts shown as their vendor models; checks and cuts still on boxes."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from hwcase import case as case_mod
from hwcase import export, models, raytrace
from hwcase.catalog import CatalogEntry
from hwcase.geom import Frame
from hwcase.ingest import draft_part
from hwcase.library import PartLibrary, load_scene
from hwcase.scene import resolve
from hwcase.schema import Part

LIB = PartLibrary.load()
MODELLED = ["adafruit-4741-oled-1v5", "adafruit-3954-neotrellis",
            "adafruit-5752-quad-encoder"]
SCENES = Path(__file__).resolve().parents[1] / "scenes"

vendored = pytest.mark.skipif(
    not all((models.CAD_ROOT / LIB[p].model.file).exists() for p in MODELLED),
    reason="vendored Adafruit CAD is not on disk")


@vendored
@pytest.mark.parametrize("pid", MODELLED)
def test_row1_the_vendored_parts_have_models_within_the_budget(pid):
    part = LIB[pid]
    glb = models.glb_for(part)
    scene = trimesh.load(glb, force="scene")
    faces = sum(len(m.faces) for m in scene.geometry.values())
    assert 0 < faces <= models.TRIANGLE_BUDGET
    assert len(scene.geometry) == 2                 # board, and metal


@vendored
@pytest.mark.parametrize("pid", MODELLED)
def test_row2_each_model_sits_on_its_parts_boxes(pid):
    assert models.alignment(LIB[pid]) == []


def test_row2_a_misplaced_model_is_caught():
    part = LIB["adafruit-4741-oled-1v5"]
    shifted = part.model.matrix.copy()
    shifted[3] += 2.0                                # 2 mm along x
    moved = part.model_copy(update={"model": part.model.model_copy(
        update={"matrix": shifted})})
    assert any("x min" in p for p in models.alignment(moved))


@vendored
def test_row2_the_audit_holds_the_models_to_it():
    from hwcase.audit import audit_part

    for pid in MODELLED:
        found = [f for f in audit_part(LIB[pid]).findings if f.kind == "model"]
        assert found == [], (pid, found)


def test_row3_models_change_nothing_the_engine_computes():
    """Strip every model and the build is byte for byte the same."""
    scene = load_scene(SCENES / "soundmachine-v0.yaml")
    with_models = export.case_to_json(case_mod.build(resolve(scene, LIB)))

    class Bare:
        def __init__(self, lib):
            self.lib = lib

        def __getattr__(self, name):
            return getattr(self.lib, name)

        def __getitem__(self, pid):
            p = self.lib[pid]
            return p.model_copy(update={"model": None})

        def __contains__(self, pid):
            return pid in self.lib

    bare = export.case_to_json(case_mod.build(resolve(scene, Bare(LIB))))
    assert json.dumps(with_models, sort_keys=True) == \
        json.dumps(bare, sort_keys=True)


@vendored
def test_row4_the_photograph_uses_the_models(tmp_path):
    scene = load_scene(SCENES / "soundmachine-v0.yaml")
    res = resolve(scene, LIB)
    model = case_mod.build(res)
    d = raytrace.build_scene_dict(res, model, raytrace.RenderSettings(),
                                  tmp_path, lib=LIB)
    modelled = {pl.id for pl in scene.placements if LIB[pl.part].model}
    assert modelled
    for pid in modelled:
        assert f"model-{pid}" in d
        assert (tmp_path / f"model-{pid}.ply").exists()


def test_row5_a_part_without_a_model_is_still_drawn_as_blocks(tmp_path):
    scene = load_scene(SCENES / "soundmachine-v0.yaml")
    res = resolve(scene, LIB)
    model = case_mod.build(res)
    d = raytrace.build_scene_dict(res, model, raytrace.RenderSettings(),
                                  tmp_path, lib=LIB)
    assert any(k.startswith("part") for k in d)     # the Pi has no model
    assert LIB["rpi-3b"].model is None
    plain = raytrace.build_scene_dict(res, model, raytrace.RenderSettings(),
                                      tmp_path / "plain")
    assert not any(k.startswith("model-") for k in plain)


# --------------------------------------------------------------------------
# the plumbing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tilt,rot", list(itertools.product(
    (0, 90, 180, 270), (0.0, 37.0, 90.0, 180.0))))
def test_the_frame_matrix_is_the_frames_own_mapping(tilt, rot):
    f = Frame(pos=(12.5, -3.0, 7.25), rot_z=rot, tilt=tilt)
    m = np.array(f.matrix(), float).reshape(4, 4)
    for p in ((0, 0, 0), (10, 0, 0), (0, 20, 0), (3, -4, 5)):
        assert m @ np.array([*p, 1.0]) == pytest.approx([*f.point(p), 1.0])


def test_the_editor_gets_a_matrix_for_every_frame():
    scene = load_scene(SCENES / "soundmachine-v0.yaml")
    data = export.scene_to_json(resolve(scene, LIB), LIB)
    assert all(len(f["matrix"]) == 16 for f in data["frames"].values())


@vendored
def test_an_import_brings_its_model_and_it_lines_up():
    step = models.CAD_ROOT / "adafruit" / "4741" / "4741 1.5-inch OLED.step"
    draft = draft_part(CatalogEntry(id="4741", name="OLED"), cad=step)
    part = Part.model_validate(draft.part)
    assert part.model.file == "adafruit/4741/4741 1.5-inch OLED.step"
    assert models.alignment(part) == []


@vendored
def test_the_model_route_serves_a_glb_and_refuses_a_part_without_one():
    from fastapi.testclient import TestClient

    from hwcase.api import app

    client = TestClient(app)
    r = client.get("/api/parts/adafruit-4741-oled-1v5/model.glb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    assert r.content[:4] == b"glTF"
    assert client.get("/api/parts/rpi-3b/model.glb").status_code == 404
