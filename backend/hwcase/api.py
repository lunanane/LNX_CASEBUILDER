"""HTTP API over the same engine the CLI uses.

The browser never computes geometry: it posts a scene, gets back resolved
solids and issues, and draws them. That keeps one source of truth for what a
part is and what "this layout is wrong" means.

    uvicorn hwcase.api:app --reload
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from . import scenefile
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import case as case_mod
from . import export
from .geom import outline_polygon
from .library import DEFAULT_PARTS_DIR, PartLibrary, load_scene
from .scene import check, resolve
from .schema import Scene

ROOT = Path(__file__).resolve().parent.parent          # backend/
REPO = ROOT.parent
SCENES_DIR = ROOT / "scenes"
WEB_DIR = REPO / "web"
OUT_DIR = REPO / "out"

app = FastAPI(title="hwcase", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_lib: PartLibrary | None = None


def library(reload: bool = False) -> PartLibrary:
    global _lib
    if _lib is None or reload:
        _lib = PartLibrary.load(DEFAULT_PARTS_DIR)
    return _lib


def _part_json(p) -> dict:
    d = p.model_dump(mode="json")
    lo, hi = p.z_extent()
    poly = outline_polygon(p.outline)
    x0, y0, x1, y1 = poly.bounds
    d["computed"] = {
        "z_extent": [lo, hi],
        "height": hi - lo,
        "bounds": [x0, y0, x1, y1],
        "outline": [list(c) for c in poly.exterior.coords],
        "confidence": p.min_confidence().value,
    }
    return d


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

@app.get("/api/parts")
def get_parts(reload: bool = False):
    lib = library(reload)
    return {"parts": [_part_json(p) for p in lib]}


@app.get("/api/parts/{part_id}")
def get_part(part_id: str):
    try:
        return _part_json(library()[part_id])
    except KeyError:
        raise HTTPException(404, f"unknown part {part_id}")


# --------------------------------------------------------------------------
# scenes on disk
# --------------------------------------------------------------------------

#: A scene name becomes a filename, so it is checked rather than trusted.
#: Without this, "../../something" would write wherever it liked.
SCENE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

STARTER = """# A new scene.
#
# Add boards from the palette on the left. Heights are worked out for you:
# anything with a screen, a knob, a button or an upward-facing socket goes to
# the faceplate, and anything else rests on the inside floor.
#
# A panel is the surface you touch. Derive it from the part that fixes the
# height -- usually a screen -- rather than typing a number:
#
#   panels:
#     - name: main
#       from_ref: <placement>.<volume>

name: {name}
placements: []
case:
  style: layered
  materials:
    - {{name: plywood-3mm, thickness: 3.0, kerf: 0.15, sheet: [600.0, 400.0], color: "#c8a165"}}
  wall: 8.0
  floor_gap: 4.0
  ceiling_gap: 1.0
  part_clearance: 0.6
  cable_clearance: 5.0
  corner_radius: 8.0
"""


def _scene_path(name: str, must_exist: bool = False,
                must_not_exist: bool = False) -> Path:
    if not SCENE_NAME.match(name or ""):
        raise HTTPException(
            400, f"{name!r} is not a usable scene name -- letters, digits, "
                 f"spaces, dot, dash and underscore only")
    path = (SCENES_DIR / f"{name}.yaml").resolve()
    if path.parent != SCENES_DIR.resolve():
        raise HTTPException(400, "scene names cannot contain a path")
    if must_exist and not path.exists():
        raise HTTPException(404, f"no scene {name}")
    if must_not_exist and path.exists():
        raise HTTPException(409, f"{name} already exists")
    return path


@app.get("/api/scenes")
def list_scenes():
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    return {"scenes": sorted(p.stem for p in SCENES_DIR.glob("*.yaml"))}


@app.get("/api/scenes/{name}")
def get_scene(name: str):
    return load_scene(_scene_path(name, must_exist=True)).model_dump(mode="json")


@app.post("/api/scenes")
def create_scene(payload: dict = Body(...)):
    """Start a new scene, optionally as a copy of an existing one.

    Copying takes the file itself rather than a re-serialised model, so the
    reasoning written into its comments comes along with the geometry.
    """
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    name = payload.get("name", "")
    path = _scene_path(name, must_not_exist=True)
    source = payload.get("copy_from")
    if source:
        src = _scene_path(source, must_exist=True)
        path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text(STARTER.format(name=name), encoding="utf-8")
    return {"created": name, "copied_from": source}


@app.post("/api/scenes/{name}/rename")
def rename_scene(name: str, payload: dict = Body(...)):
    src = _scene_path(name, must_exist=True)
    dst = _scene_path(payload.get("to", ""), must_not_exist=True)
    scene = load_scene(src)
    scene.name = dst.stem
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    scenefile.save(scene, dst)          # keeps the comments, fixes the `name`
    src.unlink()
    bak = src.with_suffix(".yaml.bak")
    if bak.exists():
        bak.unlink()
    return {"renamed": name, "to": dst.stem}


@app.put("/api/scenes/{name}")
def put_scene(name: str, scene: Scene = Body(...)):
    """Write a scene back to disk, keeping its comments.

    The scene files carry real reasoning in their comments, so the new values
    are merged into the existing document rather than dumped over it. See
    hwcase.scenefile. A `.bak` is still written, but only when the merge could
    not reuse the previous file.
    """
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    path = _scene_path(name)
    scene.name = name
    merged = scenefile.save(scene, path)
    return {"saved": str(path), "placements": len(scene.placements),
            "comments_preserved": merged}


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

@app.post("/api/resolve")
def post_resolve(scene: Scene = Body(...)):
    lib = library()
    try:
        res = resolve(scene, lib)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    issues = check(res, lib)
    payload = export.scene_to_json(res, lib, issues)
    x0, y0, z0, x1, y1, z1 = res.bounds()
    payload["extent"] = {"min": [x0, y0, z0], "max": [x1, y1, z1]}
    return JSONResponse(payload)


@app.post("/api/build")
def post_build(scene: Scene = Body(...)):
    lib = library()
    try:
        res = resolve(scene, lib)
        model = case_mod.build(res)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(export.case_to_json(model))


@app.post("/api/export/svg", response_class=PlainTextResponse)
def post_svg(scene: Scene = Body(...)):
    lib = library()
    res = resolve(scene, lib)
    return PlainTextResponse(export.to_svg(case_mod.build(res)),
                             media_type="image/svg+xml")


@app.post("/api/export/dxf")
def post_dxf(scene: Scene = Body(...)):
    lib = library()
    res = resolve(scene, lib)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = export.to_dxf(case_mod.build(res), OUT_DIR / f"{scene.name}-layers.dxf")
    return FileResponse(path, filename=path.name,
                        media_type="application/dxf")


@app.post("/api/case/freeze")
def post_freeze(scene: Scene = Body(...)):
    """Turn the auto-derived case outline into a fixed one.

    While the outline is derived from the bounding box of the hardware, moving
    a board outward moves the wall out with it -- so you can never bring a
    connector flush with the outside. Freezing pins the wall where it is; after
    that, moving a board moves it *relative to the case*.
    """
    lib = library()
    try:
        res = resolve(scene, lib)
        shape = case_mod.outer_shape(res, scene.case)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    x0, y0, x1, y1 = shape.bounds
    return {"outline": {
        "type": "rect",
        "size": [round(x1 - x0, 3), round(y1 - y0, 3)],
        "corner_radius": scene.case.corner_radius,
        "origin": "custom",
        # `custom` places the rect's min corner at -origin_offset, so this pins
        # the frozen outline exactly where the derived one was
        "origin_offset": [round(-x0, 3), round(-y0, 3)],
    }}


@app.get("/api/health")
def health():
    return {"ok": True, "parts": len(library()), "version": app.version}


# --------------------------------------------------------------------------
# the editor itself
# --------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
