"""Gerber RS-274X and Excellon, written from shapely geometry.

Just enough of each format to make a panel, and nothing a board house's
viewer would have to guess at:

* millimetres, absolute, 4.6 coordinates (`%FSLAX46Y46*%`, `%MOMM*%`);
* filled areas as regions (G36/G37). A region cannot have a hole, so a
  polygon is drawn dark (`%LPD*%`) and each of its holes cleared (`%LPC*%`)
  straight after it. Polygons are drawn largest first, so an island inside
  another polygon's hole is drawn after the hole was cleared, never under it;
* centre lines (lettering, the board outline) drawn with a round aperture;
* X2 file attributes (`%TF.FileFunction...`) so a viewer knows each layer
  without relying on the file extension -- and Protel extensions too, which
  is what JLCPCB and PCBWay match on.

Excellon drill files are metric with decimal coordinates, the form every CAM
tool reads without a format question.
"""

from __future__ import annotations

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from .geom import polygons as _polys

__all__ = ["Gerber", "excellon"]


def _c(v: float) -> str:
    return str(int(round(v * 1_000_000)))


class Gerber:
    """One layer, built up a call at a time; `text()` is the file."""

    def __init__(self, function: str, comment: str, polarity: str = "Positive",
                 generator: str = "hwcase"):
        self._head = [
            f"G04 {comment}*",
            f"%TF.GenerationSoftware,{generator}*%",
            f"%TF.FileFunction,{function}*%",
            f"%TF.FilePolarity,{polarity}*%",
            "%FSLAX46Y46*%",
            "%MOMM*%",
        ]
        self._apertures: dict[float, int] = {}
        self._body: list[str] = []
        self._dark = True            # the header sets %LPD*%
        self._current = None

    def _polarity(self, dark: bool) -> None:
        if self._dark is not dark:
            self._body.append("%LPD*%" if dark else "%LPC*%")
            self._dark = dark

    def _aperture(self, d: float) -> None:
        d = round(float(d), 4)
        if d not in self._apertures:
            self._apertures[d] = 10 + len(self._apertures)
        code = self._apertures[d]
        if self._current != code:
            self._body.append(f"D{code}*")
            self._current = code

    def stroke(self, pts, width: float) -> None:
        """A polyline drawn with a round pen `width` wide."""
        pts = list(pts)
        if len(pts) < 2:
            return
        self._polarity(True)
        self._aperture(width)
        x, y = pts[0][:2]
        self._body.append(f"X{_c(x)}Y{_c(y)}D02*")
        for p in pts[1:]:
            self._body.append(f"X{_c(p[0])}Y{_c(p[1])}D01*")

    def _region(self, coords) -> None:
        coords = list(coords)
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        self._body.append("G36*")
        x, y = coords[0][:2]
        self._body.append(f"X{_c(x)}Y{_c(y)}D02*")
        for p in coords[1:]:
            self._body.append(f"X{_c(p[0])}Y{_c(p[1])}D01*")
        self._body.append("G37*")

    def fill(self, geom) -> None:
        """Every polygon of `geom`, holes cleared."""
        polys = sorted(_polys(geom), key=lambda p: -Polygon(p.exterior).area)
        for p in polys:
            if p.is_empty:
                continue
            p = orient(p)
            self._polarity(True)
            self._region(p.exterior.coords)
            if p.interiors:
                self._polarity(False)
                for ring in p.interiors:
                    self._region(ring.coords)
        self._polarity(True)

    def text(self) -> str:
        head = list(self._head)
        for d, code in self._apertures.items():
            head.append(f"%ADD{code}C,{d:.4f}*%")
        body = ["G01*", "G75*"] if self._body else []
        return "\n".join(head + ["%LPD*%"] + body + self._body + ["M02*"]) + "\n"


def excellon(holes: list[tuple[float, float, float]], plated: bool,
             comment: str = "hwcase") -> str:
    """A drill file: one tool per diameter, metric, decimal coordinates."""
    tools: dict[float, int] = {}
    for _x, _y, d in holes:
        tools.setdefault(round(d, 3), len(tools) + 1)
    kind = "Plated,1,2,PTH" if plated else "NonPlated,1,2,NPTH"
    out = ["M48", f"; DRILL file {comment}", f"; #@! TF.FileFunction,{kind}",
           "; FORMAT={-:-/ absolute / metric / decimal}", "FMAT,2", "METRIC"]
    for d, t in tools.items():
        out.append(f"T{t}C{d:.3f}")
    out += ["%", "G90", "G05"]
    for d, t in tools.items():
        out.append(f"T{t}")
        for x, y, dd in holes:
            if round(dd, 3) == d:
                out.append(f"X{x:.4f}Y{y:.4f}")
    out += ["T0", "M30"]
    return "\n".join(out) + "\n"
