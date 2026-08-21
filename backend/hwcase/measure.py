"""Read geometry out of vendor CAD, as data rather than as printed text.

Vendor STL/STEP files are the closest thing to calipers without owning the
part, and this module is the one place that turns them into numbers. The CLIs
in tools/ are thin wrappers around it, and so is the catalogue importer, so a
part measured by hand and a part imported from Adafruit go through identical
code.

Two sources, because they carry different things:

* **STL** has the envelope and the vertical profile, but no holes -- a mesh of
  a board is a slab and the drilling is gone.
* **STEP** is a B-Rep, so a hole survives as a pair of circles. No CAD kernel
  needed: the circles are in the text.

The interesting work is `feature_stack()`. A part is not one box -- a display
breakout is a PCB, a glass module on top of it and a connector standing proud
of that -- and a case has to know about each, because each one decides whether
a layer can be there. So rather than reporting a single envelope, we walk the
z profile, find the heights where the footprint *changes*, and emit one box per
step. That is the shape a layered case actually cares about.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

__all__ = [
    "Band", "Step", "Measurement", "HoleGroup",
    "load_points", "z_bands", "feature_stack", "measure", "step_holes",
    "name_steps", "to_yaml_block",
]


# ---------------------------------------------------------------------------
# meshes
# ---------------------------------------------------------------------------

_POINT = re.compile(
    r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)", re.I)


def _step_points(path: Path) -> np.ndarray:
    """Vertices straight out of the STEP text.

    Crude on purpose -- it picks up control points of curves as well as real
    vertices -- but for a bounding box and a z profile that is harmless, and it
    means STEP works without a CAD kernel installed.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in _POINT.finditer(text):
        try:
            xyz = [float(v) for v in m.group(2).split(",")]
        except ValueError:
            continue
        if len(xyz) == 3:
            out.append(xyz)
    return np.array(out, dtype=float) if out else np.zeros((0, 3))


def load_points(path: Path) -> np.ndarray:
    """Every vertex in a CAD file, as an (n, 3) array in millimetres.

    Points, not triangles: everything below is bounding boxes and histograms,
    and a point cloud is the common denominator between a mesh and the vertex
    soup we can pull out of a STEP without a kernel.
    """
    path = Path(path)
    if path.suffix.lower() in (".step", ".stp"):
        pts = _step_points(path)
        if pts.size:
            return pts
        try:
            import cadquery as cq
        except ImportError as exc:      # pragma: no cover - optional dependency
            raise RuntimeError(
                f"{path.name}: no CARTESIAN_POINTs found and cadquery is not "
                f"installed; use the .stl next to it") from exc
        shape = cq.importers.importStep(str(path))
        return np.array([v.toTuple() for v in shape.vertices().vals()])

    import trimesh
    mesh = trimesh.load_mesh(str(path))
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
    return np.asarray(mesh.vertices, dtype=float)


# ---------------------------------------------------------------------------
# the vertical profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Band:
    """One horizontal slice: where the material is, not just how wide."""

    z0: float
    z1: float
    x0: float
    x1: float
    y0: float
    y1: float
    count: int

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.depth


def z_bands(points: np.ndarray, band: float = 0.5) -> list[Band]:
    """Slice the cloud into horizontal bands.

    Reports the *position* of the material in each band, not only its extent.
    Extent alone was enough to say "there is a 34.65 mm thing up there" and not
    enough to say where -- which is precisely the question when you are cutting
    a window for it.
    """
    if points.size == 0 or band <= 0:
        return []
    z = points[:, 2]
    lo, hi = float(z.min()), float(z.max())
    if hi - lo < 1e-9:
        return [Band(lo, hi,
                     float(points[:, 0].min()), float(points[:, 0].max()),
                     float(points[:, 1].min()), float(points[:, 1].max()),
                     len(points))]

    out: list[Band] = []
    edge = lo
    while edge < hi - 1e-9:
        top = min(edge + band, hi)
        sel = points[(z >= edge - 1e-9) & (z <= top + 1e-9)]
        if len(sel):
            out.append(Band(edge, top,
                            float(sel[:, 0].min()), float(sel[:, 0].max()),
                            float(sel[:, 1].min()), float(sel[:, 1].max()),
                            len(sel)))
        else:
            out.append(Band(edge, top, 0.0, 0.0, 0.0, 0.0, 0))
        edge = top
    return out


@dataclass
class Step:
    """A run of bands with the same footprint -- one box of the real part."""

    z0: float
    z1: float
    x0: float
    x1: float
    y0: float
    y1: float
    bands: int = 1
    name: str = ""

    @property
    def size(self) -> tuple[float, float, float]:
        return (self.x1 - self.x0, self.y1 - self.y0, self.z1 - self.z0)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    @property
    def footprint(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)


def _absorb_slivers(steps: list[Step], min_height: float) -> list[Step]:
    """Fold sub-`min_height` steps into whichever neighbour they resemble."""
    if len(steps) <= 1:
        return steps
    out: list[Step] = []
    for s in steps:
        if s.z1 - s.z0 >= min_height or not out:
            out.append(s)
            continue
        prev = out[-1]
        prev.z1 = s.z1
        prev.x0, prev.x1 = min(prev.x0, s.x0), max(prev.x1, s.x1)
        prev.y0, prev.y1 = min(prev.y0, s.y0), max(prev.y1, s.y1)
        prev.bands += s.bands
    return out


def feature_stack(points: np.ndarray, band: float = 0.5,
                  tol: float = 1.0, min_height: float = 0.4) -> list[Step]:
    """Group the profile into the boxes a case has to work around.

    Walks up the bands and starts a new step whenever the footprint moves by
    more than `tol` on any edge. Steps shorter than `min_height` are folded
    into their neighbour -- a chamfer or a fillet shows up as a one-band blip
    and is not a feature anyone needs to cut around.
    """
    bands = [b for b in z_bands(points, band) if b.count]
    if not bands:
        return []

    steps: list[Step] = []
    for b in bands:
        cur = steps[-1] if steps else None
        moved = cur is None or max(
            abs(b.x0 - cur.x0), abs(b.x1 - cur.x1),
            abs(b.y0 - cur.y0), abs(b.y1 - cur.y1)) > tol
        if moved:
            steps.append(Step(b.z0, b.z1, b.x0, b.x1, b.y0, b.y1))
            continue
        # same feature: extend it, and let it own the union of what it covers
        cur.z1 = b.z1
        cur.x0, cur.x1 = min(cur.x0, b.x0), max(cur.x1, b.x1)
        cur.y0, cur.y1 = min(cur.y0, b.y0), max(cur.y1, b.y1)
        cur.bands += 1

    return _absorb_slivers(steps, min_height)


# ---------------------------------------------------------------------------
# holes
# ---------------------------------------------------------------------------

_CIRCLE = re.compile(
    r"#(\d+)\s*=\s*CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([-\d.Ee+]+)\s*\)", re.I)
_AXIS = re.compile(
    r"#(\d+)\s*=\s*AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)", re.I)

#: clearance holes you actually find on a breakout, by radius
SCREW_RADII: dict[float, str] = {
    1.0: "M1.6", 1.1: "M2", 1.25: "M2", 1.35: "M2.5",
    1.4: "M2.5", 1.5: "M3", 1.6: "M3", 1.7: "M3", 1.75: "M3",
}


@dataclass
class HoleGroup:
    """Equal circles, well spread -- i.e. a mounting pattern."""

    radius: float
    centres: list[tuple[float, float]]
    screw: Optional[str] = None

    @property
    def diameter(self) -> float:
        return self.radius * 2.0

    @property
    def spread(self) -> float:
        xs = [c[0] for c in self.centres]
        ys = [c[1] for c in self.centres]
        return max(max(xs) - min(xs), max(ys) - min(ys))


def step_holes(path: Path, *, min_count: int = 2, min_spread: float = 10.0,
               screws_only: bool = True) -> list[HoleGroup]:
    """Mounting-hole patterns from a STEP file.

    A hole is a cylindrical face bounded by circles, and STEP stores those as
    text, so this is a parse rather than a geometry operation. A mounting hole
    is then a screw-sized circle that repeats and is spread across the board;
    a pad, a via or a rounded corner fails one of those three.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    points: dict[int, list[float]] = {}
    for m in _POINT.finditer(text):
        try:
            xyz = [float(v) for v in m.group(2).split(",")]
        except ValueError:
            continue
        if len(xyz) == 3:
            points[int(m.group(1))] = xyz
    axes = {int(m.group(1)): int(m.group(2)) for m in _AXIS.finditer(text)}

    by_radius: dict[float, set] = defaultdict(set)
    for m in _CIRCLE.finditer(text):
        origin = axes.get(int(m.group(2)))
        if origin is None or origin not in points:
            continue
        x, y, _z = points[origin]
        by_radius[round(float(m.group(3)), 4)].add((round(x, 3), round(y, 3)))

    groups = []
    for radius, centres in sorted(by_radius.items()):
        if len(centres) < min_count:
            continue
        screw = next((s for r, s in SCREW_RADII.items() if abs(radius - r) < 0.06),
                     None)
        g = HoleGroup(radius, sorted(centres), screw)
        if g.spread < min_spread:
            continue
        if screws_only and screw is None:
            continue
        groups.append(g)
    return sorted(groups, key=lambda g: -g.spread)


# ---------------------------------------------------------------------------
# bodies -- the primitive that actually works
# ---------------------------------------------------------------------------
#
# Slicing a mesh into z bands seemed like the obvious way to find a part's
# features, and it is wrong in two ways that both bit us on the 1.5" OLED.
#
#  * An STL of a flat plate has vertices only on its two faces, so a 1.6 mm
#    PCB reads as two paper-thin slivers with a void between them.
#  * A band's bounding box merges everything at that height. Two STEMMA QT
#    connectors on opposite edges of a board became one 34 mm "strip across the
#    display", and a display was then fitted to that fiction.
#
# A vendor mesh is an assembly: the board is one connected shell, each
# component is another. Splitting on connectivity asks the model what objects
# it contains instead of inferring them, and the answer is exact.

@dataclass
class Body:
    """One connected shell out of the vendor model -- a real object."""

    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float
    vertices: int = 0
    name: str = ""

    @property
    def size(self) -> tuple[float, float, float]:
        return (self.x1 - self.x0, self.y1 - self.y0, self.z1 - self.z0)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    @property
    def footprint(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    def shifted(self, dx: float, dy: float, dz: float) -> "Body":
        return Body(self.x0 - dx, self.x1 - dx, self.y0 - dy, self.y1 - dy,
                    self.z0 - dz, self.z1 - dz, self.vertices, self.name)

    def flipped(self, width: float, pcb_top: float) -> "Body":
        """Turn the part over: mirror z about the board, and x with it.

        Mirroring one axis alone would turn the part into its own mirror image,
        which puts every connector on the wrong side. Mirroring two keeps it
        the same part, seen from the other face.
        """
        return Body(width - self.x1, width - self.x0, self.y0, self.y1,
                    pcb_top - self.z1, pcb_top - self.z0, self.vertices, self.name)


def bodies(path: Path, min_footprint: float = 0.0) -> list[Body]:
    """Every connected shell in a mesh, largest footprint first."""
    import trimesh

    mesh = trimesh.load_mesh(str(path))
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(list(mesh.geometry.values()))

    out = []
    for shell in mesh.split(only_watertight=False):
        (bx0, by0, bz0), (bx1, by1, bz1) = shell.bounds
        b = Body(float(bx0), float(bx1), float(by0), float(by1),
                 float(bz0), float(bz1), len(shell.vertices))
        if b.footprint >= min_footprint:
            out.append(b)
    return sorted(out, key=lambda b: -b.footprint)
