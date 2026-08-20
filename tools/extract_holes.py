#!/usr/bin/env python3
"""Pull mounting-hole positions out of a vendor STEP file.

The STL exports have no holes in them -- a mesh of a board is a slab, and the
drilled features are gone. STEP keeps them, because it is a B-Rep: a hole is a
cylindrical face bounded by two circles, and STEP stores those circles as text.

    #4711 = CIRCLE ( 'NONE', #4712, 1.2500000 ) ;
    #4712 = AXIS2_PLACEMENT_3D ( 'NONE', #4713, #4714, #4715 ) ;
    #4713 = CARTESIAN_POINT ( 'NONE', ( 3.0, 3.0, 0.0 ) ) ;

So: find every CIRCLE, follow its placement to a point, group by radius, and
report the groups that look like mounting holes -- a handful of equal circles,
spread out, at the same height. No CAD kernel needed.

    python tools/extract_holes.py "vendor/cad/adafruit/3954/*.step"
    python tools/extract_holes.py --all-radii vendor/cad/adafruit/5752/*.step
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from collections import defaultdict
from pathlib import Path

CIRCLE = re.compile(
    r"#(\d+)\s*=\s*CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([-\d.Ee+]+)\s*\)", re.I)
AXIS = re.compile(
    r"#(\d+)\s*=\s*AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)", re.I)
POINT = re.compile(
    r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)", re.I)

#: typical screw clearance holes on a breakout, as radii
SCREW_RADII = {
    1.0: "M1.6", 1.1: "M2 tight", 1.25: "M2", 1.35: "M2.5 tight",
    1.4: "M2.5", 1.5: "M2.5/M3", 1.6: "M3 tight", 1.7: "M3", 1.75: "M3",
}


def parse(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = {int(m.group(1)): [float(v) for v in m.group(2).split(",")]
              for m in POINT.finditer(text)}
    axes = {int(m.group(1)): int(m.group(2)) for m in AXIS.finditer(text)}

    circles = []
    for m in CIRCLE.finditer(text):
        axis_id, radius = int(m.group(2)), float(m.group(3))
        origin = axes.get(axis_id)
        if origin is None or origin not in points:
            continue
        xyz = points[origin]
        if len(xyz) < 3:
            continue
        circles.append((round(radius, 4), tuple(round(v, 4) for v in xyz)))
    return circles


def unique(seq):
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def report(path: Path, all_radii: bool, min_count: int, min_spread: float) -> None:
    circles = parse(path)
    if not circles:
        print(f"\n=== {path.name}\n  no circles found -- is this really a STEP file?")
        return

    by_radius = defaultdict(list)
    for radius, xyz in circles:
        by_radius[radius].append(xyz)

    print(f"\n=== {path.name}")
    print(f"  {len(circles)} circles, {len(by_radius)} distinct radii")

    candidates = []
    for radius, pts in sorted(by_radius.items()):
        centres = unique((round(p[0], 3), round(p[1], 3)) for p in pts)
        if len(centres) < min_count:
            continue
        xs = [c[0] for c in centres]
        ys = [c[1] for c in centres]
        spread = max(max(xs) - min(xs), max(ys) - min(ys))
        if spread < min_spread:
            continue
        candidates.append((radius, centres, spread))

    if not all_radii:
        # a mounting hole is a screw-sized circle repeated a few times, well
        # spread out. Everything else is a pad, a via or a rounded corner.
        candidates = [c for c in candidates
                      if any(abs(c[0] - r) < 0.06 for r in SCREW_RADII)]

    if not candidates:
        print("  nothing that looks like a mounting hole "
              "(try --all-radii to see every group)")
        return

    for radius, centres, spread in sorted(candidates, key=lambda c: -c[2]):
        screw = next((s for r, s in SCREW_RADII.items() if abs(radius - r) < 0.06), "?")
        print(f"\n  radius {radius:.3f} mm  (dia {radius * 2:.2f}, ~{screw})"
              f"  x{len(centres)}  spread {spread:.1f} mm")
        for cx, cy in sorted(centres):
            print(f"      - {{at: [{cx:.2f}, {cy:.2f}], diameter: {radius * 2:.2f}}}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--all-radii", action="store_true",
                    help="show every repeated circle group, not just screw sizes")
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--min-spread", type=float, default=10.0,
                    help="mm; ignore groups clustered in one spot (pads, vias)")
    args = ap.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.files:
        paths += [Path(p) for p in glob.glob(pattern)]
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1
    for path in paths:
        report(path, args.all_radii, args.min_count, args.min_spread)
    return 0


if __name__ == "__main__":
    sys.exit(main())
