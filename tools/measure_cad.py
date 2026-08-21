#!/usr/bin/env python3
"""Read exact dimensions out of vendor CAD.

A thin front end onto `hwcase.measure`, which is where the actual work lives --
so a part measured at this prompt and a part imported through the editor's
search box go through identical code, and neither can quietly drift from the
other.

    python tools/measure_cad.py "vendor/cad/adafruit/4741/*.stl"
    python tools/measure_cad.py --bands vendor/cad/adafruit/3954/*.stl
    python tools/measure_cad.py --yaml vendor/cad/adafruit/5752/*.stl

By default it lists the model's **bodies**: the connected shells it is made of.
That is almost always the question you actually have -- "what objects are on
this board and where" -- and it is the one thing z-band slicing gets wrong,
because a band's bounding box merges everything at that height. Two connectors
on opposite edges of the 1.5" OLED came back as one 34 mm strip across the
middle of the display, and a display was then modelled to fit it.

`--bands` keeps the old vertical profile, which is still the right tool for
"how tall is this thing at each height".
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from hwcase.measure import (bodies, feature_stack, load_points,  # noqa: E402
                            measure, to_yaml_block, z_bands)


def report_bodies(path: Path, limit: int, min_footprint: float) -> None:
    found = bodies(path, min_footprint=min_footprint)
    print(f"\n=== {path.name}")
    if not found:
        print("  no geometry")
        return

    pcb = found[0]
    print(f"  {len(found)} connected bodies; largest is "
          f"{pcb.size[0]:.2f} x {pcb.size[1]:.2f} x {pcb.size[2]:.2f} mm")
    print(f"  {'footprint':>10} {'size (mm)':>26}  {'z (mm)':>16}   position")
    for b in found[:limit]:
        w, d, h = b.size
        print(f"  {b.footprint:10.1f} {w:8.2f} x{d:7.2f} x{h:6.2f}  "
              f"{b.z0:7.2f}..{b.z1:7.2f}   "
              f"x {b.x0:7.2f}..{b.x1:7.2f}  y {b.y0:7.2f}..{b.y1:7.2f}")
    if len(found) > limit:
        rest = found[limit:]
        tallest = max(r.size[2] for r in rest)
        print(f"  ... and {len(rest)} smaller bodies, tallest {tallest:.2f} mm "
              f"(--limit to see more)")


def report_bands(path: Path, band: float) -> None:
    pts = load_points(path)
    print(f"\n=== {path.name}  (z bands of {band} mm)")
    print(f"  {'z0':>8} {'z1':>8} {'x ext':>8} {'y ext':>8}  {'verts':>7}")
    for b in z_bands(pts, band):
        bar = "#" * min(40, int(max(b.width, b.depth) / 2))
        print(f"  {b.z0:8.2f} {b.z1:8.2f} {b.width:8.2f} {b.depth:8.2f}  "
              f"{b.count:7d}  {bar}")

    print("\n  steps (bands merged where the footprint stops moving):")
    for s in feature_stack(pts, band=band):
        w, d, h = s.size
        print(f"  z {s.z0:7.2f}..{s.z1:7.2f}  {w:7.2f} x {d:7.2f} x {h:6.2f}")


def report_yaml(path: Path) -> None:
    m = measure(path)
    print(f"\n=== {path.name}")
    print(f"    # {len(m.bodies)} bodies, board {m.pcb_thickness} mm thick")
    print(f"    outline:")
    print(f"      type: rect")
    print(f"      size: [{m.size[0]:.2f}, {m.size[1]:.2f}]")
    print(f"      origin: min")
    if m.pcb_thickness:
        print(f"    pcb_thickness: {m.pcb_thickness}")
    print(to_yaml_block(m))
    for note in m.notes:
        print(f"    # {note}")
    for g in m.holes:
        print(f"    # holes: {len(g.centres)} x {g.diameter:.2f} mm "
              f"({g.screw or 'unknown thread'})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--bands", action="store_true",
                    help="the old vertical profile instead of the body list")
    ap.add_argument("--yaml", action="store_true",
                    help="print a part YAML fragment ready to paste")
    ap.add_argument("--band", type=float, default=0.5, help="z band height, mm")
    ap.add_argument("--limit", type=int, default=12,
                    help="how many bodies to list")
    ap.add_argument("--min-footprint", type=float, default=0.0,
                    help="mm2; hide bodies smaller than this")
    args = ap.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.files:
        paths += [Path(p) for p in sorted(glob.glob(pattern))]
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    for path in paths:
        try:
            if args.yaml:
                report_yaml(path)
            elif args.bands:
                report_bands(path, args.band)
            else:
                report_bodies(path, args.limit, args.min_footprint)
        except Exception as exc:
            print(f"\n=== {path.name}\n  {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
