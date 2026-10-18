"""Display models: a part's vendor mesh, shown as itself rather than as boxes.

Everything the engine computes -- collisions, pockets, cutouts -- runs on a
part's boxes, and stays that way. This module is only what a person sees:
the vendor's own model, reduced to a triangle budget, coloured, and put in the
part's frame so it lands exactly where the boxes are.

A part names its model with `model:` -- the file under `vendor/cad`, and the
4x4 matrix that takes that file's millimetres into the part's frame. The
importer writes both, because it already computes them: the shift that puts
the board's corner at the origin and its bottom face at z = 0, and the
turn-over when the model was drawn components-up. Nothing here guesses.

Colour: the board is the board's colour (most current Adafruit boards are
black), everything standing on it a neutral metal. STEP and 3MF colours are a
later refinement.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Optional

import numpy as np

#: vendor CAD lives here; a part's `model.file` is relative to it
CAD_ROOT = Path(__file__).resolve().parent.parent.parent / "vendor" / "cad"

#: reduced models are cached here, keyed by everything that shapes them
CACHE = CAD_ROOT / ".models"

#: triangles per part model. Enough that a pin header still reads as pins;
#: few enough that a dozen boards stay smooth on an old laptop.
TRIANGLE_BUDGET = 40_000

#: the board, unless the part says otherwise
PCB_COLOR = "#1b1b1f"
#: everything standing on it
METAL_COLOR = "#a9adb3"


def matrix_for(pcb, flip: bool) -> list[float]:
    """Row-major 4x4 from the model file's millimetres to the part's frame.

    The same two moves `ingest.draft_part` makes with the boxes: put the
    board's corner at the origin and its bottom at z = 0, or -- turned over --
    mirror x and z together about the board, so the part stays itself and is
    seen from the other face.
    """
    if flip:
        return [-1.0, 0.0, 0.0, pcb.size[0] + pcb.x0,
                0.0, 1.0, 0.0, -pcb.y0,
                0.0, 0.0, -1.0, pcb.z1,
                0.0, 0.0, 0.0, 1.0]
    return [1.0, 0.0, 0.0, -pcb.x0,
            0.0, 1.0, 0.0, -pcb.y0,
            0.0, 0.0, 1.0, -pcb.z0,
            0.0, 0.0, 0.0, 1.0]


def model_for(path: Path, flip: Optional[bool] = None) -> dict:
    """The `model:` block for a vendor file, worked out as the importer
    would: which body is the board, and which way up."""
    from . import ingest
    from .measure import bodies

    found = bodies(Path(path))
    pcb = found[ingest._pick_pcb(found)]
    if flip is None:
        flip, _why = ingest._should_flip(found, pcb)
    rel = Path(path).resolve().relative_to(CAD_ROOT.resolve())
    return {"file": rel.as_posix(),
            "matrix": [round(v, 4) for v in matrix_for(pcb, flip)],
            "color": PCB_COLOR}


def _rgba(colour: str) -> list[float]:
    return [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)] + [1.0]


def pieces_for(part, budget: Optional[int] = TRIANGLE_BUDGET
               ) -> dict[str, object]:
    """{colour: mesh} -- the board, and everything standing on it -- each
    reduced in proportion to its share of the budget, in the part's frame.
    `budget=None` keeps every triangle, which is what a measurement wants."""
    import trimesh

    from . import ingest
    from .measure import bodies

    if part.model is None:
        raise ValueError(f"{part.id} has no display model")
    found = bodies(CAD_ROOT / part.model.file)
    if not found:
        raise ValueError(f"{part.model.file}: no geometry")
    board = ingest._pick_pcb(found)
    total = sum(len(b.mesh.faces) for b in found) or 1
    # every body keeps at least a dozen triangles; set that aside first so
    # the shares cannot add up past the budget
    share = max(0, (budget or 0) - 12 * len(found))
    groups: dict[str, list] = {}
    for i, b in enumerate(found):
        m = b.mesh.copy()
        target = max(12, int(share * len(m.faces) / total))
        if budget is not None and len(m.faces) > target:
            try:
                m = m.simplify_quadric_decimation(face_count=target)
            except Exception:                    # pragma: no cover - degenerate
                pass
        colour = (part.model.color or PCB_COLOR) if i == board else METAL_COLOR
        groups.setdefault(colour, []).append(m)
    matrix = np.array(part.model.matrix, float).reshape(4, 4)
    out = {}
    for colour, meshes in groups.items():
        mesh = trimesh.util.concatenate(meshes)
        mesh.apply_transform(matrix)
        out[colour] = mesh
    # decimation lands near its target, not on it: trim the largest group
    # until the whole model is inside the budget
    for _ in range(3 if budget is not None else 0):
        over = sum(len(m.faces) for m in out.values()) - budget
        if over <= 0:
            break
        big = max(out, key=lambda c: len(out[c].faces))
        try:
            out[big] = out[big].simplify_quadric_decimation(
                face_count=max(12, len(out[big].faces) - over - 16))
        except Exception:                        # pragma: no cover
            break
    return out


def mesh_for(part, budget: Optional[int] = TRIANGLE_BUDGET):
    """The part's model in its own frame, as one uncoloured mesh -- for
    bounds, alignment and the photograph's geometry."""
    import trimesh

    return trimesh.util.concatenate(list(pieces_for(part, budget).values()))


def _key(part, budget: int) -> str:
    src = CAD_ROOT / part.model.file
    stamp = f"{src.stat().st_mtime_ns}:{src.stat().st_size}" if src.exists() else ""
    raw = f"{part.model.file}|{part.model.matrix}|{part.model.color}|{budget}|{stamp}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def glb_for(part, budget: int = TRIANGLE_BUDGET) -> Path:
    """A binary glTF of the part's model, built once and cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{part.id}-{_key(part, budget)}.glb"
    if not out.exists():
        import trimesh
        from trimesh.visual.material import PBRMaterial
        from trimesh.visual.texture import TextureVisuals

        # one glTF material per colour: no per-vertex colours, which trimesh
        # can only compute with scipy -- forty megabytes for two colours
        scene = trimesh.Scene()
        for colour, mesh in pieces_for(part, budget).items():
            metal = colour == METAL_COLOR
            mesh.visual = TextureVisuals(material=PBRMaterial(
                baseColorFactor=_rgba(colour),
                metallicFactor=0.8 if metal else 0.0,
                roughnessFactor=0.35 if metal else 0.7))
            scene.add_geometry(mesh, geom_name=f"{part.id}-{colour[1:]}")
        data = scene.export(file_type="glb")
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(out)
    return out


def alignment(part, tol: float = 0.5) -> list[str]:
    """Why the model does not sit on the part's boxes, or [] when it does.

    The board outline must match the model's footprint, and the model must
    not stand taller or deeper than the part reserves -- the part may reserve
    more (pin tails, a pad that is not in the vendor's file), never less.
    """
    from .geom import outline_polygon

    m = mesh_for(part, budget=None)            # measured whole, not reduced
    (mx0, my0, mz0), (mx1, my1, mz1) = m.bounds
    ox0, oy0, ox1, oy1 = outline_polygon(part.outline).bounds
    lo, hi = part.z_extent()
    out = []
    for name, got, want in (("x min", mx0, ox0), ("y min", my0, oy0),
                            ("x max", mx1, ox1), ("y max", my1, oy1)):
        if abs(got - want) > tol:
            out.append(f"model {name} {got:.2f} against the outline's {want:.2f}")
    if mz0 < lo - tol:
        out.append(f"model reaches {lo - mz0:.2f} mm below the part ({mz0:.2f})")
    if mz1 > hi + tol:
        out.append(f"model stands {mz1 - hi:.2f} mm above the part ({mz1:.2f})")
    return out
