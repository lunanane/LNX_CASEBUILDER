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
from . import __version__
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

app = FastAPI(title="hwcase", version=__version__)
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


# --------------------------------------------------------------------------
# the vendor catalogue
# --------------------------------------------------------------------------
#
# The palette lists parts somebody has measured and vouched for. The catalogue
# is the other five and a half thousand things Adafruit sell, searched on
# demand -- listing them all would bury the dozen that matter, and a hosted
# instance picking up new products the week they ship is the whole point.

@app.get("/api/catalog/search")
def catalog_search(q: str, limit: int = 25):
    """Products matching `q`, best first.

    Never fails because someone else's site is down: a stale catalogue still
    answers most questions, and the response says how old it is.
    """
    from . import catalog as cat

    try:
        c = cat.shared()
    except Exception as exc:                  # pragma: no cover - defensive
        raise HTTPException(503, f"catalogue unavailable: {exc}")

    known = {p.sku: p.id for p in library() if p.sku}
    hits = c.search(q, limit=max(1, min(limit, 100)), known=known)
    return {"results": [e.as_dict() for e in hits], "catalog": c.status()}


@app.get("/api/catalog/status")
def catalog_status(refresh: bool = False):
    from . import catalog as cat

    return cat.shared(refresh=refresh).status()


@app.post("/api/catalog/import/{product_id}")
def catalog_import(product_id: str, download: bool = True):
    """Measure a product's vendor model and add it to the library.

    The result is a draft: real envelope, real mounting holes, guessed
    orientation, unnamed volumes and *no connectors at all*. Those caveats ride
    along in the response and in the part's own notes rather than being
    smoothed over, because a board that silently asks for no cable room is a
    case you find out about with a soldering iron.
    """
    from . import catalog as cat
    from . import ingest

    if not re.fullmatch(r"[0-9]{1,7}", product_id):
        raise HTTPException(400, "a product id is digits")

    entry = cat.shared().get(product_id)
    if entry is None:
        raise HTTPException(404, f"no product {product_id} in the catalogue")
    if not entry.cad:
        raise HTTPException(
            422, f"{entry.name} has no vendor CAD, so there is nothing to "
                 f"measure -- it would have to be entered by hand")

    if download and not ingest.local_cad(product_id):
        try:
            ingest.download_cad(entry)
        except Exception as exc:
            raise HTTPException(502, f"could not fetch vendor CAD: {exc}")

    try:
        draft = ingest.draft_part(entry)
    except Exception as exc:
        raise HTTPException(422, f"could not measure {entry.name}: {exc}")

    try:
        part = ingest.save_draft(draft)
    except Exception as exc:
        raise HTTPException(500, f"could not save the part: {exc}")

    library(reload=True)
    return {
        "part": _part_json(part),
        "warnings": draft.warnings,
        "flipped": draft.flipped,
        "bodies": draft.bodies,
        "source": Path(draft.source).name,
    }


@app.delete("/api/catalog/import/{part_id}")
def catalog_forget(part_id: str):
    """Drop an imported part again -- a bad draft should be one click to undo."""
    from . import ingest

    if not ingest.forget_draft(part_id):
        raise HTTPException(404, f"{part_id} is not an imported part")
    library(reload=True)
    return {"ok": True, "removed": part_id}


# --------------------------------------------------------------------------
# the photographic render
# --------------------------------------------------------------------------

@app.get("/api/render/status")
def render_status():
    """Whether a final-image render is possible on this machine."""
    from . import raytrace

    return {"available": raytrace.available(), "hint": raytrace.INSTALL_HINT,
            "environments": sorted(p.stem for p in raytrace.HDRI_DIR.glob("*.hdr"))}


@app.post("/api/render")
def render_image(scene: Scene = Body(...), samples: int = 96,
                 width: int = 1280, height: int = 960,
                 env: str = "studio", elevation: float = 32.0,
                 azimuth: float = 38.0):
    """Trace a finished picture of the machine. Minutes, not milliseconds.

    Synchronous on purpose. This is a thing somebody asks for once, watches,
    and saves -- wrapping it in a job queue would be more moving parts than
    the feature is worth, and a request that takes two minutes is a much
    clearer signal than a job id that has to be polled.
    """
    from . import raytrace

    if not raytrace.available():
        raise HTTPException(503, raytrace.INSTALL_HINT)

    lib = library()
    res = resolve(scene, lib)
    model = case_mod.build(res, scene.case)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{scene.name or 'scene'}-render.png"
    settings = raytrace.RenderSettings(
        width=max(64, min(width, 4096)), height=max(64, min(height, 4096)),
        samples=max(1, min(samples, 4096)), env=env,
        elevation=elevation, azimuth=azimuth)
    try:
        raytrace.render(res, model, out, settings)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"render failed: {exc}")
    return FileResponse(out, media_type="image/png", filename=out.name)


@app.get("/api/health")
def health():
    return {"ok": True, "parts": len(library()), "version": app.version}


# --------------------------------------------------------------------------
# the editor itself
# --------------------------------------------------------------------------

class _NoCacheStatic(StaticFiles):
    """Serve the editor, but make the browser check before reusing it.

    Without a Cache-Control header a browser is free to guess how long a file
    stays fresh, and for a plain .js with a last-modified date the guess can be
    hours. The server reloads on edit, the page does not, and you end up
    debugging a fix that is sitting on disk but is not the code running -- the
    symptom being a button that does nothing.

    `no-cache` is not `no-store`: the file is still cached, the browser just has
    to revalidate. The ETag makes that a 304 and a few bytes.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


if WEB_DIR.exists():
    app.mount("/", _NoCacheStatic(directory=str(WEB_DIR), html=True), name="web")
