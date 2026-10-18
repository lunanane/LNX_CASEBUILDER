"""Flat parts: every cut piece from either pipeline, as one thing a packer packs.

The first test here is the guard the rest of the work hangs off. The sheet
writers used to take a layered case and nothing else; they now take flat
parts, and the layered pipeline has to come out of that change producing the
same files to the byte. So the files every layered scene in `backend/scenes`
exported *before* the change are recorded in `fixtures/sheets/layered.json`
-- one SHA-256 per sheet file and per cut list, by name -- and compared here.

Regenerate the record only when an export change is meant to change the
files, and say so in the commit:

    python tests/test_flat.py --write
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from hwcase import case as case_mod                       # noqa: E402
from hwcase import export                                 # noqa: E402
from hwcase.library import PartLibrary, load_scene        # noqa: E402
from hwcase.scene import resolve                          # noqa: E402

SCENES = BACKEND / "scenes"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sheets" / "layered.json"

# ---------------------------------------------------------------------------
# byte-identical sheets for every layered scene
# ---------------------------------------------------------------------------

#: ezdxf stamps every new document with the time and two fresh GUIDs, so two
#: exports of the same case a second apart already differ. And it writes the
#: CLASSES section in set order, which follows Python's per-process string
#: hash seed, so two runs of the same code shuffle that section. None of it
#: carries geometry: the stamps are blanked and the CLASS records sorted
#: before hashing, and nothing else is touched.
_GUID = re.compile(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")
_EZDXF_STAMP = re.compile(r"^\d+\.\d+\.\d+ @ \d{4}-\d\d-\d\dT[0-9:.+]+$")
_CLASSES = re.compile(r"(  2\nCLASSES\n)(.*?)(  0\nENDSEC\n)", re.S)


def _sorted_classes(m: re.Match) -> str:
    records = m.group(2).split("  0\nCLASS\n")
    head, body = records[0], sorted(records[1:])
    return m.group(1) + head + "".join("  0\nCLASS\n" + r for r in body) \
        + m.group(3)


def _stable(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        # header variable name, group code, value: $TDCREATE, $TDUPDATE, ...
        if line.strip().startswith("$TD") and i + 2 < len(lines):
            lines[i + 2] = "<time>"
        elif _EZDXF_STAMP.match(line.strip()):
            lines[i] = "<ezdxf stamp>"
    text = _GUID.sub("{GUID}", "\n".join(lines))
    return _CLASSES.sub(_sorted_classes, text, count=1)


def _layered_scenes() -> list[Path]:
    """Scenes built as a layer stack -- not frames, and not the box cases and
    module panels that arrived later (M8-M10), which have their own tests."""
    out = []
    for p in sorted(SCENES.glob("*.yaml")):
        scene = load_scene(p)
        if (scene.kind == "parts" and scene.case.style == "layered"
                and scene.case.eurorack_panel is None):
            out.append(p)
    return out


def _library_for(scene) -> PartLibrary:
    """The shared library with the scene's own parts over it, as the API does."""
    shared = PartLibrary.load()
    if not scene.parts:
        return shared
    merged = PartLibrary()
    own = {p.id for p in scene.parts}
    for part in shared:
        if part.id not in own:
            merged.add(part)
    for part in scene.parts:
        merged.add(part)
    return merged


def sheet_digests(path: Path) -> dict[str, dict[str, str]]:
    """{fmt: {file name: sha256}} for one scene's sheet export."""
    scene = load_scene(path)
    model = case_mod.build(resolve(scene, _library_for(scene)), scene.case)
    out: dict[str, dict[str, str]] = {}
    for fmt in ("svg", "dxf"):
        out[fmt] = {
            name: hashlib.sha256(_stable(content).encode("utf-8")).hexdigest()
            for name, content in export.sheet_files(model, fmt, scene.name)}
    return out


def _recorded() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", _layered_scenes(), ids=lambda p: p.stem)
def test_every_layered_scene_exports_byte_identical_sheets(path):
    recorded = _recorded()
    assert path.name in recorded, (
        f"{path.name} is a layered scene with no recorded sheets -- "
        f"run `python tests/test_flat.py --write` and commit the fixture")
    assert sheet_digests(path) == recorded[path.name]


def test_the_record_covers_every_layered_scene_and_nothing_else():
    assert sorted(_recorded()) == [p.name for p in _layered_scenes()]


# ---------------------------------------------------------------------------
# the datum edge
# ---------------------------------------------------------------------------

from hwcase import flat, rack                             # noqa: E402
from hwcase.eurorack import ROW_3U                        # noqa: E402
from hwcase.schema import CaseSpec, Material              # noqa: E402
from shapely.geometry import box as shbox                 # noqa: E402


def test_an_edge_has_the_part_on_its_left():
    e = flat.Edge((0.0, 0.0), (100.0, 0.0), "bottom")
    assert e.inward == pytest.approx((0.0, 1.0))
    assert e.outward == pytest.approx((0.0, -1.0))
    # along the edge from `a`, then into the part
    assert e.measure((12.5, 40.0)) == pytest.approx((12.5, 40.0))


def test_an_edge_measures_the_same_whichever_way_it_lies():
    """A left edge walked top to bottom keeps the part on its left too."""
    e = flat.Edge((0.0, 50.0), (0.0, 0.0), "left")
    assert e.inward == pytest.approx((1.0, 0.0))
    assert e.measure((7.0, 45.0)) == pytest.approx((5.0, 7.0))


def test_a_zero_length_edge_is_refused():
    with pytest.raises(ValueError, match="zero length"):
        flat.Edge((1.0, 1.0), (1.0, 1.0), "dot").direction


# ---------------------------------------------------------------------------
# both pipelines produce flat parts
# ---------------------------------------------------------------------------

def _frame(rows=(ROW_3U, ROW_3U), hp=68.0):
    return rack.build(list(rows), hp)


def test_a_layer_keeps_its_index_role_and_names():
    from hwcase.case import Layer

    mat = Material(name="plywood-3mm", thickness=3.0, kerf=0.15)
    layer = Layer(3, 9.0, 12.0, "floor", mat, shbox(0, 0, 80, 60),
                  notes=["base"], engrave=shbox(10, 10, 20, 20))
    part = flat.from_layer(layer)
    assert (part.index, part.role, part.name) == (3, "floor", "layer-3")
    assert part.outline is layer.geom and part.engrave is layer.engrave
    assert part.engraved_face_down, "the floor is installed marks down"
    assert part.labels.cad_layer == "L03_floor"
    assert part.labels.called == "layer 3 (floor)"
    assert part.datum == flat.Edge((0.0, 0.0), (80.0, 0.0), "bottom")


def test_only_the_floor_is_engraved_face_down():
    from hwcase.case import Layer

    mat = Material()
    for role, down in (("floor", True), ("body", False), ("lid", False)):
        part = flat.from_layer(Layer(0, 0, 3, role, mat, shbox(0, 0, 9, 9)))
        assert part.engraved_face_down is down


def test_a_frame_gives_exactly_its_two_side_panels():
    model = _frame()
    parts = flat.from_rack(model)
    assert [p.name for p in parts] == ["side-left", "side-right"]
    assert [p.index for p in parts] == [0, 1]
    for p in parts:
        assert p.role == "side_panel"
        assert p.material.thickness == rack.SIDE_PANEL_T
        assert p.material.name == "acrylic-5mm"
        assert not p.engraved_face_down


def test_a_rail_is_not_a_flat_part():
    rail = _frame().rails[0]
    with pytest.raises(ValueError, match="not cut from a plate"):
        flat.from_member(rail, 0)


def test_side_panel_holes_are_measured_from_its_bottom_edge():
    """The panel's own frame starts at its front-bottom corner, so the
    datum reading of each M5 is its position in the model, unchanged."""
    model = _frame()
    panel = next(m for m in model.members if m.kind == "side_panel")
    part = flat.from_rack(model)[0]
    assert part.datum.a == (0.0, 0.0)
    assert part.datum.length == pytest.approx(panel.section.bounds[2])
    got = part.datum_positions()
    assert len(got) == len(model.rails), "one M5 per rail end on each panel"
    for (note, along, into), hole in zip(got, panel.holes):
        assert note == hole.note
        assert (along, into) == pytest.approx(hole.at, abs=1e-9)


def test_every_side_panel_hole_is_a_hole_in_its_outline():
    for part in flat.from_rack(_frame()):
        rings = list(part.outline.interiors)
        assert len(rings) == len(part.holes)
        for h in part.holes:
            assert not part.outline.contains(
                shbox(h.at[0] - 0.1, h.at[1] - 0.1, h.at[0] + 0.1, h.at[1] + 0.1))


# ---------------------------------------------------------------------------
# the packer takes flat parts
# ---------------------------------------------------------------------------

def test_frame_plates_pack_onto_the_stock():
    from hwcase.export import pack_sheets

    parts = flat.from_rack(_frame())
    sheets = pack_sheets(parts, stock=CaseSpec())
    placed = [pl.part.name for s in sheets for pl in s.placements]
    assert sorted(placed) == ["side-left", "side-right"]
    for s in sheets:
        for pl in s.placements:
            x0, y0, x1, y1 = pl.geom.bounds
            assert x0 >= 5.0 - 1e-6 and x1 <= s.width - 5.0 + 1e-6
            assert y0 >= 5.0 - 1e-6 and y1 <= s.height - 5.0 + 1e-6


def test_flat_parts_without_a_stock_are_refused():
    from hwcase.export import pack_sheets

    with pytest.raises(ValueError, match="stock"):
        pack_sheets(flat.from_rack(_frame()))


def test_anything_but_a_flat_part_is_refused():
    from hwcase.export import pack_sheets

    with pytest.raises(TypeError, match="flat parts"):
        pack_sheets([_frame().members[0]], stock=CaseSpec())


def test_a_plate_too_big_for_the_stock_is_named_in_the_error():
    from hwcase.export import pack_sheets

    with pytest.raises(ValueError) as err:
        pack_sheets(flat.from_rack(_frame()),
                    stock=CaseSpec(plates=[(100.0, 100.0)]))
    assert "side-left (side_panel)" in str(err.value)
    assert "fits none of the plates" in str(err.value)


def test_a_case_and_its_flat_parts_pack_the_same():
    """The thin path for layered call sites is the flat-part path."""
    from hwcase.case import CaseModel, Layer
    from hwcase.export import pack_sheets

    spec = CaseSpec()
    mat = Material(kerf=0.0)
    layers = [Layer(i, 0, 3, "body", mat, shbox(0, 0, w, h))
              for i, (w, h) in enumerate([(160, 90), (60, 200), (120, 120)])]
    case = CaseModel(spec=spec, outer=shbox(0, 0, 1, 1), layers=layers,
                     z0=0.0, z1=3.0)
    a = [(p.part.index, p.x, p.y, p.rotated)
         for s in pack_sheets(case) for p in s.placements]
    b = [(p.part.index, p.x, p.y, p.rotated)
         for s in pack_sheets(flat.from_case(case), stock=spec)
         for p in s.placements]
    assert a == b


if __name__ == "__main__":
    if "--write" not in sys.argv:
        sys.exit("usage: python tests/test_flat.py --write")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    record = {p.name: sheet_digests(p) for p in _layered_scenes()}
    FIXTURE.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
    print(f"wrote {FIXTURE} ({len(record)} scenes)")
