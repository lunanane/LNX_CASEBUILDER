#!/usr/bin/env python3
"""Read exact dimensions out of vendor CAD meshes.

Vendor STL/STEP files are the closest thing we have to calipers without owning
the part. This reads a mesh, normalises it into our part frame (origin at the
outline's min corner, z = 0 at the PCB bottom face), and prints:

  * the overall bounding box
  * a z profile: how wide the part is at each height, which is exactly the
    question a layered case asks
  * a first-cut `volumes:` block you can paste into a part YAML

    python tools/measure_cad.py vendor/cad/adafruit/3954/*.stl
    python tools/measure_cad.py --band 0.5 vendor/cad/adafruit/5752/*.stl

STEP is read only if `cadquery`/`build123d` (OCP) is installed; STL always
works via trimesh, and for a bounding-box-and-profile job STL is plenty.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import trimesh


def load(path: Path):
    if path.suffix.lower() in (".step", ".stp"):
        try:
            import cadquery as cq  # noqa: F401
        except ImportError:
            raise SystemExit(
                f"{path.name}: reading STEP needs `pip install cadquery`; "
                f"use the .stl next to it instead")
        import cadquery as cq
        shape = cq.importers.importStep(str(path))
        verts = np.array([v.toTuple() for v in shape.vertices().vals()])
        return trimesh.PointCloud(verts)
    mesh = trimesh.load_mesh(str(path))
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
    return mesh


def z_profile(mesh, band: float) -> list[tuple[float, float, float, float, int]]:
    """Per z band: (z0, z1, x extent, y extent, vertex count)."""
    v = np.asarray(mesh.vertices)
    z0, z1 = v[:, 2].min(), v[:, 2].max()
    out = []
    z = z0
    while z < z1 - 1e-9:
        top = min(z + band, z1)
        sel = v[(v[:, 2] >= z - 1e-9) & (v[:, 2] <= top + 1e-9)]
        if len(sel):
            out.append((z, top,
                        float(sel[:, 0].max() - sel[:, 0].min()),
                        float(sel[:, 1].max() - sel[:, 1].min()),
                        len(sel)))
        else:
            out.append((z, top, 0.0, 0.0, 0))
        z = top
    return out


def report(path: Path, band: float, pcb_thickness: float | None) -> None:
    mesh = load(path)
    v = np.asarray(mesh.vertices)
    lo, hi = v.min(axis=0), v.max(axis=0)
    size = hi - lo

    print(f"\n=== {path.name}")
    print(f"  raw bbox   x {lo[0]:8.3f}..{hi[0]:8.3f}   "
          f"y {lo[1]:8.3f}..{hi[1]:8.3f}   z {lo[2]:8.3f}..{hi[2]:8.3f}")
    print(f"  size       {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} mm")
    print(f"  vertices   {len(v)}")

    prof = z_profile(mesh, band)
    print(f"\n  z profile (band {band} mm, z relative to the model's own zero):")
    print(f"  {'z0':>8} {'z1':>8} {'x ext':>8} {'y ext':>8}  {'verts':>7}")
    for z0, z1, xe, ye, n in prof:
        bar = "#" * min(40, int(max(xe, ye) / 2))
        print(f"  {z0:8.2f} {z1:8.2f} {xe:8.2f} {ye:8.2f}  {n:7d}  {bar}")

    # widest band = the board itself; use it as the outline
    widest = max(prof, key=lambda p: p[2] * p[3])
    print(f"\n  widest band  z {widest[0]:.2f}..{widest[1]:.2f}  "
          f"{widest[2]:.2f} x {widest[3]:.2f} mm  <- likely the PCB outline")

    print("\n  suggested YAML (origin: min, z=0 at the model's zero):")
    print(f"    outline:")
    print(f"      type: rect")
    print(f"      size: [{size[0]:.2f}, {size[1]:.2f}]")
    print(f"      origin: min")
    if pcb_thickness:
        print(f"    pcb_thickness: {pcb_thickness}")
    print(f"    volumes:")
    print(f"      - name: envelope")
    print(f"        kind: body")
    print(f"        at: [{size[0] / 2:.2f}, {size[1] / 2:.2f}]")
    print(f"        size: [{size[0]:.2f}, {size[1]:.2f}]")
    print(f"        z: [{0.0:.2f}, {size[2]:.2f}]")
    print(f"        src: {{confidence: datasheet, note: \"from {path.name}\"}}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--band", type=float, default=1.0, help="z band height, mm")
    ap.add_argument("--pcb", type=float, default=None, help="PCB thickness, mm")
    args = ap.parse_args(argv)
    for f in args.files:
        report(Path(f), args.band, args.pcb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
