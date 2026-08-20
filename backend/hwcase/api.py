"""HTTP API over the same engine the CLI uses.

The browser never computes geometry: it posts a scene, gets back resolved
solids and issues, and draws them. That keeps one source of truth for what a
part is and what "this layout is wrong" means.

    uvicorn hwcase.api:app --reload
"""

from __future__ import annotations

import io
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

@app.get("/api/scenes")
def list_scenes():
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    return {"scenes": sorted(p.stem for p in SCENES_DIR.glob("*.yaml"))}


@app.get("/api/scenes/{name}")
def get_scene(name: str):
    path = SCENES_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(404, f"no scene {name}")
    return load_scene(path).model_dump(mode="json")


@app.put("/api/scenes/{name}")
def put_scene(name: str, scene: Scene = Body(...)):
    """Write a scene back to disk, keeping its comments.

    The scene files carry real reasoning in their comments, so the new values
    are merged into the existing document rather than dumped over it. See
    hwcase.scenefile. A `.bak` is still written, but only when the merge could
    not reuse the previous file.
    """
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    path = SCENES_DIR / f"{name}.yaml"
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
