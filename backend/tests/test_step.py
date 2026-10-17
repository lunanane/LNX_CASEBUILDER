"""M2: STEP import, and checking an import before trusting it.

The three vendored Adafruit models exist both as STL and as STEP, which makes
them a ground truth: the same board read two ways has to give the same answer,
holes included. The synthetic boards below cover what no vendored model does --
a round board, a board with slots, holes read in the wrong frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import shapely
import trimesh
from shapely.geometry import Point, box

from hwcase import ingest, measure
from hwcase.catalog import CatalogEntry
from hwcase.measure import Body, HoleGroup, board_footprint, bodies, hole_groups
from hwcase.schema import Part

CAD = Path(__file__).resolve().parents[2] / "vendor" / "cad" / "adafruit"

#: product -> (STL, STEP) as vendored. The STL is the older import path.
MODELS = {
    "3954": ("Adafruit NeoTrellis 4x4 v2.stl", "Adafruit NeoTrellis 4x4.step"),
    "4741": ("4741 1.5-inch OLED.stl", "4741 1.5-inch OLED.step"),
    "5752": ("5752 Quad Rotary Breakout.stl", "5752 Quad Rotary Breakout.step"),
}

vendored = pytest.mark.skipif(
    not all((CAD / pid / f).exists() for pid, fs in MODELS.items() for f in fs),
    reason="vendored Adafruit CAD is not on disk")

#: Needs shapely 2.1's constrained triangulation, to build fixtures without a
#: CAD kernel or a triangulation engine.
synthetic = pytest.mark.skipif(
    not hasattr(shapely, "constrained_delaunay_triangles"),
    reason="needs shapely >= 2.1 to build the synthetic boards")


def _entry(pid: str = "9999", name: str = "Test board") -> CatalogEntry:
    return CatalogEntry(id=pid, name=name)


def _holes(part: dict) -> list[tuple[float, float, float]]:
    return sorted((round(h["at"][0], 1), round(h["at"][1], 1), h["diameter"])
                  for h in part.get("holes", []))


def _outline_bounds(part: dict) -> tuple[float, float, float, float]:
    from hwcase.geom import outline_polygon

    return outline_polygon(Part.model_validate(part).outline).bounds


def _slab(poly, thickness: float = 1.6) -> trimesh.Trimesh:
    """A watertight board: `poly` (holes and all) extruded."""
    tris = shapely.constrained_delaunay_triangles(poly)
    index: dict[tuple[float, float], int] = {}
    verts: list[tuple[float, float]] = []
    faces: list[list[int]] = []
    for t in tris.geoms:
        face = []
        for x, y in list(shapely.geometry.polygon.orient(t).exterior.coords)[:3]:
            key = (round(x, 6), round(y, 6))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            face.append(index[key])
        faces.append(face)
    return trimesh.creation.extrude_triangulation(
        np.array(verts), np.array(faces), thickness)


def _board_stl(tmp_path: Path, poly, name: str = "board.stl") -> Path:
    path = tmp_path / name
    _slab(poly).export(str(path))
    return path


# --------------------------------------------------------------------------
# reading STEP
# --------------------------------------------------------------------------


@vendored
@pytest.mark.parametrize("pid", sorted(MODELS))
def test_a_step_arrives_in_millimetres(pid):
    """The file says metres; the board must still be 60 mm, not 0.06."""
    _, step = MODELS[pid]
    found = bodies(CAD / pid / step)
    board = max((b for b in found if 0.6 <= b.height <= 2.6),
                key=lambda b: b.footprint)
    assert 20.0 < max(board.size[0], board.size[1]) < 100.0
    assert board.height == pytest.approx(1.57, abs=0.02)


@vendored
@pytest.mark.parametrize("pid", sorted(MODELS))
def test_step_and_stl_imports_agree(pid):
    """Row 1: outline within 0.3 mm, and the holes land in the same place."""
    stl, step = MODELS[pid]
    via_stl = ingest.draft_part(_entry(pid), cad=CAD / pid / stl,
                                step=CAD / pid / step)
    via_step = ingest.draft_part(_entry(pid), cad=CAD / pid / step)

    a, b = via_stl.part, via_step.part
    for ca, cb in zip(_outline_bounds(a), _outline_bounds(b)):
        assert ca == pytest.approx(cb, abs=0.3)
    assert a["pcb_thickness"] == pytest.approx(b["pcb_thickness"], abs=0.05)
    assert via_stl.flipped == via_step.flipped
    assert _holes(a) == _holes(b), "holes read from the STEP geometry moved"
    assert _holes(b), "a board with mounting holes imported with none"
    Part.model_validate(b)                    # and it is a legal part


@vendored
def test_the_neotrellis_has_its_eight_m25_holes_from_the_step_geometry():
    """The README's claim, checked against the geometry rather than the text."""
    part = ingest.draft_part(_entry("3954"),
                             cad=CAD / "3954" / MODELS["3954"][1]).part
    assert len(part["holes"]) == 8
    assert {h["screw"] for h in part["holes"]} == {"M2.5"}
    assert part["holes"][0]["src"]["note"] == "from the STEP geometry"


@vendored
def test_a_step_only_product_imports(tmp_path, monkeypatch):
    """Row 2: a folder with no STL at all."""
    step = tmp_path / MODELS["4741"][1]
    step.write_bytes((CAD / "4741" / MODELS["4741"][1]).read_bytes())
    monkeypatch.setattr(ingest, "local_cad", lambda pid: [step])

    draft = ingest.draft_part(_entry("4741"))

    assert draft.source.endswith(".step")
    assert draft.part["holes"]
    assert draft.bodies > 5


def test_a_step_only_product_is_offered_as_importable():
    only_step = CatalogEntry(id="1", name="x", formats=["f3d", "step"])
    nothing = CatalogEntry(id="2", name="y", formats=["f3d", "3mf"])
    assert only_step.importable
    assert not nothing.importable


def test_a_missing_kernel_says_what_to_install(tmp_path, monkeypatch):
    fake = tmp_path / "x.step"
    fake.write_text("ISO-10303-21;")
    monkeypatch.setitem(sys.modules, "cascadio", None)    # import -> ImportError
    with pytest.raises(RuntimeError, match="cascadio"):
        bodies(fake)


# --------------------------------------------------------------------------
# the board's own shape
# --------------------------------------------------------------------------


@synthetic
def test_a_round_board_gets_a_polygon_outline(tmp_path):
    """Row 3."""
    r = 20.0
    stl = _board_stl(tmp_path, Point(r, r).buffer(r, quad_segs=32))

    draft = ingest.draft_part(_entry(), cad=stl)

    outline = draft.part["outline"]
    assert outline["type"] == "poly"
    from hwcase.geom import outline_polygon

    shape = outline_polygon(Part.model_validate(draft.part).outline)
    assert shape.area == pytest.approx(np.pi * r * r, rel=0.02)
    assert shape.bounds == pytest.approx((0.0, 0.0, 2 * r, 2 * r), abs=0.1)
    assert any("polygon" in w for w in draft.warnings)


@synthetic
def test_a_rectangular_board_stays_a_rectangle(tmp_path):
    stl = _board_stl(tmp_path, box(0, 0, 60, 40))

    draft = ingest.draft_part(_entry(), cad=stl)

    assert draft.part["outline"] == {
        "type": "rect", "size": [60.0, 40.0], "origin": "min"}


@synthetic
def test_rounded_corners_do_not_make_a_polygon(tmp_path):
    """A 3 mm corner radius on a 60 x 40 board is noise, not a shape."""
    board = box(3, 3, 57, 37).buffer(3.0, quad_segs=16)
    stl = _board_stl(tmp_path, board)

    assert ingest.draft_part(_entry(), cad=stl).part["outline"]["type"] == "rect"


@synthetic
def test_slots_and_vias_are_not_mounting_holes(tmp_path):
    """Round and screw-sized, repeated and spread: only those two M2.5."""
    board = box(0, 0, 60, 40)
    for x, y, r in ((5, 5, 1.4), (55, 35, 1.4),            # the mounting holes
                    (20, 10, 0.5), (22.54, 10, 0.5),       # vias
                    (30, 20, 0.5)):
        board = board.difference(Point(x, y).buffer(r, quad_segs=16))
    board = board.difference(box(30, 5, 36, 8))            # a slot
    found = bodies(_board_stl(tmp_path, board))[0]

    fp = board_footprint(found)
    groups = hole_groups(fp)

    assert len(fp.holes) >= 5                              # it saw them all
    assert len(groups) == 1
    assert groups[0].radius == pytest.approx(1.4, abs=0.02)
    assert groups[0].screw == "M2.5"
    assert sorted(groups[0].centres) == [(5.0, 5.0), (55.0, 35.0)]


@synthetic
def test_a_footprint_of_a_slab_without_a_mesh_is_none():
    assert board_footprint(Body(0, 1, 0, 1, 0, 1)) is None


# --------------------------------------------------------------------------
# checking holes before trusting them
# --------------------------------------------------------------------------


@vendored
def test_holes_read_in_another_frame_are_dropped_with_a_warning(monkeypatch):
    """Row 4: the STEP text and the mesh disagree about where the board is."""
    stl, step = MODELS["4741"]
    real = measure.step_holes(CAD / "4741" / step)
    shifted = [HoleGroup(g.radius, [(x + 500.0, y) for x, y in g.centres],
                         g.screw) for g in real]
    monkeypatch.setattr(ingest, "step_holes", lambda path: shifted)

    draft = ingest.draft_part(_entry("4741"), cad=CAD / "4741" / stl,
                              step=CAD / "4741" / step)

    assert "holes" not in draft.part
    assert any("mounting holes dropped" in w and "outside the board" in w
               for w in draft.warnings)


def test_a_hole_inside_a_body_through_the_board_is_dropped():
    holes = [{"name": "h1", "at": [20.0, 20.0]}, {"name": "h2", "at": [5.0, 5.0]}]
    outline = box(0, 0, 40, 40)
    straddling = Body(10, 30, 10, 30, -3.0, 12.0)          # passes through z 0-1.6
    above = Body(10, 30, 10, 30, 2.0, 12.0)                # sits on the board

    assert "h1" in ingest._hole_problem(holes, outline, [straddling], 1.6)
    assert ingest._hole_problem(holes, outline, [above], 1.6) == ""
    assert ingest._hole_problem(holes, outline, [], 1.6) == ""


def test_small_bodies_never_condemn_a_hole_set():
    """A resistor over a hole is not a body that blocks a screw."""
    holes = [{"name": "h1", "at": [20.0, 20.0]}]
    tiny = Body(19, 21, 19, 21, -1.0, 2.0)                 # 4 mm2
    assert ingest._hole_problem(holes, box(0, 0, 40, 40), [tiny], 1.6) == ""


def test_a_hole_just_outside_the_outline_is_forgiven_a_quarter_millimetre():
    outline = box(0, 0, 40, 40)
    near = [{"name": "h1", "at": [-0.2, 20.0]}]
    far = [{"name": "h1", "at": [-0.4, 20.0]}]
    assert ingest._hole_problem(near, outline, [], 1.6) == ""
    assert "outside" in ingest._hole_problem(far, outline, [], 1.6)


@synthetic
def test_a_single_solid_model_is_called_what_it_is(tmp_path):
    """A battery or a speaker imports -- as an envelope, and it says so."""
    block = trimesh.creation.box(extents=(60.0, 40.0, 1.6))
    path = tmp_path / "block.stl"
    block.export(str(path))

    draft = ingest.draft_part(_entry(), cad=path)

    assert draft.bodies == 1
    assert any("single solid" in w for w in draft.warnings)
    assert "single solid" in draft.part["notes"]


@synthetic
def test_a_board_with_parts_on_it_is_not_called_a_block(tmp_path):
    board = trimesh.creation.box(extents=(60.0, 40.0, 1.6))
    part = trimesh.creation.box(extents=(10.0, 10.0, 5.0))
    part.apply_translation((0, 0, 3.3))                     # standing on it
    path = tmp_path / "board.stl"
    trimesh.util.concatenate([board, part]).export(str(path))

    draft = ingest.draft_part(_entry(), cad=path)

    assert draft.bodies == 2
    assert not any("single solid" in w for w in draft.warnings)


# --------------------------------------------------------------------------
# units and plausibility
# --------------------------------------------------------------------------


def test_a_board_in_the_wrong_units_is_flagged():
    metres = Body(0, 0.06, 0, 0.06, 0, 0.0016)             # a 60 mm board in m
    msgs = ingest._plausibility(metres)
    assert any("thick" in m for m in msgs)
    assert any("across" in m for m in msgs)


def test_an_ordinary_board_raises_no_plausibility_warning():
    assert ingest._plausibility(Body(0, 60, 0, 40, 0, 1.6)) == []


def test_a_board_that_is_far_too_big_is_flagged():
    assert any("across" in m for m in
               ingest._plausibility(Body(0, 6000, 0, 4000, 0, 1.6)))
