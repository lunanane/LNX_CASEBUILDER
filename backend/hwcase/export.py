"""Writers: SVG and DXF for the cutter, JSON for the browser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from shapely.affinity import translate
from shapely.geometry import Polygon

from .case import CaseModel, Layer, kerf_compensated
from .library import PartLibrary
from .scene import Resolved

SHEET_MARGIN = 10.0
LAYER_GAP = 8.0


from .geom import polygons as _polys


def _rings(poly: Polygon) -> list[list[tuple[float, float]]]:
    return [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]


def pack(case: CaseModel) -> list[tuple[Layer, float, float]]:
    """Lay the layers out side by side, wrapping at the sheet width."""
    sheet_w = case.layers[0].material.sheet[0] if case.layers else 600.0
    placed: list[tuple[Layer, float, float]] = []
    cx, cy, row_h = SHEET_MARGIN, SHEET_MARGIN, 0.0
    for layer in case.layers:
        polys = _polys(layer.geom)
        if not polys:
            continue
        x0, y0, x1, y1 = layer.geom.bounds
        w, h = x1 - x0, y1 - y0
        if cx + w > sheet_w - SHEET_MARGIN and cx > SHEET_MARGIN:
            cx = SHEET_MARGIN
            cy += row_h + LAYER_GAP
            row_h = 0.0
        placed.append((layer, cx - x0, cy - y0))
        cx += w + LAYER_GAP
        row_h = max(row_h, h)
    return placed


def to_svg(case: CaseModel, apply_kerf: bool = True) -> str:
    placed = pack(case)
    parts: list[str] = []
    max_x = max_y = 0.0
    for layer, dx, dy in placed:
        geom = kerf_compensated(layer.geom, layer.material.kerf) if apply_kerf else layer.geom
        geom = translate(geom, dx, dy)
        x0, y0, x1, y1 = geom.bounds
        max_x, max_y = max(max_x, x1), max(max_y, y1)
        paths = []
        for poly in _polys(geom):
            for ring in _rings(poly):
                d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in ring) + " Z"
                paths.append(f'<path d="{d}"/>')
        # Engraving is never kerf-compensated: a mark is where you asked
        # for it, and widening it by half a kerf just makes it fat.
        marks = []
        if layer.engrave is not None and not layer.engrave.is_empty:
            for poly in _polys(translate(layer.engrave, dx, dy)):
                for ring in _rings(poly):
                    d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in ring) + " Z"
                    marks.append(f'<path d="{d}"/>')

        label = f"{layer.index:02d} {layer.role} {layer.material.name} " \
                f"z {layer.z0:.1f}..{layer.z1:.1f}"
        parts.append(
            f'<g id="layer-{layer.index}" data-role="{layer.role}" '
            f'data-material="{layer.material.name}">\n'
            f'  <title>{label}</title>\n  ' + "\n  ".join(paths) + "\n</g>"
        )
        if marks:
            # Blue, and its own group. Every cutter's software wants cut and
            # engrave separated by colour or by layer; this does both.
            parts.append(
                f'<g id="engrave-{layer.index}" data-role="engrave" '
                f'stroke="#0000ff">\n  <title>{label} -- ENGRAVE, do not cut'
                f'</title>\n  ' + "\n  ".join(marks) + "\n</g>"
            )
    w = max_x + SHEET_MARGIN
    h = max_y + SHEET_MARGIN
    body = "\n".join(parts)
    # y is flipped so the SVG reads the same way up as the 3D view
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" '
        f'height="{h:.2f}mm" viewBox="0 0 {w:.2f} {h:.2f}">\n'
        f'<g transform="translate(0,{h:.2f}) scale(1,-1)" fill="none" '
        f'stroke="#ff0000" stroke-width="0.1">\n{body}\n</g>\n</svg>\n'
    )


def to_dxf(case: CaseModel, path: Path, apply_kerf: bool = True) -> Path:
    import ezdxf

    doc = ezdxf.new(setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for layer, dx, dy in pack(case):
        name = f"L{layer.index:02d}_{layer.role}"
        if name not in doc.layers:
            doc.layers.add(name)
        geom = kerf_compensated(layer.geom, layer.material.kerf) if apply_kerf else layer.geom
        geom = translate(geom, dx, dy)
        for poly in _polys(geom):
            for ring in _rings(poly):
                msp.add_lwpolyline([(x, y) for x, y in ring], close=True,
                                   dxfattribs={"layer": name})

        if layer.engrave is not None and not layer.engrave.is_empty:
            mark_layer = f"{name}_ENGRAVE"
            if mark_layer not in doc.layers:
                doc.layers.add(mark_layer, color=5)         # blue
            for poly in _polys(translate(layer.engrave, dx, dy)):
                for ring in _rings(poly):
                    msp.add_lwpolyline([(x, y) for x, y in ring], close=True,
                                       dxfattribs={"layer": mark_layer})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _clipped_outline(poly: Polygon, res: Resolved) -> list[list[float]]:
    """Trim a region to the case outline so the browser can draw it."""
    from .case import outer_shape

    try:
        clipped = poly.intersection(outer_shape(res, res.scene.case))
    except Exception:
        clipped = poly
    if clipped.is_empty:
        return []
    biggest = max(_polys(clipped), key=lambda p: p.area, default=None)
    return [list(c) for c in biggest.exterior.coords] if biggest else []


def scene_to_json(res: Resolved, lib: PartLibrary, issues: Iterable = ()) -> dict:
    """What the browser needs to draw the scene: boxes, frames, issues."""
    out_solids = []
    for s in res.solids:
        x0, y0, x1, y1 = s.poly.bounds
        out_solids.append({
            "placement": s.placement, "part": s.part, "name": s.name,
            "kind": s.kind.value,
            "bounds": [x0, y0, s.z[0], x1, y1, s.z[1]],
            "outline": [list(c) for c in s.poly.exterior.coords],
            "confidence": s.src.confidence.value,
        })
    return {
        "name": res.scene.name,
        "anchor": res.scene.anchor,
        "floor": res.floor,
        "panels": [{"name": p.name, "z": res.panels.get(p.name),
                    "from_ref": p.from_ref, "note": p.note}
                   for p in res.scene.panels],
        "frames": {k: {"pos": list(f.pos), "rot_z": f.rot_z,
                       "tilt": f.tilt, "flip": f.flip}
                   for k, f in res.frames.items()},
        "placements": [p.model_dump(mode="json") for p in res.scene.placements],
        "solids": out_solids,
        "connectors": [{
            "ref": c.ref, "placement": c.placement, "part": c.part,
            "name": c.conn.name, "type": c.conn.type,
            "at": list(c.at), "normal": list(c.face_normal),
            "external": c.conn.external,
            "face": c.conn.face.value,
            "policy": c.policy.value,
            "included": c.included,
            "cuts_the_wall": c.cuts_the_wall,
            # how far a plug + its cable bend must stay clear, along `normal`
            "reach": c.conn.plug_depth + (c.conn.bend_radius if c.conn.external else 0.0),
        } for c in res.connectors],
        # clipped to the case outline: the raw region reaches 1000 mm out so
        # that any outline is guaranteed to be cut, which is useless to draw
        "supports": [{
            "ref": s.ref, "placement": s.placement, "mode": s.mode.value,
            "at": list(s.at), "screw_d": s.screw_d, "screw": s.screw,
            "board_bottom": s.board_bottom, "board_top": s.board_top,
        } for s in res.supports],
        "side_openings": [{
            "ref": o.ref, "placement": o.placement, "side": o.side.value,
            "z": list(o.z), "reason": o.reason,
            "outline": _clipped_outline(o.poly, res),
        } for o in res.side_openings],
        "issues": [{"level": i.level, "code": i.code, "message": i.message,
                    "refs": i.refs} for i in issues],
    }


def case_to_json(case: CaseModel) -> dict:
    return {
        "z0": case.z0, "z1": case.z1,
        "bom": case.bill_of_materials(),
        "layers": [{
            "index": l.index, "role": l.role, "z0": l.z0, "z1": l.z1,
            "material": l.material.model_dump(mode="json"),
            "notes": l.notes,
            "rings": [[list(c) for c in ring]
                      for poly in _polys(l.geom) for ring in _rings(poly)],
            "engrave": ([[list(c) for c in ring]
                         for poly in _polys(l.engrave) for ring in _rings(poly)]
                        if l.engrave is not None and not l.engrave.is_empty
                        else []),
        } for l in case.layers],
    }


def write_all(res: Resolved, case: CaseModel, lib: PartLibrary, outdir: Path,
              issues: Iterable = ()) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    svg = outdir / f"{res.scene.name}-layers.svg"
    svg.write_text(to_svg(case), encoding="utf-8")
    written.append(svg)
    written.append(to_dxf(case, outdir / f"{res.scene.name}-layers.dxf"))
    js = outdir / f"{res.scene.name}-scene.json"
    js.write_text(json.dumps(scene_to_json(res, lib, issues), indent=2), encoding="utf-8")
    written.append(js)
    cj = outdir / f"{res.scene.name}-case.json"
    cj.write_text(json.dumps(case_to_json(case), indent=2), encoding="utf-8")
    written.append(cj)
    return written
