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
#: exports of the same case a second apart already differ. Those fields carry
#: no geometry; they are blanked before hashing and nothing else is touched.
_GUID = re.compile(r"\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")
_EZDXF_STAMP = re.compile(r"^\d+\.\d+\.\d+ @ \d{4}-\d\d-\d\dT[0-9:.+]+$")


def _stable(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        # header variable name, group code, value: $TDCREATE, $TDUPDATE, ...
        if line.strip().startswith("$TD") and i + 2 < len(lines):
            lines[i + 2] = "<time>"
        elif _EZDXF_STAMP.match(line.strip()):
            lines[i] = "<ezdxf stamp>"
    return _GUID.sub("{GUID}", "\n".join(lines))


def _layered_scenes() -> list[Path]:
    return [p for p in sorted(SCENES.glob("*.yaml"))
            if load_scene(p).kind == "parts"]


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


if __name__ == "__main__":
    if "--write" not in sys.argv:
        sys.exit("usage: python tests/test_flat.py --write")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    record = {p.name: sheet_digests(p) for p in _layered_scenes()}
    FIXTURE.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
    print(f"wrote {FIXTURE} ({len(record)} scenes)")
