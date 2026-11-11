"""A Eurorack panel made by a PCB service: what goes on which layer.

A panel ordered as a PCB is an FR4 board with four looks on its face:

* **soldermask** everywhere, unless opened -- the panel's colour;
* **silkscreen** printed on the mask -- the lettering;
* **exposed metal** -- copper under an opening in the mask, plated by the
  finish: gold with ENIG, silver with HASL;
* **bare substrate** -- an opening in the mask with no copper under it.

Every decoration element and every band of an artwork names one of them
(`Engraving.layer`, `PanelArt.layers`), and this module turns the lot into the
four things a board house reads: copper, mask openings, silk and the edge.
Panel holes are not plated (NPTH): round ones are drilled, everything else --
the oval rail slots, display windows -- is routed, drawn on the outline layer.

Things a fab would do silently and badly are done here, and said:

* silk is clipped off mask openings and holes (a fab clips it anyway, and
  then the label reads wrong without anyone having been told);
* copper is held 0.3 mm in from every board edge, holes included;
* artwork is kept clear of the holes and of the rail screws' heads.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from . import pcbfab
from .geom import polygons as _polys

__all__ = ["PcbArt", "compose", "fab_notes", "gerber_files", "gerber_zip"]

#: an area this small is rounding, not a problem worth a sentence
TINY = 0.01


@dataclass
class PcbArt:
    """Everything a panel's Gerbers are drawn from, in panel millimetres."""

    #: the board, holes and routed cut-outs taken out
    board: Polygon
    #: (x, y, diameter) of every round hole: NPTH drills
    drills: list[tuple[float, float, float]]
    #: closed contours for the edge-cuts layer: the outline, then every
    #: cut-out that is not a round hole (rail slots, windows)
    routes: list[list[tuple[float, float]]]
    copper: object
    mask_open: object
    substrate: object
    #: silk drawn as filled outlines
    silk_regions: object
    #: silk drawn with a round pen: (centre line, width) -- unclipped labels
    silk_strokes: list[tuple[list[tuple[float, float]], float]] = field(
        default_factory=list)
    #: (level, code, message, refs)
    issues: list[tuple[str, str, str, list[str]]] = field(default_factory=list)

    @property
    def silk(self):
        strokes = [LineString(p).buffer(w / 2, quad_segs=6)
                   for p, w in self.silk_strokes]
        return unary_union([self.silk_regions, *strokes])


def _round_hole(ring) -> tuple[float, float, float] | None:
    """(x, y, d) if this hole is a circle -- a buffered point -- else None."""
    pts = np.asarray(ring.coords)[:-1]
    if len(pts) < 12:
        return None
    c = Polygon(ring).centroid
    r = np.hypot(pts[:, 0] - c.x, pts[:, 1] - c.y)
    if r.max() - r.min() > max(0.02, 0.02 * r.mean()):
        return None
    return (round(c.x, 4), round(c.y, 4), round(2 * r.max(), 2))


def _thin(g, width: float) -> float:
    """Area of the parts of `g` narrower than `width`."""
    if g.is_empty or width <= 0:
        return 0.0
    opened = g.buffer(-width / 2 + 1e-4, quad_segs=4).buffer(width / 2 - 1e-4,
                                                             quad_segs=4)
    return g.difference(opened).area


def compose(res, spec, board, rail_shapes) -> PcbArt:
    """Split the scene's decoration onto the PCB layers.

    `board` is the panel as cut -- outline, holes, through-cuts -- and
    `rail_shapes` the rail slots, which artwork keeps its distance from.
    """
    from .artwork import trace
    from .engrave import build_engraving, text_strokes
    from .schema import Pattern

    pcb = spec.eurorack_panel.pcb
    lim = pcbfab.limits()
    issues: list[tuple[str, str, str, list[str]]] = []
    metal = pcbfab.metal_word(pcb.finish)

    shells = [Polygon(p.exterior) for p in _polys(board)]
    outline = unary_union(shells)
    drills, routes, hole_polys = [], [], []
    for p in _polys(board):
        routes.append(list(p.exterior.coords))
        for ring in p.interiors:
            hole_polys.append(Polygon(ring))
            hole = _round_hole(ring)
            if hole:
                drills.append(hole)
            else:
                routes.append(list(ring.coords))
    holes = unary_union(hole_polys) if hole_polys else Polygon()

    # artwork keeps off the holes and the rail screws' heads; lettering and
    # patterns are placed by hand, so they are only clipped to the board
    art_keep = board.difference(holes.buffer(lim["art_hole_clearance"])) \
        .difference(unary_union([s.buffer(lim["rail_zone"])
                                 for s in rail_shapes]))

    parts = {"silk": [], "copper": [], "substrate": []}
    stroke_text = []                       # (engraving, strokes) for silk text
    scene = res.scene
    for e in scene.engravings if scene else []:
        if e.through:
            continue                       # already cut out of the board
        if e.face == "floor":
            issues.append(("warning", "pcb_back_face",
                           f"{e.name!r} is on the backplate; a PCB panel "
                           f"prints its front only, so it is left off", []))
            continue
        full = build_engraving(e, clip=outline)
        g = build_engraving(e, clip=board)
        if g.is_empty:
            continue
        if e.layer == "silk" and full.area - g.area > TINY:
            issues.append(("warning", "pcb_silk_clipped",
                           f"silk {e.name!r} runs over a hole and is cut "
                           f"short there", []))
        parts[e.layer].append(g)
        is_text = Pattern(e.pattern) is Pattern.text
        if e.layer == "silk":
            if e.stroke < lim["silk_line"]:
                issues.append(("warning", "pcb_silk_line",
                               f"silk {e.name!r} has a {e.stroke:g} mm line; "
                               f"services print {lim['silk_line']:g} mm and "
                               f"up -- it may break up or vanish", []))
            if is_text and e.text_size < lim["silk_text"]:
                issues.append(("warning", "pcb_silk_text",
                               f"silk {e.name!r} is {e.text_size:g} mm tall; "
                               f"under {lim['silk_text']:g} mm lettering "
                               f"fills in", []))
            if is_text and abs(full.area - g.area) <= TINY:
                stroke_text.append((e, g))
        elif e.stroke < lim["copper_feature"] and not e.through:
            issues.append(("warning", "pcb_copper_feature",
                           f"{metal} {e.name!r} has a {e.stroke:g} mm line; "
                           f"under {lim['copper_feature']:g} mm it may not "
                           f"etch", []))

    for art in scene.artwork if scene else []:
        try:
            layers, dropped = trace(art)
        except ValueError as exc:
            issues.append(("error", "pcb_artwork", str(exc), []))
            continue
        for layer, g in layers.items():
            parts[layer].append(g.intersection(art_keep))
        for layer, lost in dropped.items():
            issues.append(("info", "pcb_artwork_detail",
                           f"artwork {art.name!r}: {lost:.1f} mm² of "
                           f"{layer} finer than {art.min_feature:g} mm was "
                           f"dropped", []))
        floor = {"silk": lim["silk_line"], "copper": lim["copper_feature"],
                 "substrate": lim["copper_feature"]}
        worst = max((floor[l] for l in layers), default=0.0)
        if layers and art.min_feature < worst:
            issues.append(("warning", "pcb_artwork_feature",
                           f"artwork {art.name!r} keeps features down to "
                           f"{art.min_feature:g} mm; the fab's minimum is "
                           f"{worst:g} mm", []))

    def union(key):
        return unary_union(parts[key]) if parts[key] else Polygon()

    # copper: held off every edge of the board, holes included
    copper_keep = board.buffer(-lim["copper_edge"], quad_segs=8)
    wanted = union("copper")
    copper = wanted.intersection(copper_keep)
    if wanted.area - copper.area > TINY:
        issues.append(("warning", "pcb_copper_edge",
                       f"{metal} closer than {lim['copper_edge']:g} mm to an "
                       f"edge or hole was pulled back "
                       f"({wanted.area - copper.area:.1f} mm²)", []))
    if pcb.ring > 0:
        if pcb.ring < lim["ring"]:
            issues.append(("warning", "pcb_ring",
                           f"a {pcb.ring:g} mm ring round the holes is under "
                           f"the {lim['ring']:g} mm a fab holds to", []))
        rings = [Point(x, y).buffer(d / 2 + pcb.ring, quad_segs=24)
                 .difference(Point(x, y).buffer(d / 2, quad_segs=24))
                 for x, y, d in drills]
        edge_keep = outline.buffer(-lim["copper_edge"], quad_segs=8)
        copper = unary_union([copper, *[r.intersection(edge_keep)
                                        for r in rings]])

    substrate = union("substrate").intersection(board).difference(copper)
    mask_open = unary_union([copper, substrate])

    silk_all = union("silk")
    silk = silk_all.difference(mask_open)
    if silk_all.area - silk.area > TINY:
        issues.append(("warning", "pcb_silk_clipped",
                       f"{silk_all.area - silk.area:.1f} mm² of silk sits on "
                       f"{metal} or bare board and is left off -- silk is "
                       f"only printed on mask", []))
    thin = _thin(silk, lim["silk_line"])
    if thin > 0.05:
        issues.append(("warning", "pcb_silk_line",
                       f"{thin:.1f} mm² of silk is narrower than "
                       f"{lim['silk_line']:g} mm", []))

    # labels nothing has clipped are drawn with a round pen, the rest filled
    strokes, stroked = [], []
    for e, g in stroke_text:
        if g.intersects(mask_open):
            continue
        strokes += [(s, e.stroke) for s in text_strokes(e)]
        stroked.append(g)
    regions = silk.difference(unary_union(stroked)) if stroked else silk

    if pcb.silk not in pcbfab.palette()["mask"][pcb.mask]["silk"]:
        issues.append(("warning", "pcb_silk_colour",
                       f"{pcb.silk} silk on {pcb.mask} mask is not a "
                       f"combination most services offer", []))

    return PcbArt(board=board, drills=drills, routes=routes, copper=copper,
                  mask_open=mask_open, substrate=substrate,
                  silk_regions=regions, silk_strokes=strokes, issues=issues)


def order_line(spec) -> str:
    """One line for the cut list: what to order instead of a sheet."""
    pcb = spec.eurorack_panel.pcb
    return (f"order as a PCB: {pcb.thickness:g} mm FR4, "
            f"{pcbfab.label('mask', pcb.mask)} mask, "
            f"{pcbfab.label('silk', pcb.silk)} silk, "
            f"{pcbfab.label('finish', pcb.finish)} -- export for PCB fab "
            f"gives the Gerbers")


def fab_notes(spec, art: PcbArt, base: str) -> str:
    """What to type into the order form, and what is in the zip."""
    ep = spec.eurorack_panel
    pcb = ep.pcb
    x0, y0, x1, y1 = art.board.bounds
    has_copper = not art.copper.is_empty
    min_drill = min((d for _x, _y, d in art.drills), default=None)
    lines = [
        f"{base}: Eurorack {ep.format} panel, {ep.hp:g} HP, made as a PCB",
        "",
        "ORDER",
        f"  base material     FR4",
        f"  layers            2 (bottom copper empty)",
        f"  dimensions        {x1 - x0:.2f} x {y1 - y0:.2f} mm",
        f"  thickness         {pcb.thickness:g} mm",
        f"  solder mask       {pcbfab.label('mask', pcb.mask)}",
        f"  silkscreen        {pcbfab.label('silk', pcb.silk)}",
        f"  surface finish    {pcbfab.label('finish', pcb.finish)}"
        + ("" if has_copper else "  (no exposed copper: any finish will do)"),
        f"  copper weight     1 oz",
        f"  via covering      n/a (no vias)",
        f"  edge plating      no",
        f"  remove order number: yes, or 'specify a location' -- a service "
        f"that prints its order number on the board puts it on the front of "
        f"your panel otherwise. Where the service supports it, place its "
        f"marker text (JLCPCB: JLCJLCJLCJLC) on the back.",
        "",
        "FILES",
        f"  {base}.GTL        top copper -- exposed {pcbfab.metal_word(pcb.finish)}",
        f"  {base}.GTS        top solder mask (drawn = opening)",
        f"  {base}.GTO        top silkscreen",
        f"  {base}.GBL        bottom copper (empty)",
        f"  {base}.GBS        bottom solder mask (no openings: back fully masked)",
        f"  {base}.GKO        board outline, plus every non-round cut-out "
        f"(the oval rail slots, windows) as a closed route",
        f"  {base}-NPTH.DRL   non-plated round holes, Excellon, metric",
        "",
        "NOTES",
        f"  {len(art.drills)} round holes, all non-plated"
        + (f", smallest {min_drill:g} mm" if min_drill else ""),
        f"  {len(art.routes) - 1} routed cut-outs on the outline layer; the "
        f"rail slots are 3.2 mm wide, well over any router's minimum",
        "  units mm, Gerber RS-274X with X2 file attributes, 4.6 format",
    ]
    return "\n".join(lines) + "\n"


def preview_json(spec, art: PcbArt) -> dict:
    """For the editor: the colours and the layers, as rings."""
    from .export import _rings

    pcb = spec.eurorack_panel.pcb

    def rings(g):
        if g is None or g.is_empty:
            return []
        return [[list(c) for c in ring] for p in _polys(g) for ring in _rings(p)]

    return {
        "thickness": pcb.thickness,
        "mask": pcb.mask, "silk": pcb.silk, "finish": pcb.finish,
        "colors": {"mask": pcbfab.colour("mask", pcb.mask),
                   "silk": pcbfab.colour("silk", pcb.silk),
                   "metal": pcbfab.colour("finish", pcb.finish),
                   "substrate": pcbfab.palette()["substrate"]["color"]},
        "metal": pcbfab.metal_word(pcb.finish),
        "layers": {"silk": rings(art.silk), "copper": rings(art.copper),
                   "substrate": rings(art.substrate)},
        "drills": [list(d) for d in art.drills],
    }


#: the outline is drawn with this pen; fabs route the centre of the line
OUTLINE_PEN = 0.1


def gerber_files(model, base: str) -> list[tuple[str, str]]:
    """(filename, text) for every file of the fab package, notes included."""
    from .gerber import Gerber, excellon

    art = getattr(model, "pcb", None)
    if art is None:
        raise ValueError("this panel is not made as a PCB -- set "
                         "eurorack_panel.fab: pcb")
    spec = model.spec
    pcb = spec.eurorack_panel.pcb

    top_cu = Gerber("Copper,L1,Top", f"{base} top copper -- exposed "
                    f"{pcbfab.metal_word(pcb.finish)}")
    top_cu.fill(art.copper)
    top_mask = Gerber("Soldermask,Top", f"{base} top solder mask, drawn = "
                      f"opening", polarity="Negative")
    top_mask.fill(art.mask_open)
    silk = Gerber("Legend,Top", f"{base} top silkscreen, "
                  f"{pcbfab.label('silk', pcb.silk)}")
    silk.fill(art.silk_regions)
    for pts, width in art.silk_strokes:
        silk.stroke(pts, width)
    bot_cu = Gerber("Copper,L2,Bot", f"{base} bottom copper, empty")
    bot_mask = Gerber("Soldermask,Bot", f"{base} bottom solder mask, no "
                      f"openings", polarity="Negative")
    edge = Gerber("Profile,NP", f"{base} board outline and routed cut-outs")
    for ring in art.routes:
        edge.stroke(ring, OUTLINE_PEN)

    return [
        (f"{base}.GTL", top_cu.text()),
        (f"{base}.GTS", top_mask.text()),
        (f"{base}.GTO", silk.text()),
        (f"{base}.GBL", bot_cu.text()),
        (f"{base}.GBS", bot_mask.text()),
        (f"{base}.GKO", edge.text()),
        (f"{base}-NPTH.DRL", excellon(art.drills, plated=False,
                                      comment=f"{base} non-plated holes")),
        (f"{base}-fab-notes.txt", fab_notes(spec, art, base)),
    ]


def gerber_zip(model, base: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in gerber_files(model, base):
            z.writestr(name, text)
    return buf.getvalue()
