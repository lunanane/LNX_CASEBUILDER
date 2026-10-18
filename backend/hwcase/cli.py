"""Command line front end.

    python -m hwcase.cli parts
    python -m hwcase.cli check scenes/soundmachine-v0.yaml
    python -m hwcase.cli build scenes/soundmachine-v0.yaml -o ../out
    python -m hwcase.cli build scenes/frame-6.yaml -o ../out

`check` and `build` follow the scene's `kind`: a layered case for `parts`,
and for `rack` the frame -- its summary, and its plates' sheets with a cut
list for the rails, M5s and U-channel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import case as case_mod
from . import export, flat
from . import rack as rack_mod
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


def _load(args, scene=None):
    lib = PartLibrary.load(args.parts)
    scene = scene if scene is not None else load_scene(args.scene)
    res = resolve(scene, lib)
    return lib, scene, res


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------
#
# A `kind: rack` scene has no placements to resolve and no layers to stack:
# its shape comes from the format. So both commands branch on the kind before
# touching the parts library, and a frame never goes near `case.build` -- a
# layered case wrapped around a frame's empty placement list is a box around
# nothing, written next to the frame's files as if it meant something.


def _frame_issues(scene, model) -> list[tuple[str, str, str]]:
    """(level, code, message) for a frame that built.

    The frame's geometry is checked as it is built -- an impossible depth or
    an unknown row format raises before there is a model -- so what is left
    to ask is whether its plates can be cut from the stock on the shelf.
    """
    issues = []
    try:
        export.pack_sheets(flat.from_rack(model), stock=scene.case)
    except ValueError as exc:
        issues.append(("error", "stock", str(exc)))
    if scene.placements:
        issues.append(("info", "placements",
                       f"{len(scene.placements)} placements are ignored: a "
                       f"frame is built from its rows, not from parts"))
    return issues


def _frame_summary(scene, model) -> None:
    rows = " + ".join(r.fmt.name for r in model.rows)
    print(f"frame {scene.name}: {model.hp:g} HP, {len(model.rows)} rows "
          f"({rows})")
    print(f"  width   {model.width:.2f} mm between the panels, "
          f"{model.outer_width:.2f} mm outside them")
    print(f"  depth   {model.depth:.2f} mm "
          f"(module depth {model.module_depth:.2f})")
    print(f"  panel   {model.panel_height:.2f} mm tall, "
          f"stands on its {model.stands_on}")
    print("\nbill of materials:")
    for item, n in model.bill_of_materials().items():
        print(f"  {n:g} x {item}" if not item.endswith(" mm")
              else f"  {item[:-3]}: {n:.2f} mm")
    order = model.order_spec()
    if order is None:
        print(f"\nlaser-cut: {len(model.plates)} plates, "
              f"{model.panel_t:g} mm sheet, {len(model.joints)} joints")
        return
    print(f"\nU-channel order: length {order['length_mm']} mm, "
          f"A / B / C {order['width_a_mm']} / {order['width_b_mm']} / "
          f"{order['width_c_mm']} mm, {order['thickness_mm']} mm thick")


def _frame_model(scene):
    try:
        return rack_mod.build_spec(scene.rack)
    except (KeyError, ValueError) as exc:
        print(f"frame {scene.name}: cannot be built\n\n  [X] {'frame':<18} {exc}")
        return None


def _print_issues(issues) -> tuple[int, int]:
    print()
    for level in ("error", "warning", "info"):
        for lv, code, message in issues:
            if lv == level:
                print(f"  [{LEVEL_MARK[level]}] {code:<18} {message}")
    errors = sum(1 for lv, _c, _m in issues if lv == "error")
    warnings = sum(1 for lv, _c, _m in issues if lv == "warning")
    print(f"\n{errors} errors, {warnings} warnings")
    return errors, warnings


def check_frame(scene, args) -> int:
    model = _frame_model(scene)
    if model is None:
        print("\n1 errors, 0 warnings")
        return 1 if args.strict else 0
    _frame_summary(scene, model)
    errors, _w = _print_issues(_frame_issues(scene, model))
    return 1 if errors and args.strict else 0


def build_frame(scene, args) -> int:
    """Write a frame's sheets (SVG and DXF), its cut list and its JSON."""
    model = _frame_model(scene)
    if model is None:
        return 1
    _frame_summary(scene, model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    js = out / f"{scene.name}-frame.json"
    js.write_text(json.dumps(export.rack_to_json(model), indent=2),
                  encoding="utf-8")
    written.append(js)
    sheets_dir = out / f"{scene.name}-sheets"
    try:
        files = (export.rack_sheet_files(model, "svg", scene.name, scene.case)
                 + export.rack_sheet_files(model, "dxf", scene.name,
                                           scene.case)[:-1])
    except ValueError as exc:
        # unlike a case's strip export there is nothing else to cut from, so
        # a frame whose plates fit no stock is a failed build
        print(f"\n  [X] {'stock':<18} {exc}")
        return 1
    sheets_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files:
        target = sheets_dir / name
        target.write_text(content, encoding="utf-8")
        written.append(target)
    print("\nwrote:")
    for p in written:
        print(f"  {p}")
    return 0


def cmd_check(args) -> int:
    scene = load_scene(args.scene)
    if scene.kind == "rack":
        return check_frame(scene, args)
    lib, scene, res = _load(args, scene)
    issues = check(res, lib)
    x0, y0, z0, x1, y1, z1 = res.bounds()
    print(f"scene {scene.name}: {len(scene.placements)} placements")
    print(f"  extent  {x1 - x0:.1f} x {y1 - y0:.1f} x {z1 - z0:.1f} mm")
    print(f"  bounds  x {x0:.1f}..{x1:.1f}  y {y0:.1f}..{y1:.1f}  z {z0:.1f}..{z1:.1f}")
    for name, z in res.panels.items():
        riders = [p.id for p in scene.placements if p.on_panel == name]
        print(f"  panel   {name!r} at z {z:.2f}  ({len(riders)} placements: "
              f"{', '.join(riders) or 'none'})")
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
    scene = load_scene(args.scene)
    if scene.kind == "rack":
        return build_frame(scene, args)
    lib, scene, res = _load(args, scene)
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


def cmd_audit(args) -> int:
    """Check every part against the vendor model it claims to come from.

    A part file is a set of assertions about a physical object; this is the
    one automated second opinion available. Exit code is non-zero only for
    real disagreements -- a part whose `cad:` points at a datasheet is
    information, not a failure.
    """
    from .audit import audit_library

    lib = PartLibrary.load(args.parts) if args.parts else PartLibrary.load()
    results = audit_library(lib, tolerance=args.tolerance)

    problems = 0
    for a in results:
        real = [f for f in a.findings if f.severity != "info"]
        problems += len(real)
        if a.ok:
            print(f"  ok   {a.part:<40} {a.source}")
            continue
        if not a.findings:
            print(f"  --   {a.part:<40} no model to check against")
            continue
        print(f"  {'!!' if real else 'ii'}   {a.part:<40} {a.source or ''}")
        for f in a.findings:
            print(f"         {f.kind}: {f.message}")

    checked = sum(1 for a in results if a.checked)
    print()
    print(f"{checked} of {len(results)} parts measured against a model, "
          f"{problems} disagreement{'' if problems == 1 else 's'}")
    return 1 if (problems and args.strict) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwcase")
    ap.add_argument("--parts", default=None, help="parts directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("parts", help="list the library").set_defaults(fn=cmd_parts)

    c = sub.add_parser("check", help="resolve a scene and report problems")
    c.add_argument("scene")
    c.add_argument("--strict", action="store_true", help="exit non-zero on errors")
    c.set_defaults(fn=cmd_check)

    a = sub.add_parser("audit", help="check the library against its vendor CAD")
    a.add_argument("--tolerance", type=float, default=0.3,
                   help="mm of disagreement to tolerate before saying so")
    a.add_argument("--strict", action="store_true",
                   help="exit non-zero when a part disagrees with its model")
    a.set_defaults(fn=cmd_audit)

    b = sub.add_parser("build", help="generate the case and export it")
    b.add_argument("scene")
    b.add_argument("-o", "--out", default="out")
    b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
