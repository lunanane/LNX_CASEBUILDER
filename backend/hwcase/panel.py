"""A Eurorack module panel: one sheet, with the boards hanging behind it.

The layered pipeline already knows how to make a faceplate -- windows for
screens, holes for shafts and jacks, clearance holes for boards screwed up
against its underside, engraving on its face. A module panel is exactly one
faceplate and nothing else, so this module asks `case._layer_geometry` for
that one slab and adds what only a Eurorack panel has: an outline set by the
format, and rail holes where a rail's screws will find them.

What the format fixes (`docs/eurorack.md` sections 1-3):

* the width, from Doepfer's table for the HP -- never `hp * 5.08 - 0.3`, which
  over-cuts most sizes;
* the height, 128.5 mm for 3U;
* the rail holes, 3.0 mm in from the top and bottom edges, so 122.5 mm apart,
  and oval so the panel can slide along a rail of threaded strip or sliding
  nuts. Two holes up to 10 HP, four from 12 HP.

Coordinates: the panel's lower-left corner is the world origin, x across it,
y up it, and its face is the scene's panel plane. Boards sit below that plane
(behind the panel), and anything further behind it than `module_depth` would
not fit the case it is going into.
"""

from __future__ import annotations

from shapely.geometry import LineString, Polygon, box

from .eurorack import HOLE_INSET, HP, ROW_FORMATS, panel_width
from .schema import Material, VolumeKind

#: Horizontal centre of the rail holes, from the panel's left edge. Doepfer
#: draws this in the A-100 construction sketch rather than stating it in the
#: text, so it is `community` until somebody measures a Doepfer panel
#: (plan assumption A-5).
HOLE_X = 7.5

#: The oval rail hole: an M3 clearance high, twice that wide, so the panel
#: can be nudged along the rail (`docs/eurorack.md` section 3).
SLOT_H = 3.2
SLOT_W = 6.4

#: From this width up, a panel takes a second column of rail holes.
FOUR_HOLES_FROM_HP = 12


def outline(hp: float, fmt: str = "3U") -> Polygon:
    """The panel, lower-left corner at the origin."""
    row = _format(fmt)
    return box(0.0, 0.0, panel_width(hp), row.panel)


def _format(fmt: str):
    row = ROW_FORMATS.get(fmt)
    if row is None:
        raise ValueError(f"unknown panel format {fmt!r}; known: "
                         f"{', '.join(sorted(ROW_FORMATS))}")
    if row.u != 3.0:
        # 1U tiles are two incompatible standards (docs/eurorack.md section 7)
        # and neither's rail-hole position is recorded here yet
        raise ValueError(f"{fmt} panels are not supported yet: only 3U has "
                         f"its rail-hole positions recorded")
    return row


def hole_columns(hp: float) -> list[float]:
    """x of each column of rail holes.

    One column at 7.5 mm; from 12 HP a second one a whole number of HP
    further along -- Doepfer's rule is that the spacing is a multiple of the
    grid -- three HP short of the panel's width, which leaves both columns the
    same distance from their edge. A panel too narrow for 7.5 mm (1 and 1.5
    HP) gets its one column on the centre line.
    """
    width = panel_width(hp)
    if width < 2 * HOLE_X:
        return [width / 2]
    if hp < FOUR_HOLES_FROM_HP:
        return [HOLE_X]
    return [HOLE_X, HOLE_X + (int(hp) - 3) * HP]


def rail_holes(hp: float, fmt: str = "3U") -> list[tuple[float, float, Polygon]]:
    """(x, y, shape) for every rail hole: ovals, round on a panel too narrow
    to take one."""
    row = _format(fmt)
    width = panel_width(hp)
    long_way = min(SLOT_W, width - 2.0)          # leave a millimetre each side
    out = []
    for x in hole_columns(hp):
        for y in (HOLE_INSET, row.panel - HOLE_INSET):
            half = max(0.0, (long_way - SLOT_H) / 2)
            shape = LineString([(x - half, y), (x + half, y)]).buffer(
                SLOT_H / 2, quad_segs=12) if half > 0 else \
                LineString([(x, y), (x, y)]).buffer(SLOT_H / 2, quad_segs=12)
            out.append((x, y, shape))
    return out


def panel_plane(res, spec) -> float:
    """The panel's face: the scene's panel plane, or -- with none declared --
    the top of the hardware plus the ceiling gap, as a layered case does."""
    if res.panels:
        return max(res.panels.values())
    _x0, _y0, _z0, _x1, _y1, z1 = res.bounds()
    return z1 + spec.ceiling_gap


def build_panel(res, spec):
    """The panel as a one-layer case model, ready for the usual exports."""
    from .case import CaseModel, Layer, _apply_engravings, _layer_geometry

    ep = spec.eurorack_panel
    outer = outline(ep.hp, ep.format)
    z1 = panel_plane(res, spec)
    z0 = z1 - ep.thickness
    kerf = spec.materials[0].kerf if spec.materials else 0.15
    mat = Material(name=ep.material, thickness=ep.thickness, kerf=kerf,
                   color="#c9ccd1")

    geom, notes = _layer_geometry(res, spec, outer, (z0, z1), "lid",
                                  (z0, z1), [(z0, z1)])
    holes = rail_holes(ep.hp, ep.format)
    for _x, _y, shape in holes:
        geom = geom.difference(shape)
    notes.append(f"{panel_width(ep.hp):.2f} x {outer.bounds[3]:.2f} mm "
                 f"{ep.format} panel, {ep.hp:g} HP, {len(holes)} rail holes "
                 f"({SLOT_W:g} x {SLOT_H:g} oval)")
    notes += standoff_notes(res, z0)

    layer = Layer(0, z0, z1, "lid", mat, geom, notes)
    _apply_engravings(res, [layer])
    return CaseModel(spec=spec, outer=outer, layers=[layer], z0=z0, z1=z1)


def standoff_notes(res, panel_back: float) -> list[str]:
    """One line per board screwed up against the panel: the standoff that
    holds it there, from the panel's back face to the board's top."""
    out = []
    for s in res.supports:
        if s.mode.value != "from_lid":
            continue
        gap = panel_back - s.board_top
        if gap < 0.05:
            out.append(f"{s.placement}: screwed straight to the panel "
                       f"({s.screw or 'screw'}), no standoff")
        else:
            out.append(f"{s.placement}: standoff {gap:.1f} mm "
                       f"({s.screw or 'screw'}) at ({s.at[0]:.1f}, "
                       f"{s.at[1]:.1f})")
    return out


def depth_issues(res, spec) -> list[tuple[str, str, list[str]]]:
    """(code, message, refs) for everything reaching deeper behind the panel
    than the module depth allows -- it would not go into the case."""
    ep = spec.eurorack_panel
    limit = panel_plane(res, spec) - ep.module_depth
    worst: dict[str, float] = {}
    for s in res.solids:
        if s.kind in (VolumeKind.display, VolumeKind.actuator):
            continue
        if s.z[0] < limit - 1e-6:
            worst[s.placement] = min(worst.get(s.placement, s.z[0]), s.z[0])
    return [("module_depth",
             f"{pl} reaches {limit - z:.1f} mm deeper than the "
             f"{ep.module_depth:g} mm module depth behind the panel",
             [pl]) for pl, z in sorted(worst.items())] + rail_issues(res, spec)


#: Room for a board between the rails behind a 3U panel (docs/eurorack.md
#: section 5, `community`): the panel less about 9 mm at the top and the
#: bottom, where the rails and their screws are.
PCB_HEIGHT_3U = 110.0


def rail_band(fmt: str = "3U") -> float:
    """How far in from the top and the bottom edge the rails reach."""
    return (_format(fmt).panel - PCB_HEIGHT_3U) / 2


def rail_issues(res, spec) -> list[tuple[str, str, list[str]]]:
    """(code, message, refs) for every board or part behind the panel that
    reaches into the rail band at its top or bottom: it hits the rail."""
    ep = spec.eurorack_panel
    height = _format(ep.format).panel
    band = rail_band(ep.format)
    back = panel_plane(res, spec) - ep.thickness
    hits: dict[str, list[float]] = {}
    for s in res.solids:
        if s.kind in (VolumeKind.display, VolumeKind.actuator):
            continue
        if s.z[0] >= back - 1e-6:
            continue                       # on or in front of the panel
        _x0, y0, _x1, y1 = s.poly.bounds
        over = max(band - y0, y1 - (height - band))
        if over > 1e-6:
            worst = hits.setdefault(s.placement, [0.0])
            worst[0] = max(worst[0], over)
    return [("rail_clearance",
             f"{pl} reaches {over[0]:.1f} mm into the rails: behind a "
             f"{ep.format} panel a board has about {PCB_HEIGHT_3U:g} mm "
             f"between them, {band:.2f} mm in from the top and the bottom",
             [pl]) for pl, over in sorted(hits.items())]
