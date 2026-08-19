"""Command line front end.

    python -m hwcase.cli parts
    python -m hwcase.cli check scenes/soundmachine-v0.yaml
    python -m hwcase.cli build scenes/soundmachine-v0.yaml -o ../out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import case as case_mod
from . import export
from .library import PartLibrary, load_scene
from .scene import check, resolve

LEVEL_MARK = {"error": "X", "warning": "!", "info": "-"}


def cmd_parts(args) -> int:
    lib = PartLibrary.load(args.parts)
    for category, parts in sorted(lib.by_category().items()):
        print(f"\n{category}")
        for p in sorted(parts, key=lambda x: x.id):
            lo, hi = p.z_extent()
            try:
                w, h = p.outline.size  # type: ignore[union-attr]
                size = f"{w:.1f} x {h:.1f}"
            except AttributeError:
                size = "poly"
            print(f"  {p.id:<32} {size:>14} mm  h {hi - lo:5.1f}  "
                  f"[{p.min_confidence().value}]")
    print(f"\n{len(lib)} parts")
    return 0


def _load(args):
    lib = PartLibrary.load(args.parts)
    scene = load_scene(args.scene)
    res = resolve(scene, lib)
    return lib, scene, res


def cmd_check(args) -> int:
    lib, scene, res = _load(args)
    issues = check(res, lib)
    x0, y0, z0, x1, y1, z1 = res.bounds()
    print(f"scene {scene.name}: {len(scene.placements)} placements")
    print(f"  extent  {x1 - x0:.1f} x {y1 - y0:.1f} x {z1 - z0:.1f} mm")
    print(f"  bounds  x {x0:.1f}..{x1:.1f}  y {y0:.1f}..{y1:.1f}  z {z0:.1f}..{z1:.1f}")
    print()
    for level in ("error", "warning", "info"):
        for i in issues:
            if i.level == level:
                print(f"  [{LEVEL_MARK[level]}] {i.code:<18} {i.message}")
    errors = sum(1 for i in issues if i.level == "error")
    warnings = sum(1 for i in issues if i.level == "warning")
    print(f"\n{errors} errors, {warnings} warnings")
    return 1 if errors and args.strict else 0


def cmd_build(args) -> int:
    lib, scene, res = _load(args)
    issues = check(res, lib)
    model = case_mod.build(res)
    out = Path(args.out)
    written = export.write_all(res, model, lib, out, issues)
    print(f"case: {model.height:.1f} mm tall, {len(model.layers)} layers")
    for layer in model.layers:
        note = ("; ".join(layer.notes))[:70]
        print(f"  {layer.index:02d} {layer.role:<5} z {layer.z0:7.2f}..{layer.z1:7.2f}  "
              f"{layer.material.name:<14} {note}")
    print("\nbill of materials:")
    for mat, n in model.bill_of_materials().items():
        print(f"  {n} x {mat}")
    print("\nwrote:")
    for p in written:
        print(f"  {p}")
    errors = sum(1 for i in issues if i.level == "error")
    if errors:
        print(f"\n{errors} errors -- run `check` before you cut anything")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwcase")
    ap.add_argument("--parts", default=None, help="parts directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("parts", help="list the library").set_defaults(fn=cmd_parts)

    c = sub.add_parser("check", help="resolve a scene and report problems")
    c.add_argument("scene")
    c.add_argument("--strict", action="store_true", help="exit non-zero on errors")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("build", help="generate the case and export it")
    b.add_argument("scene")
    b.add_argument("-o", "--out", default="out")
    b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
