#!/usr/bin/env python3
"""Pull mounting-hole positions out of a vendor STEP file.

A front end onto `hwcase.measure.step_holes`, so this and the editor's import
agree about what a mounting hole is.

The STL exports have no holes in them -- a mesh of a board is a slab and the
drilled features are gone. STEP keeps them, because it is a B-Rep: a hole is a
cylindrical face bounded by two circles, and STEP stores those as text.

    #4711 = CIRCLE ( 'NONE', #4712, 1.2500000 ) ;
    #4712 = AXIS2_PLACEMENT_3D ( 'NONE', #4713, #4714, #4715 ) ;
    #4713 = CARTESIAN_POINT ( 'NONE', ( 3.0, 3.0, 0.0 ) ) ;

So: find every CIRCLE, follow its placement to a point, group by radius, and
report the groups that look like mounting holes -- a screw-sized circle that
repeats and is spread across the board. A pad, a via or a rounded corner fails
one of those three. No CAD kernel needed.

    python tools/extract_holes.py "vendor/cad/adafruit/3954/*.step"
    python tools/extract_holes.py --all-radii vendor/cad/adafruit/5752/*.step

Positions are printed in the STEP's own frame. Subtract the board's minimum
corner (`measure_cad.py` prints it) to get part coordinates.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from hwcase.measure import SCREW_RADII, step_holes  # noqa: E402


def report(path: Path, all_radii: bool, min_count: int, min_spread: float,
           origin: tuple[float, float]) -> None:
    groups = step_holes(path, min_count=min_count, min_spread=min_spread,
                        screws_only=not all_radii)
    print(f"\n=== {path.name}")
    if not groups:
        print("  nothing that looks like a mounting hole "
              "(try --all-radii to see every repeated circle)")
        return

    for g in groups:
        print(f"\n  radius {g.radius:.3f} mm  (dia {g.diameter:.2f}, "
              f"~{g.screw or 'unknown thread'})  x{len(g.centres)}  "
              f"spread {g.spread:.1f} mm")
        for cx, cy in g.centres:
            print(f"      - {{at: [{cx - origin[0]:.2f}, {cy - origin[1]:.2f}], "
                  f"diameter: {g.diameter:.2f}, screw: {g.screw or '?'}}}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--all-radii", action="store_true",
                    help="show every repeated circle group, not just screw sizes")
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--min-spread", type=float, default=10.0,
                    help="mm; ignore groups clustered in one spot (pads, vias)")
    ap.add_argument("--origin", type=float, nargs=2, default=(0.0, 0.0),
                    metavar=("X", "Y"),
                    help="subtract this from every position, to convert the "
                         "STEP's frame into the part's")
    args = ap.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.files:
        paths += [Path(p) for p in sorted(glob.glob(pattern))]
    if not paths:
        print("no files matched", file=sys.stderr)
        return 1

    print(f"screw sizes recognised: "
          f"{', '.join(sorted(set(SCREW_RADII.values())))}")
    for path in paths:
        try:
            report(path, args.all_radii, args.min_count, args.min_spread,
                   tuple(args.origin))
        except Exception as exc:
            print(f"\n=== {path.name}\n  {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
