"""A photographic render of the finished machine, for when a preview is not enough.

Not realtime and not meant to be. The editor's WebGL view is for working: it
has to answer "does that fit" in a sixtieth of a second, and it does that by
faking most of the light. This is for the one picture at the end -- the render
that goes in a build log or on a product page -- where taking a minute is fine
and the light being wrong is not.

**What we settled on, and why.** Four options were real candidates:

* **Mitsuba 3** -- `pip install mitsuba`, physically based, unbiased, a pure
  Python scene API, and cp310 Windows wheels that install without a compiler.
  Chosen. It is the only one that drops into this stack without asking anyone
  to install an application or set up a build.
* **Blender / Cycles headless** -- the best-looking result and by far the most
  material control, but `bpy` publishes no wheel for the Python this runs on,
  so it means installing Blender itself and shelling out to it. Supported as
  a fallback for people who already have it; not required.
* **LuxCoreRender** (`pyluxcore`) -- installs cleanly and renders beautifully,
  but its scene API is a string-property format that would need its own
  translation layer for no gain over Mitsuba here.
* **three-gpu-pathtracer** -- path tracing in the browser, which would have
  been the tidiest answer since the editor already has a three.js scene. It
  needs three >= 0.180 and we vendor r169, plus three-mesh-bvh and a build
  step. Revisit if the vendored three is ever upgraded.

Mitsuba is an *optional* dependency. Nothing else in hwcase imports it, and a
missing install produces one clear sentence rather than a traceback -- the
editor and the exporter are the product, and this is the garnish.

The environments are the same CC0 HDRIs the WebGL preview uses, so the
photographic render and the working view are lit by the same light. A final
image that looks nothing like what you were looking at while you designed it
is not much use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import triangulate

from .case import CaseModel, Layer
from .scene import Resolved

__all__ = ["RenderSettings", "render", "build_scene_dict", "available"]

REPO = Path(__file__).resolve().parent.parent.parent
HDRI_DIR = REPO / "web" / "vendor" / "hdri"

INSTALL_HINT = (
    "the photographic renderer needs Mitsuba, which is not installed. "
    "`pip install mitsuba` (about 100 MB) and try again -- everything else in "
    "hwcase works without it."
)


def available() -> bool:
    """Whether a photographic render can be produced at all."""
    try:
        import mitsuba  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class RenderSettings:
    """How to take the picture."""

    width: int = 1280
    height: int = 960
    #: Samples per pixel. 64 is a clean-ish preview, 256 is a finished image;
    #: noise falls with the square root, so quadrupling this halves it.
    samples: int = 128
    max_depth: int = 12
    #: which vendored HDRI lights the scene -- studio, daylight, dusk
    env: str = "studio"
    #: how bright the environment is
    env_scale: float = 1.0
    #: camera elevation and azimuth in degrees, and distance as a multiple of
    #: the model's size. The default is the three-quarter view everybody
    #: photographs a box from, because it shows a face, a side and the top.
    elevation: float = 32.0
    azimuth: float = 38.0
    distance: float = 2.1
    #: 35 mm-equivalent field of view. Long-ish on purpose: a wide lens up
    #: close makes a small object look like architecture.
    fov: float = 28.0
    #: put the machine on a surface instead of floating it in the environment
    ground: bool = True


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

from .geom import polygons as _polys


def _triangulate(poly: Polygon) -> list[tuple]:
    """Triangles covering one polygon, holes and all.

    Delaunay over the polygon's own vertices, then keep the triangles whose
    centroid is actually inside it. That is not a constrained triangulation --
    a deeply concave outline can in principle produce an edge that cuts a
    corner -- but every vertex of the outline is in the input, so the boundary
    is reproduced, and on the shapes a case is made of it lands on the
    polygon's area exactly.

    The alternative was another dependency (mapbox_earcut) for a feature that
    is already optional, which seemed like a poor trade.
    """
    tris = []
    for t in triangulate(poly):
        if poly.contains(t.centroid):
            tris.append(tuple(t.exterior.coords)[:3])
    return tris


def _extrude(polys: Iterable[Polygon], z0: float, z1: float):
    """A closed-enough mesh for one slab: two caps and a wall per ring."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add(v) -> int:
        verts.append(v)
        return len(verts) - 1

    for poly in polys:
        for tri in _triangulate(poly):
            base = [add((x, y, z0)) for x, y in tri]
            faces.append((base[0], base[2], base[1]))       # bottom, wound down
            top = [add((x, y, z1)) for x, y in tri]
            faces.append(tuple(top))

        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                a = add((x0, y0, z0))
                b = add((x1, y1, z0))
                c = add((x1, y1, z1))
                d = add((x0, y0, z1))
                faces.append((a, b, c))
                faces.append((a, c, d))

    return np.array(verts, dtype=np.float32), np.array(faces, dtype=np.uint32)


def _write_ply(path: Path, verts: np.ndarray, faces: np.ndarray) -> Path:
    """A binary PLY, which Mitsuba reads directly.

    Written by hand rather than via a mesh library: the format is twenty lines
    of header and a memory dump, and it saves depending on something else for
    an optional feature.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(verts)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        f"element face {len(faces)}\n"
        "property list uchar uint vertex_indices\n"
        "end_header\n"
    ).encode("ascii")

    counts = np.full((len(faces), 1), 3, dtype=np.uint8)
    face_bytes = np.hstack([
        counts.view(np.uint8),
        faces.astype(np.uint32).view(np.uint8).reshape(len(faces), 12),
    ])
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(verts.astype("<f4").tobytes())
        fh.write(face_bytes.tobytes())
    return path


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

def _srgb(hex_color: Optional[str], fallback: tuple) -> list[float]:
    if not hex_color:
        return list(fallback)
    s = str(hex_color).strip().lstrip("#")
    if len(s) != 6:
        return list(fallback)
    try:
        return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return list(fallback)


#: Same idea as the browser's finishes.js, and deliberately the same names:
#: the material's own name says what it is made of, so a scene lit here and a
#: scene lit in the editor agree about what plywood looks like.
_FINISHES = {
    "smoked": dict(rgb=(0.11, 0.12, 0.14), rough=0.10, metal=0.0, alpha=0.62),
    "plywood": dict(rgb=(0.62, 0.45, 0.26), rough=0.72, metal=0.0, alpha=1.0),
    "mdf": dict(rgb=(0.52, 0.40, 0.27), rough=0.90, metal=0.0, alpha=1.0),
    "cardboard": dict(rgb=(0.50, 0.42, 0.28), rough=0.95, metal=0.0, alpha=1.0),
    "felt": dict(rgb=(0.22, 0.24, 0.27), rough=1.00, metal=0.0, alpha=1.0),
    "acrylic": dict(rgb=(0.80, 0.86, 0.92), rough=0.05, metal=0.0, alpha=0.42),
    "aluminium": dict(rgb=(0.75, 0.77, 0.79), rough=0.28, metal=1.0, alpha=1.0),
    "brass": dict(rgb=(0.72, 0.59, 0.14), rough=0.26, metal=1.0, alpha=1.0),
    "steel": dict(rgb=(0.56, 0.59, 0.62), rough=0.28, metal=1.0, alpha=1.0),
}
#: Qualifiers first: `acrylic-smoked-3mm` is smoked, not plain acrylic.
_PRECEDENCE = ("smoked", "plywood", "mdf", "cardboard", "felt", "acrylic",
               "aluminium", "brass", "steel")


def _finish_for(name: str) -> dict:
    n = (name or "").lower()
    for key in _PRECEDENCE:
        if key in n:
            return _FINISHES[key]
    return dict(rgb=(0.55, 0.58, 0.62), rough=0.55, metal=0.1, alpha=1.0)


def _bsdf(layer: Layer) -> dict:
    f = _finish_for(layer.material.name)
    rgb = _srgb(getattr(layer.material, "color", None), f["rgb"])

    if f["alpha"] < 1.0:
        # Real dielectric rather than a transparency hack: the whole reason to
        # reach for a raytracer on an acrylic-lidded box is that you get the
        # refraction and the edge glow, and alpha blending gives neither.
        return {"type": "roughdielectric", "int_ior": 1.49,
                "alpha": max(f["rough"], 0.001)}

    if f["metal"] > 0.5:
        return {"type": "roughconductor", "alpha": max(f["rough"] ** 2, 0.001),
                "material": "Al"}

    return {
        "type": "roughplastic",
        "diffuse_reflectance": {"type": "rgb", "value": rgb},
        "alpha": max(f["rough"] ** 2, 0.002),
        "int_ior": 1.4,
    }


# ---------------------------------------------------------------------------
# the scene
# ---------------------------------------------------------------------------

def _look_at(target, radius: float, s: RenderSettings):
    el = math.radians(s.elevation)
    az = math.radians(s.azimuth)
    d = radius * max(s.distance, 0.2)
    return (
        target[0] + d * math.cos(el) * math.cos(az),
        target[1] + d * math.cos(el) * math.sin(az),
        target[2] + d * math.sin(el),
    )


def build_scene_dict(res: Resolved, case: CaseModel, s: RenderSettings,
                     workdir: Path) -> dict:
    """A Mitsuba scene, with the meshes written out beside it.

    Split from `render` so the translation can be tested without Mitsuba
    installed -- which matters, because most machines running the tests will
    not have it.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    x0, y0, x1, y1 = case.outer.bounds
    z0, z1 = case.z0, case.z1
    centre = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    radius = max(x1 - x0, y1 - y0, z1 - z0) or 100.0

    scene: dict = {
        "type": "scene",
        "integrator": {"type": "path", "max_depth": int(s.max_depth)},
        "sensor": {
            "type": "perspective",
            "fov": float(s.fov),
            "fov_axis": "smaller",
            "to_world": _transform_look_at(_look_at(centre, radius, s), centre),
            "sampler": {"type": "independent", "sample_count": int(s.samples)},
            "film": {
                "type": "hdrfilm",
                "width": int(s.width), "height": int(s.height),
                "rfilter": {"type": "gaussian"},
                "pixel_format": "rgb",
            },
        },
    }

    hdr = HDRI_DIR / f"{s.env}.hdr"
    if hdr.exists():
        scene["environment"] = {
            "type": "envmap",
            "filename": str(hdr),
            "scale": float(s.env_scale),
        }
    else:
        # Better a plain sky than a black frame: someone who deleted the HDRIs
        # should still get a picture, just a duller one.
        scene["environment"] = {
            "type": "constant",
            "radiance": {"type": "rgb", "value": [0.55, 0.6, 0.68]},
        }

    if s.ground:
        scene["ground"] = {
            "type": "rectangle",
            "to_world": _transform_ground(centre, z0, radius),
            "bsdf": {"type": "roughplastic",
                     "diffuse_reflectance": {"type": "rgb",
                                             "value": [0.18, 0.18, 0.19]},
                     "alpha": 0.25, "int_ior": 1.4},
        }

    for layer in case.layers:
        polys = _polys(layer.geom)
        if not polys:
            continue
        verts, faces = _extrude(polys, layer.z0, layer.z1)
        if not len(faces):
            continue
        ply = _write_ply(workdir / f"layer{layer.index:02d}.ply", verts, faces)
        scene[f"layer{layer.index:02d}"] = {
            "type": "ply", "filename": str(ply), "bsdf": _bsdf(layer),
        }

        # Engraving is a surface mark, so it is a separate, very slightly
        # proud shell in a dark matte finish -- the same thing a laser does to
        # the fibres of a piece of plywood.
        if layer.engrave is not None and not layer.engrave.is_empty:
            everts, efaces = _extrude(_polys(layer.engrave),
                                      layer.z1 - 0.25, layer.z1 + 0.02)
            if len(efaces):
                eply = _write_ply(workdir / f"engrave{layer.index:02d}.ply",
                                  everts, efaces)
                scene[f"engrave{layer.index:02d}"] = {
                    "type": "ply", "filename": str(eply),
                    "bsdf": {"type": "diffuse",
                             "reflectance": {"type": "rgb",
                                             "value": [0.05, 0.05, 0.06]}},
                }

    # The hardware, as blocks. Not pretty, but a picture of an empty box is
    # not a picture of the machine, and the boards are what it is for.
    for i, solid in enumerate(res.bodies()):
        verts, faces = _extrude([solid.poly], solid.z[0], solid.z[1])
        if not len(faces):
            continue
        ply = _write_ply(workdir / f"part{i:03d}.ply", verts, faces)
        scene[f"part{i:03d}"] = {
            "type": "ply", "filename": str(ply),
            "bsdf": {"type": "roughplastic",
                     "diffuse_reflectance": {"type": "rgb",
                                             "value": [0.08, 0.13, 0.10]},
                     "alpha": 0.09, "int_ior": 1.5},
        }

    return scene


def _transform_look_at(origin, target) -> list[list[float]]:
    """A camera-to-world matrix, built here rather than by Mitsuba.

    Mitsuba would happily construct this, but then translating a scene would
    require Mitsuba to be installed -- and the whole point of keeping it
    optional is that the translation can be tested on a machine that does not
    have it. `load_dict` takes a plain 4x4 either way.

    Mitsuba's camera looks down its own +Z with +Y up, so the columns are
    right, up, forward, position.
    """
    origin = np.asarray(origin, dtype=float)
    target = np.asarray(target, dtype=float)

    forward = target - origin
    n = np.linalg.norm(forward)
    forward = forward / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])

    world_up = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(forward, world_up))) > 0.999:
        world_up = np.array([0.0, 1.0, 0.0])       # looking straight down

    right = np.cross(world_up, forward)
    right /= np.linalg.norm(right)
    up = np.cross(forward, right)

    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = right, up, forward, origin
    return [[float(v) for v in row] for row in m]


def _transform_ground(centre, z0: float, radius: float) -> list[list[float]]:
    """Translate then scale, as a plain 4x4 for the same reason."""
    extent = radius * 6.0
    m = np.eye(4)
    m[0, 0] = m[1, 1] = extent
    m[:3, 3] = [centre[0], centre[1], z0 - 0.5]
    return [[float(v) for v in row] for row in m]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(res: Resolved, case: CaseModel, out: Path,
           settings: Optional[RenderSettings] = None,
           workdir: Optional[Path] = None) -> Path:
    """Render the machine to a PNG. Slow on purpose."""
    try:
        import mitsuba as mi
    except ImportError as exc:
        raise RuntimeError(INSTALL_HINT) from exc

    s = settings or RenderSettings()
    out = Path(out)

    # scalar_rgb: one ray at a time on the CPU. The LLVM and CUDA variants are
    # much faster but pull in a JIT and a working GPU driver, and this is a
    # picture somebody takes once.
    mi.set_variant("scalar_rgb")

    work = Path(workdir) if workdir else out.parent / f".{out.stem}-meshes"
    scene_dict = build_scene_dict(res, case, s, work)
    image = mi.render(mi.load_dict(scene_dict))

    out.parent.mkdir(parents=True, exist_ok=True)
    mi.util.write_bitmap(str(out), image)
    return out
