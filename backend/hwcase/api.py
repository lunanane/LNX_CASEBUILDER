"""HTTP API over the same engine the CLI uses.

The browser never computes geometry: it posts a scene, gets back resolved
solids and issues, and draws them. That keeps one source of truth for what a
part is and what "this layout is wrong" means.

    uvicorn hwcase.api:app --reload
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import threading
from pathlib import Path

from . import scenefile
from . import __version__
from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response)
from fastapi.staticfiles import StaticFiles

from . import case as case_mod
from . import rack as rack_mod
from . import export
from .geom import outline_polygon
from .library import DEFAULT_PARTS_DIR, PartLibrary, load_scene
from .scene import check, resolve
from .schema import Part, Scene

ROOT = Path(__file__).resolve().parent.parent          # backend/
REPO = ROOT.parent
SCENES_DIR = ROOT / "scenes"
WEB_DIR = REPO / "web"
OUT_DIR = REPO / "out"

#: Hosted mode: the server keeps nothing.
#:
#: Locally, hwcase is a single-user tool and the scene files on disk *are* the
#: project -- editable in a text editor, diffable, committed next to the
#: firmware. On a public instance that same directory would be one shared
#: drawer that every visitor can read, rename and overwrite, so hosted mode
#: takes it away entirely: no scene storage, no library writes, no output
#: files. The browser holds the project and the server is pure arithmetic --
#: post a scene, get geometry back, keep nothing.
#:
#: Everything that makes the tool worth using survives the change, because the
#: engine endpoints were already stateless: they take the whole scene in the
#: request body and read no file to answer.
HOSTED = os.environ.get("HWCASE_HOSTED", "").strip().lower() in {"1", "true", "yes", "on"}

#: A render is minutes of every core the box has. One at a time on a shared
#: instance, or the second visitor's click makes the first one's slower.
RENDER_SLOTS = int(os.environ.get("HWCASE_RENDER_SLOTS", "1" if HOSTED else "4"))
_render_slots = threading.BoundedSemaphore(max(1, RENDER_SLOTS))

#: Ceilings on what one request may ask the tracer for. Locally these are
#: generous because the only person they can cost anything is the person who
#: typed them; hosted, 4096 samples at 4096 squared is several core-hours that
#: anybody can order with one POST.
MAX_SAMPLES = int(os.environ.get("HWCASE_MAX_SAMPLES", "512" if HOSTED else "4096"))
MAX_PIXELS = int(os.environ.get("HWCASE_MAX_PIXELS", "1920" if HOSTED else "4096"))

#: How much scene text the two routes that accept a file will look at.
#:
#: A real scene is a few tens of kilobytes -- the largest one in this repo is
#: under forty. A megabyte is room for something twenty times bigger than
#: anything anyone has drawn, and a ceiling on how much work a stranger can
#: hand the parser in one request.
MAX_SCENE_BYTES = int(os.environ.get("HWCASE_MAX_SCENE_BYTES", str(1 << 20)))

def _readable_scene_text(text: object) -> str:
    """Check a posted scene file is worth handing to a YAML parser at all.

    Two things, and the second one matters more than it looks.

    `safe_load` is safe in the sense that it builds no arbitrary objects, but
    it still expands aliases -- and a few hundred bytes of `&d [*c, *c, *c,
    *c]` nested twenty deep expands to trillions of list elements and takes
    the worker down with it. Counting anchors is not a defence, because the
    depth that hurts is reached in twenty lines; the expansion has to be
    refused outright.

    Which costs nothing, because a scene file has never contained one. Nothing
    writes them -- hwcase.scenefile does not emit anchors -- and nothing needs
    them, so a file that has them is either something exotic or something
    aimed at this endpoint, and both get the same answer.

    Scanning is what makes this cheap and honest: `yaml.scan` walks the token
    stream without composing or constructing anything, so an alias is spotted
    before it is ever expanded. It also means a `&` inside a comment or a
    string is not mistaken for an anchor.
    """
    import yaml as _yaml

    if not isinstance(text, str):
        raise HTTPException(400, "expected the scene file as text")
    if len(text.encode("utf-8")) > MAX_SCENE_BYTES:
        raise HTTPException(
            413, f"that file is larger than {MAX_SCENE_BYTES // 1024} KB, which "
                 f"is far larger than any scene -- if it really is one, raise "
                 f"HWCASE_MAX_SCENE_BYTES")
    try:
        for token in _yaml.scan(text):
            if isinstance(token, (_yaml.AnchorToken, _yaml.AliasToken)):
                raise HTTPException(
                    422, "this file uses YAML anchors, which a scene file never "
                         "does and which can be built into something that "
                         "exhausts the parser -- expand them and try again")
    except _yaml.YAMLError as exc:
        raise HTTPException(422, f"not valid YAML: {exc}")
    return text

app = FastAPI(title="hwcase", version=__version__)

# The editor is served by this same app, so same-origin is the normal case and
# no CORS header is needed for it. The wildcard stays the local default only
# because someone running the frontend off a separate dev server relies on it;
# a hosted instance opts in by naming its origins.
_origins = os.environ.get("HWCASE_ORIGINS", "" if HOSTED else "*").strip()
if _origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
        allow_methods=["*"], allow_headers=["*"],
    )

_lib: PartLibrary | None = None


def library(reload: bool = False) -> PartLibrary:
    global _lib
    if _lib is None or reload:
        _lib = PartLibrary.load(DEFAULT_PARTS_DIR)
    return _lib


def _lib_for(scene: Scene) -> PartLibrary:
    """The shared library, with the scene's own parts laid over the top.

    Same-id parts in the scene win. That is the point: a board you drafted
    from a vendor model belongs to the project, and if the installation later
    ships a properly measured part under the same id, your file keeps working
    off the copy it was drawn against until you delete it.

    Cheap enough to do per request -- it is a dict of a few dozen models --
    and doing it per request is what keeps the process free of user state.
    """
    if not scene.parts:
        return library()
    merged = PartLibrary()
    own = {p.id for p in scene.parts}
    for part in library():
        if part.id not in own:
            merged.add(part)
    for part in scene.parts:
        merged.add(part)
    return merged


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


@app.post("/api/parts/describe")
def describe_parts(payload: dict = Body(...)):
    """Enrich a scene's own parts the way /api/parts enriches the library's.

    A scene that carries its own boards (Scene.parts) still has to draw them
    in the palette, and the palette needs the derived numbers -- z extent,
    footprint, confidence -- that only the engine can work out. Without this
    the browser would have to reimplement outline geometry to show a part it
    already has.
    """
    raw = payload.get("parts")
    if not isinstance(raw, list):
        raise HTTPException(400, "expected {parts: [...]}")
    try:
        parts = [Part.model_validate(entry) for entry in raw]
    except Exception as exc:
        raise HTTPException(422, f"not a usable part: {exc}")
    return {"parts": [_part_json(p) for p in parts]}


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


RACK_STARTER = """# A new Eurorack frame.
#
# This project is built from the format, not from boards: set a width in HP
# and a stack of rows, and the frame follows. Nothing here is placed by hand.
#
# The case is two side panels, one folded aluminium U, and two rails per row
# bolted between the panels with an M5 at each end. Rows are an ordered list
# bottom to top, so a mixed stack works:
#
#   rows:
#     - {{format: 1U-intellijel}}
#     - {{format: 3U}}
#     - {{format: 1U-intellijel}}
#
# `inserts_per_row: 1` cuts a 4 HP slot into each side panel per 3U row, so a
# module slides in from the side -- 8 HP of extra capacity per row, without
# widening the rails.

name: {name}
kind: rack
rack:
  hp: 68
  rows:
    - {{format: 3U}}
    - {{format: 3U}}
  inserts_per_row: 0
  insert_hp: 4.0
  panel:
    top: 10.5
    bottom: 10.5
    front: 0.0
    back: 0.0
"""

#: Which starter text a new project of each kind begins from.
STARTERS = {"parts": STARTER, "rack": RACK_STARTER}


def _starter_text(name: str, kind: str) -> str:
    tpl = STARTERS.get(kind or "parts")
    if tpl is None:
        raise HTTPException(400, f"unknown project kind {kind!r}")
    return tpl.format(name=name)


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


#: The routes that read and write `backend/scenes`. Hosted mode never includes
#: this router, so those URLs are simply not there -- a 404 says "this server
#: has no scene drawer" more honestly than a 403 on a route that exists.
disk = APIRouter()


@disk.get("/api/scenes")
def list_scenes():
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    return {"scenes": sorted(p.stem for p in SCENES_DIR.glob("*.yaml"))}


@disk.get("/api/scenes/{name}")
def get_scene(name: str):
    return load_scene(_scene_path(name, must_exist=True)).model_dump(mode="json")


@disk.post("/api/scenes")
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
        path.write_text(_starter_text(name, payload.get("kind", "parts")),
                        encoding="utf-8")
    return {"created": name, "copied_from": source}


@disk.post("/api/scenes/{name}/rename")
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


@disk.put("/api/scenes/{name}")
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
# scene files, without a filesystem
# --------------------------------------------------------------------------
#
# These two are what a hosted editor uses instead of the routes above, and
# they are just as useful locally: text in, text out, nothing stored. The
# browser owns the file -- it opened it from the user's disk and it will write
# it back there -- and asks the server only for the two things the browser
# cannot do for itself, which are validating a scene and serialising one
# without throwing its comments away.

@app.post("/api/scene/parse")
def scene_parse(payload: dict = Body(...)):
    """YAML text in, a validated scene out.

    The error matters as much as the success: a file that has been hand-edited
    into an invalid state should say which field and why, in the editor, not
    fail silently into a half-loaded scene.
    """
    import yaml as _yaml

    text = _readable_scene_text(payload.get("yaml"))
    try:
        raw = _yaml.safe_load(text)
    except Exception as exc:
        raise HTTPException(422, f"not valid YAML: {exc}")
    if not isinstance(raw, dict):
        raise HTTPException(422, "a scene file is a mapping, not a bare value")
    try:
        scene = Scene.model_validate(raw)
    except Exception as exc:
        raise HTTPException(422, f"not a usable scene: {exc}")
    return scene.model_dump(mode="json")


@app.post("/api/scene/serialize", response_class=PlainTextResponse)
def scene_serialize(payload: dict = Body(...)):
    """A scene as the YAML text to save, keeping the previous file's comments.

    `previous` is the text the browser opened. Passing it back is what makes a
    save a *merge*: the reasoning written into a scene file -- why the Pi is
    rotated, what the panel is derived from -- survives a round trip through
    the editor. Without it the comments are gone, which is exactly the bug
    hwcase.scenefile exists to prevent.
    """
    try:
        scene = Scene.model_validate(payload.get("scene"))
    except Exception as exc:
        raise HTTPException(422, f"not a usable scene: {exc}")
    previous = payload.get("previous")
    if previous is not None:
        previous = _readable_scene_text(previous)
    text = scenefile.dumps(scene, previous)
    return PlainTextResponse(text, media_type="application/x-yaml", headers={
        "Content-Disposition": f'attachment; filename="{scene.name or "scene"}.yaml"',
    })


@app.get("/api/scene/starter", response_class=PlainTextResponse)
def scene_starter(name: str = "untitled", kind: str = "parts"):
    """The blank scene, as text.

    A browser that stores its own scenes still needs one to start from, and
    the starter is a commented file rather than an empty mapping -- the
    comments are half of what it teaches. Serving it from here keeps one copy
    of that text instead of a second one drifting in the frontend.
    """
    if not SCENE_NAME.match(name or ""):
        raise HTTPException(400, f"{name!r} is not a usable scene name")
    return PlainTextResponse(_starter_text(name, kind),
                             media_type="application/x-yaml")


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

@app.post("/api/resolve")
def post_resolve(scene: Scene = Body(...)):
    lib = _lib_for(scene)
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
    # A rack scene has no placements to resolve -- its shape comes from the
    # format, not from the hardware -- so it takes the other builder entirely.
    if scene.kind == "rack":
        try:
            return JSONResponse(export.rack_to_json(rack_mod.build_spec(scene.rack)))
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))
    lib = _lib_for(scene)
    try:
        res = resolve(scene, lib)
        model = case_mod.build(res)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(export.case_to_json(model))


@app.post("/api/export/rack/panels.svg", response_class=PlainTextResponse)
def post_rack_panels_svg(scene: Scene = Body(...)):
    """The frame's side panels, as one cuttable SVG.

    Frames only. The rails and the U-channel are ordered, not cut, so the
    panels are the whole of what a laser gets from this pipeline -- the
    U-channel's numbers come back on the build as `order` instead.
    """
    if scene.kind != "rack":
        raise HTTPException(400, "not a frame scene")
    try:
        model = rack_mod.build_spec(scene.rack)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", scene.name or "frame").strip("-") or "frame"
    return PlainTextResponse(
        export.rack_panels_svg(model), media_type="image/svg+xml",
        headers={"Content-Disposition":
                 f'attachment; filename="{name}-side-panels.svg"'})


@app.post("/api/export/svg", response_class=PlainTextResponse)
def post_svg(scene: Scene = Body(...)):
    lib = _lib_for(scene)
    res = resolve(scene, lib)
    return PlainTextResponse(export.to_svg(case_mod.build(res)),
                             media_type="image/svg+xml")


@app.post("/api/export/dxf")
def post_dxf(scene: Scene = Body(...)):
    """The cut as one DXF, handed back as bytes.

    It used to be written to `out/{name}-layers.dxf` and served from there,
    which is fine for one person and wrong for two: the filename is the scene
    name, so two visitors both working on a `case` would take turns
    overwriting each other's download.
    """
    lib = _lib_for(scene)
    res = resolve(scene, lib)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", scene.name or "case").strip("-") or "case"
    return Response(export.to_dxf_text(case_mod.build(res)),
                    media_type="application/dxf", headers={
                        "Content-Disposition": f'attachment; filename="{name}-layers.dxf"',
                    })


@app.post("/api/export/sheets/{fmt}")
def export_sheets(fmt: str, scene: Scene = Body(...), as_json: bool = False):
    """The cut, split into bed-sized files.

    `as_json` returns {sheets: [{name, content}...], manifest} so a browser
    with a directory picker can write real files into a chosen folder;
    without it the same files come back as one zip, which is what a browser
    without a picker (Firefox) can actually save.
    """
    if fmt not in ("svg", "dxf"):
        raise HTTPException(400, "format is svg or dxf")
    lib = _lib_for(scene)
    res = resolve(scene, lib)
    model = case_mod.build(res, scene.case)
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", scene.name or "case").strip("-") or "case"

    try:
        files = export.sheet_files(model, fmt, base)
    except ValueError as exc:
        # a layer bigger than the bed is the user's problem to hear about,
        # not a 500
        raise HTTPException(422, str(exc))

    if as_json:
        return {
            "sheets": [{"name": n, "content": c} for n, c in files],
            "count": len(files) - 1,           # the manifest is not a sheet
        }
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files:
            z.writestr(name, content)
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{base}-sheets-{fmt}.zip"',
    })


@app.post("/api/case/freeze")
def post_freeze(scene: Scene = Body(...)):
    """Turn the auto-derived case outline into a fixed one.

    While the outline is derived from the bounding box of the hardware, moving
    a board outward moves the wall out with it -- so you can never bring a
    connector flush with the outside. Freezing pins the wall where it is; after
    that, moving a board moves it *relative to the case*.
    """
    lib = _lib_for(scene)
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

    if HOSTED:
        # Nothing is filed. The draft goes back to the browser, which keeps it
        # in the scene itself (Scene.parts) and saves it into the user's own
        # `.yaml`. One visitor importing a board must not change what every
        # other visitor sees in their palette.
        try:
            part = Part.model_validate(draft.part)
        except Exception as exc:
            raise HTTPException(422, f"the draft did not validate: {exc}")
    else:
        try:
            part = ingest.save_draft(draft)
        except Exception as exc:
            raise HTTPException(500, f"could not save the part: {exc}")
        library(reload=True)

    return {
        "stored": not HOSTED,
        # `part` carries the derived numbers the palette draws with; the
        # definition is the part as it would be written to a file, and is what
        # a hosted client puts into its scene.
        "definition": part.model_dump(mode="json"),
        "part": _part_json(part),
        "warnings": draft.warnings,
        "flipped": draft.flipped,
        "bodies": draft.bodies,
        "source": Path(draft.source).name,
    }


@disk.delete("/api/catalog/import/{part_id}")
def catalog_forget(part_id: str):
    """Drop an imported part again -- a bad draft should be one click to undo.

    Only meaningful where importing filed something. Hosted, the draft only
    ever lived in the scene, so undoing it is the browser deleting it from
    `Scene.parts` -- no server round trip, and no route here.
    """
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

    Shared, it is also the only route that can cost the machine real money, so
    it is capped rather than queued: one at a time, and the second person to
    ask is told so (429, Retry-After) instead of joining a line that makes
    both pictures slower. A queue would be the right answer if renders were
    the point of the tool. They are the last five minutes of it.
    """
    from . import raytrace

    if not raytrace.available():
        raise HTTPException(503, raytrace.INSTALL_HINT)

    if not _render_slots.acquire(blocking=False):
        raise HTTPException(
            429, "the renderer is busy -- this machine traces one picture at a "
                 "time, because two at once only makes both of them slower. "
                 "Try again when the current one finishes.",
            headers={"Retry-After": "30"})
    try:
        lib = _lib_for(scene)
        res = resolve(scene, lib)
        model = case_mod.build(res, scene.case)

        settings = raytrace.RenderSettings(
            width=max(64, min(width, MAX_PIXELS)),
            height=max(64, min(height, MAX_PIXELS)),
            samples=max(1, min(samples, MAX_SAMPLES)), env=env,
            elevation=elevation, azimuth=azimuth)

        name = re.sub(r"[^A-Za-z0-9._-]+", "-", scene.name or "scene").strip("-") or "scene"

        def trace(out: Path) -> None:
            try:
                raytrace.render(res, model, out, settings)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc))
            except Exception as exc:
                raise HTTPException(500, f"render failed: {exc}")

        if HOSTED:
            # The picture is never written anywhere a second visitor could
            # find it. The tracer needs a path to work against, so it gets a
            # private directory that goes away with the request -- along with
            # the mesh cache it writes beside the image.
            with tempfile.TemporaryDirectory(prefix="hwcase-render-") as tmp:
                out = Path(tmp) / f"{name}-render.png"
                trace(out)
                return Response(out.read_bytes(), media_type="image/png", headers={
                    "Content-Disposition": f'attachment; filename="{out.name}"',
                })

        # Locally the file stays in `out/`, which is where you go looking for
        # it afterwards.
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{name}-render.png"
        trace(out)
        return FileResponse(out, media_type="image/png", filename=out.name)
    finally:
        _render_slots.release()


@app.get("/api/config")
def config():
    """What this instance can do, so the editor stops guessing.

    The frontend has to behave differently against a hosted server -- scenes
    live in the browser, imported parts ride along in the file, the renderer
    may refuse -- and asking once beats probing each route for a 404.
    """
    from . import raytrace

    return {
        "hosted": HOSTED,
        "scene_storage": not HOSTED,
        "part_import_stored": not HOSTED,
        "render": {"available": raytrace.available(),
                   "max_samples": MAX_SAMPLES, "max_pixels": MAX_PIXELS,
                   "slots": RENDER_SLOTS},
        "version": app.version,
    }


@app.get("/api/health")
def health():
    return {"ok": True, "parts": len(library()), "version": app.version,
            "hosted": HOSTED}


# --------------------------------------------------------------------------
# the editor itself
# --------------------------------------------------------------------------

class _NoCacheStatic(StaticFiles):
    """Serve the editor, but never let a browser reuse it without asking.

    Without a Cache-Control header a browser is free to guess how long a file
    stays fresh, and for a plain .js with a last-modified date that guess runs
    to hours. The server reloads on edit, the page does not, and you end up
    debugging a fix that is on disk but is not the code running. The symptom is
    a button that does nothing, which is indistinguishable from a bug in the
    button, and that cost several rounds of chasing the wrong thing.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


def asset_stamp() -> str:
    """A token that changes whenever any editor source does."""
    newest = 0.0
    for pattern in ("*.js", "*.css", "*.html"):
        for f in WEB_DIR.glob(pattern):
            newest = max(newest, f.stat().st_mtime)
    return str(int(newest))


#: Local modules the page pulls in. Listed by scanning rather than hardcoded,
#: so a new one cannot be forgotten and quietly become the stale file.
def _local_modules() -> list[str]:
    return sorted(f.name for f in WEB_DIR.glob("*.js"))


def index_html() -> str:
    """`index.html`, with every local script pinned to the current build.

    Cache-Control only governs responses fetched *after* it was added. A copy
    already sitting in a browser cache under an earlier heuristic keeps its
    freshness and is never re-requested, so no header can dislodge it. Changing
    the URL can: a versioned query is a cache miss, always.

    The `<script src>` is rewritten, and the import map gains an entry per
    local module -- without that, a fresh app.js would go straight back to a
    stale menu.js, which is a worse kind of confusing.
    """
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    stamp = asset_stamp()

    html = html.replace('src="./app.js"', f'src="./app.js?v={stamp}"')

    pins = "".join(
        ',\n    "./%s": "./%s?v=%s"' % (name, name, stamp)
        for name in _local_modules())
    html = html.replace(
        '"three/addons/environments/RoomEnvironment.js": "./vendor/RoomEnvironment.js"',
        '"three/addons/environments/RoomEnvironment.js": "./vendor/RoomEnvironment.js"'
        + pins, 1)
    return html


# Last, because the routes that touch disk are spread down the file, as far as
# the catalogue -- and before the static mount below, which answers "/" and
# everything under it and would otherwise swallow them.
if not HOSTED:
    app.include_router(disk)


if WEB_DIR.exists():
    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    def editor():
        return HTMLResponse(index_html(), headers={
            "Cache-Control": "no-store, must-revalidate",
            "Pragma": "no-cache",
        })

    app.mount("/", _NoCacheStatic(directory=str(WEB_DIR), html=True), name="web")
