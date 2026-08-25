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


def _holes_then_outlines(geom):
    """Rings split into (holes, outlines), each a list of coordinate lists.

    A cutter runs paths in file order, and a part whose outer edge is cut
    first falls out of the sheet before its holes exist. So: every interior
    ring is a hole, every exterior is an outline, and outlines are sorted
    smallest piece first so the edge that frees the largest chunk of material
    is always the very last path.
    """
    polys = sorted(_polys(geom), key=lambda pp: pp.area)
    holes = [list(r.coords) for pp in polys for r in pp.interiors]
    outlines = [list(pp.exterior.coords) for pp in polys]
    return holes, outlines


def _ring_path(ring) -> str:
    return ('<path d="M '
            + " L ".join(f"{x:.3f},{y:.3f}" for x, y in ring) + ' Z"/>')


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
    tail: list[str] = []
    max_x = max_y = 0.0
    for layer, dx, dy in placed:
        geom = kerf_compensated(layer.geom, layer.material.kerf) if apply_kerf else layer.geom
        geom = translate(geom, dx, dy)
        x0, y0, x1, y1 = geom.bounds
        max_x, max_y = max(max_x, x1), max(max_y, y1)
        holes, outlines = _holes_then_outlines(geom)
        # Engraving is never kerf-compensated: a mark is where you asked
        # for it, and widening it by half a kerf just makes it fat.
        marks = []
        if layer.engrave is not None and not layer.engrave.is_empty:
            for poly in _polys(translate(layer.engrave, dx, dy)):
                for ring in _rings(poly):
                    marks.append(_ring_path(ring))

        label = f"{layer.index:02d} {layer.role} {layer.material.name} " \
                f"z {layer.z0:.1f}..{layer.z1:.1f}"
        parts.append(
            f'<g id="layer-{layer.index}" data-role="{layer.role}" '
            f'data-pass="holes" data-material="{layer.material.name}">\n'
            f'  <title>{label} -- inner cuts first</title>\n  '
            + "\n  ".join(_ring_path(r) for r in holes) + "\n</g>"
        )
        if marks:
            # Blue, and its own group. Every cutter's software wants cut and
            # engrave separated by colour or by layer; this does both.
            parts.append(
                f'<g id="engrave-{layer.index}" data-role="engrave" '
                f'stroke="#0000ff">\n  <title>{label} -- ENGRAVE, do not cut'
                f'</title>\n  ' + "\n  ".join(marks) + "\n</g>"
            )
        # Outer edges at the very END of the file, after every hole of every
        # layer: a cutter runs paths in document order, and an outline run
        # early drops the part out of the sheet before its holes exist.
        tail.append(
            f'<g id="layer-{layer.index}-outline" data-role="{layer.role}" '
            f'data-pass="outline" data-material="{layer.material.name}">\n'
            f'  <title>{label} -- outer edge, cut last</title>\n  '
            + "\n  ".join(_ring_path(r) for r in outlines) + "\n</g>"
        )
    w = max_x + SHEET_MARGIN
    h = max_y + SHEET_MARGIN
    body = "\n".join(parts + tail)
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
    # Entity order IS cut order on a cutter that runs the file as-is, so the
    # outer edges are queued and appended after every hole of every layer:
    # an outline run early drops the part before its holes are cut.
    deferred: list[tuple[list, str]] = []
    for layer, dx, dy in pack(case):
        name = f"L{layer.index:02d}_{layer.role}"
        if name not in doc.layers:
            doc.layers.add(name)
        geom = kerf_compensated(layer.geom, layer.material.kerf) if apply_kerf else layer.geom
        geom = translate(geom, dx, dy)
        holes, outlines = _holes_then_outlines(geom)
        for ring in holes:
            msp.add_lwpolyline([(x, y) for x, y in ring], close=True,
                               dxfattribs={"layer": name})
        deferred.extend((ring, name) for ring in outlines)

        if layer.engrave is not None and not layer.engrave.is_empty:
            mark_layer = f"{name}_ENGRAVE"
            if mark_layer not in doc.layers:
                doc.layers.add(mark_layer, color=5)         # blue
            for poly in _polys(translate(layer.engrave, dx, dy)):
                for ring in _rings(poly):
                    msp.add_lwpolyline([(x, y) for x, y in ring], close=True,
                                       dxfattribs={"layer": mark_layer})
    for ring, name in deferred:
        msp.add_lwpolyline([(x, y) for x, y in ring], close=True,
                           dxfattribs={"layer": name})
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

    # The files that actually go to the machine: one per bed-load, in their
    # own folder so the strip files above stay for looking at. A case too big
    # for the configured bed is reported, not fatal -- the strip export still
    # stands and the message says which layer will not fit.
    sheets_dir = outdir / f"{res.scene.name}-sheets"
    try:
        files = sheet_files(case, "svg", res.scene.name)
        sheets_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files:
            target = sheets_dir / name
            target.write_text(content, encoding="utf-8")
            written.append(target)
    except ValueError as exc:
        print(f"  (no sheet files: {exc})")
    return written


# ---------------------------------------------------------------------------
# sheet packing: one file per bed-load
# ---------------------------------------------------------------------------
#
# The single-file export lays every layer on one endless strip, which is fine
# for looking at and useless at the machine: a real bed is 350 x 350, has
# clamps at the edges, and eleven layers do not fit on it. So the cut is
# split into sheets -- each one file, each guaranteed to fit the bed with the
# margin respected, parts rotated 90 degrees when that packs tighter.
#
# The packer is a shelf packer (rows of parts, tallest-first), not a true
# nesting solver. Layers are rectangles to within a few percent -- the case
# outline IS the part -- so shelf packing is within spitting distance of
# optimal here, deterministic, and simple enough to trust at 2 am. Real
# irregular-shape nesting stays on the roadmap.

from dataclasses import dataclass, field as _field


@dataclass
class SheetPlacement:
    layer: Layer
    x: float               # sheet coordinates of the part's min corner
    y: float
    rotated: bool          # turned 90 degrees counter-clockwise
    geom: object           # the placed cut geometry (kerf applied)
    engrave: object        # the placed engrave geometry, or None
    #: cut mirrored so its surface marks end up on the OUTSIDE face: the
    #: plate is installed engraved side down (only the floor does this)
    mirrored: bool = False


@dataclass
class PackedSheet:
    index: int
    width: float
    height: float
    placements: list = _field(default_factory=list)


def _placed(geom, rotated: bool, tx: float, ty: float, anchor=None):
    """The geometry, rotated then moved so the min corner of `anchor` (the
    geometry itself when not given) sits at (tx, ty).

    `anchor` exists for the engrave pass: marks must ride the SAME transform
    as the cut outline they sit on. Normalising them by their own bounds --
    the old behaviour -- pinned every grill and label to the plate's corner.
    """
    from shapely.affinity import rotate as _rotate

    if geom is None or geom.is_empty:
        return None
    if rotated:
        geom = _rotate(geom, 90, origin=(0, 0))
    if anchor is not None and not anchor.is_empty:
        ref = _rotate(anchor, 90, origin=(0, 0)) if rotated else anchor
    else:
        ref = geom
    x0, y0, _x1, _y1 = ref.bounds
    return translate(geom, tx - x0, ty - y0)


def pack_sheets(case: CaseModel, apply_kerf: bool = True) -> list[PackedSheet]:
    """Split the layers across the available cutting plates.

    Tallest-part-first shelf packing with 90-degree rotation. Within a shelf
    the orientation that wastes the least shelf height wins; a part that only
    fits one way round is turned automatically.

    `spec.plates` lists the plate sizes actually on the shelf -- one entry is
    the ordinary "a stack of 350 x 350" case, several describe a mixed stock
    of full sheets and offcuts. When a part fits no open sheet, a new plate is
    started: the SMALLEST usable area that still holds the part. Parts arrive
    largest first, so the part being placed is the biggest still unplaced,
    and the small offcuts get consumed by exactly the work that fits them
    instead of a full sheet being broken for a 60 mm boss ring.

    A layer that fits no plate in either orientation is a hard error naming
    the layer and every plate -- silently dropping a floor plate is not an
    export.
    """
    spec = case.spec
    margin = max(0.0, spec.sheet_margin)
    gap = max(0.0, spec.sheet_spacing)
    if not spec.plates:
        raise ValueError("no cutting plates defined -- add at least one size")
    #: (plate_w, plate_h, usable_w, usable_h), as declared
    plates = [(pw, ph, pw - 2 * margin, ph - 2 * margin)
              for pw, ph in spec.plates]

    def fits_plate(w: float, h: float, uw: float, uh: float) -> bool:
        return (w <= uw + 1e-9 and h <= uh + 1e-9) or \
               (h <= uw + 1e-9 and w <= uh + 1e-9)

    parts = []
    for layer in case.layers:
        geom = (kerf_compensated(layer.geom, layer.material.kerf)
                if apply_kerf else layer.geom)
        if geom.is_empty:
            continue
        x0, y0, x1, y1 = geom.bounds
        w, h = x1 - x0, y1 - y0
        if not any(fits_plate(w, h, uw, uh) for _pw, _ph, uw, uh in plates):
            stock = ", ".join(f"{pw:.0f} x {ph:.0f}" for pw, ph, _u, _v in plates)
            raise ValueError(
                f"layer {layer.index} ({layer.role}) is {w:.0f} x {h:.0f} mm "
                f"and fits none of the plates ({stock}, each minus the "
                f"{margin:.0f} mm margin) in either orientation -- it cannot "
                f"be cut from this stock")
        engrave = layer.engrave
        mirrored = False
        if (layer.role == "floor" and engrave is not None
                and not engrave.is_empty):
            # Surface marks on the floor belong on its OUTSIDE (bottom) face.
            # A laser engraves the face looking up at it, so the whole plate
            # is cut mirrored -- outline and marks together -- and installed
            # engraved side down, where the flip puts every hole back where
            # the design says it is.
            from shapely.affinity import scale as _scale
            geom = _scale(geom, xfact=-1, yfact=1, origin=(0, 0))
            engrave = _scale(engrave, xfact=-1, yfact=1, origin=(0, 0))
            mirrored = True
        parts.append((layer, geom, engrave, mirrored, w, h))

    # Tallest first: shelf height is set by the tallest part in the row, so
    # placing tall parts together keeps short rows short.
    parts.sort(key=lambda p: (-max(p[4], p[5]), p[0].index))

    # a shelf: [y, height, x-cursor]; a sheet: its shelves + its usable size
    sheets: list[dict] = []
    out: list[PackedSheet] = []

    def orientations(w: float, h: float):
        yield False, w, h
        if abs(w - h) > 1e-9:
            yield True, h, w

    for layer, geom, engrave, mirrored, w, h in parts:
        best = None  # (waste, sheet_i, shelf_i | None, rotated, pw, ph)
        for si, sheet in enumerate(sheets):
            shelves, uw, uh = sheet["shelves"], sheet["uw"], sheet["uh"]
            for hi, (sy, sh, sx) in enumerate(shelves):
                for rotated, pw, ph in orientations(w, h):
                    if ph <= sh + 1e-9 and sx + pw <= uw + 1e-9:
                        cand = (sh - ph, si, hi, rotated, pw, ph)
                        if best is None or cand < best:
                            best = cand
            # a new shelf on this sheet, below the existing ones
            top = shelves[-1][0] + shelves[-1][1] + gap if shelves else 0.0
            for rotated, pw, ph in orientations(w, h):
                if pw <= uw + 1e-9 and top + ph <= uh + 1e-9:
                    cand = (ph * 0.01, si, None, rotated, pw, ph)
                    if best is None or cand < best:
                        best = cand

        if best is None:
            # No open sheet takes it: start the smallest plate that does.
            # Ties (same area) break towards the earlier list entry, so the
            # order the user wrote their stock in is meaningful and the result
            # stays deterministic.
            pw_, ph_, uw, uh = min(
                (p for p in plates if fits_plate(w, h, p[2], p[3])),
                key=lambda p: (p[2] * p[3], plates.index(p)))
            # flattest orientation first so the opening shelf is short
            rotated, pw, ph = min(
                ((r, ow, oh) for r, ow, oh in orientations(w, h)
                 if ow <= uw + 1e-9 and oh <= uh + 1e-9),
                key=lambda o: o[2])
            sheets.append({"shelves": [], "uw": uw, "uh": uh})
            out.append(PackedSheet(len(out), pw_, ph_))
            best = (0.0, len(sheets) - 1, None, rotated, pw, ph)

        _waste, si, hi, rotated, pw, ph = best
        shelves = sheets[si]["shelves"]
        if hi is None:
            sy = shelves[-1][0] + shelves[-1][1] + gap if shelves else 0.0
            shelves.append([sy, ph, 0.0])
            hi = len(shelves) - 1
        sy, sh, sx = shelves[hi]

        tx, ty = margin + sx, margin + sy
        out[si].placements.append(SheetPlacement(
            layer, tx, ty, rotated,
            _placed(geom, rotated, tx, ty),
            _placed(engrave, rotated, tx, ty, anchor=geom)
            if engrave is not None and not engrave.is_empty
            else None,
            mirrored))
        shelves[hi][2] = sx + pw + gap

    return out


def _path_group(geom, indent: str = "  ") -> list[str]:
    paths = []
    for poly in _polys(geom):
        for ring in _rings(poly):
            d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in ring) + " Z"
            paths.append(f'{indent}<path d="{d}"/>')
    return paths


def sheet_to_svg(sheet: PackedSheet) -> str:
    """One bed-load as one SVG, canvas exactly the physical sheet.

    No sheet boundary is drawn: cutters that run every path in the file were
    cutting the frame as a 350 x 350 cut. The canvas itself is the bed size,
    which is all the alignment the frame ever provided.

    Path order is cut order on such a cutter, so every hole of every part
    comes first and every outer edge comes last -- an outline run early
    drops the part out of the sheet before its holes exist.
    """
    w, h = sheet.width, sheet.height
    parts: list[str] = []
    tail: list[str] = []
    for pl in sheet.placements:
        layer = pl.layer
        label = (f"{layer.index:02d} {layer.role} {layer.material.name}"
                 + (" (rotated)" if pl.rotated else ""))
        holes, outlines = _holes_then_outlines(pl.geom)
        parts.append(
            f'<g id="layer-{layer.index}" data-role="{layer.role}" '
            f'data-pass="holes" data-material="{layer.material.name}">\n'
            f'  <title>{label} -- inner cuts first</title>\n  '
            + "\n  ".join(_ring_path(r) for r in holes) + "\n</g>")
        if pl.engrave is not None:
            parts.append(
                f'<g id="engrave-{layer.index}" data-role="engrave" '
                f'stroke="#0000ff">\n'
                f'  <title>{label} -- ENGRAVE, do not cut</title>\n'
                + "\n".join(_path_group(pl.engrave)) + "\n</g>")
        tail.append(
            f'<g id="layer-{layer.index}-outline" data-role="{layer.role}" '
            f'data-pass="outline" data-material="{layer.material.name}">\n'
            f'  <title>{label} -- outer edge, cut last</title>\n  '
            + "\n  ".join(_ring_path(r) for r in outlines) + "\n</g>")

    body = "\n".join(parts + tail)
    # y flipped so the sheet reads the same way up as the 3D view
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" '
        f'height="{h:.2f}mm" viewBox="0 0 {w:.2f} {h:.2f}">\n'
        f'<g transform="translate(0,{h:.2f}) scale(1,-1)" fill="none" '
        f'stroke="#ff0000" stroke-width="0.1">\n{body}\n</g>\n</svg>\n'
    )


def sheet_to_dxf_text(sheet: PackedSheet) -> str:
    """One bed-load as DXF text, sheet boundary on its own SHEET layer."""
    import io

    import ezdxf

    doc = ezdxf.new(setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()

    # no SHEET frame: cutters that run the whole file were cutting it
    deferred: list[tuple[list, str]] = []
    for pl in sheet.placements:
        name = f"L{pl.layer.index:02d}_{pl.layer.role}"
        if name not in doc.layers:
            doc.layers.add(name)
        holes, outlines = _holes_then_outlines(pl.geom)
        for ring in holes:
            msp.add_lwpolyline(list(ring), close=True,
                               dxfattribs={"layer": name})
        deferred.extend((ring, name) for ring in outlines)
        if pl.engrave is not None:
            ename = f"{name}_ENGRAVE"
            if ename not in doc.layers:
                doc.layers.add(ename, color=5)
            for poly in _polys(pl.engrave):
                for ring in _rings(poly):
                    msp.add_lwpolyline(list(ring), close=True,
                                       dxfattribs={"layer": ename})

    # outer edges dead last, after every hole and every engrave pass
    for ring, name in deferred:
        msp.add_lwpolyline(list(ring), close=True,
                           dxfattribs={"layer": name})

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue()


def sheet_manifest(sheets: list[PackedSheet], case: CaseModel) -> str:
    """The paper that goes to the machine with the files."""
    spec = case.spec
    # what to actually pull from the shelf, per size
    counts: dict[tuple[float, float], int] = {}
    for sheet in sheets:
        counts[(sheet.width, sheet.height)] =             counts.get((sheet.width, sheet.height), 0) + 1
    pull = ", ".join(f"{n} x {pw:.0f} x {ph:.0f} mm"
                     for (pw, ph), n in sorted(counts.items(), reverse=True))

    lines = [
        f"cut list -- {len(sheets)} sheet(s), plate stock "
        + " / ".join(f"{pw:.0f} x {ph:.0f}" for pw, ph in spec.plates)
        + f" mm, {spec.sheet_margin:.0f} mm edge margin, "
        f"{spec.sheet_spacing:.0f} mm between parts",
        f"pull from stock: {pull}",
        "red = cut (holes first, outer edges last), blue = engrave "
        "(mark only); no sheet frame is drawn -- the canvas is the bed",
        "",
    ]
    for sheet in sheets:
        lines.append(f"sheet {sheet.index + 1} "
                     f"({sheet.width:.0f} x {sheet.height:.0f} mm):")
        for pl in sheet.placements:
            layer = pl.layer
            x0, y0, x1, y1 = pl.geom.bounds
            lines.append(
                f"  layer {layer.index:02d} {layer.role:<6} "
                f"{layer.material.name:<20} {x1 - x0:6.1f} x {y1 - y0:6.1f} mm"
                f"{'  (rotated 90)' if pl.rotated else ''}"
                f"{'  (MIRRORED: install engraved face down)' if pl.mirrored else ''}")
        lines.append("")
    return "\n".join(lines)


def sheet_files(case: CaseModel, fmt: str, basename: str = "case"
                ) -> list[tuple[str, str]]:
    """(filename, content) per sheet, plus the manifest."""
    if fmt not in ("svg", "dxf"):
        raise ValueError(f"unknown sheet format {fmt!r}")
    sheets = pack_sheets(case)
    n = len(sheets)
    files = []
    for sheet in sheets:
        name = f"{basename}-sheet-{sheet.index + 1:02d}-of-{n:02d}.{fmt}"
        content = (sheet_to_svg(sheet) if fmt == "svg"
                   else sheet_to_dxf_text(sheet))
        files.append((name, content))
    files.append((f"{basename}-cutlist.txt", sheet_manifest(sheets, case)))
    return files


def sheets_zip(case: CaseModel, fmt: str, basename: str = "case") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in sheet_files(case, fmt, basename):
            z.writestr(name, content)
    return buf.getvalue()
