"""Propose connectors for an imported board from the bodies in its model.

A mesh cannot say where a cable plugs in, which is why an import used to
arrive with no connectors at all -- and a board with none asks the case for
no cable room whatsoever. But a connector is an unusually recognisable body:
a STEMMA QT socket is 4.95 x 6.00 x 2.96 mm on every Adafruit board that has
one, and it sits at an edge with its mouth facing out. So bodies are matched
against a small table of such signatures, and a match becomes a *proposed*
connector, tagged `estimated` so the issues pane keeps asking for it to be
confirmed.

Two rules keep the false positives down:

* **edge only.** Every signature here is a connector that plugs in from the
  side, so it has to sit within `EDGE_GAP` of a board edge; a body in the
  middle of a board is never proposed, however well its size matches. The
  face is the edge it sits at, and the mouth is on that edge.
* **size within tolerance**, both footprint dimensions and the height, the
  footprint either way round.

The product name is a prior: a board called "... STEMMA QT" that has a
STEMMA-sized body near an edge gets it a little more readily, and a match the
name agrees with is noted as such.
"""

from __future__ import annotations

from dataclasses import dataclass

#: how close to an edge a side-entry connector's body sits
EDGE_GAP = 1.5


@dataclass(frozen=True)
class Signature:
    type: str                       # the Connector.type it becomes
    footprint: tuple[float, float]  # either way round
    height: float
    tol: float
    plug_depth: float
    bend_radius: float
    external: bool
    keywords: tuple[str, ...] = ()
    #: where the numbers come from, for the proposal's note
    source: str = ""

    def fits(self, size: tuple[float, float, float], slack: float = 1.0) -> bool:
        a, b, h = size
        t = self.tol * slack
        fa, fb = self.footprint
        flat = ((abs(a - fa) <= t and abs(b - fb) <= t)
                or (abs(a - fb) <= t and abs(b - fa) <= t))
        return flat and abs(h - self.height) <= t


def _header(n: int) -> Signature:
    """A right-angle 0.1 inch header of n pins, plastic body only."""
    return Signature(f"header_1x{n}", (n * 2.54, 2.54), 2.54, 0.35,
                     plug_depth=12.0, bend_radius=10.0, external=False,
                     keywords=("header",),
                     source="0.1 inch pitch, right-angle")


SIGNATURES: tuple[Signature, ...] = (
    # measured off Adafruit's own models (4741, 5752): identical every time
    Signature("stemma_qt", (4.95, 6.00), 2.96, 0.4, plug_depth=8.0,
              bend_radius=10.0, external=False,
              keywords=("stemma qt", "stemma-qt", "qwiic"),
              source="measured, Adafruit CAD"),
    # typical datasheet bodies; tolerances wider to match
    Signature("jst_ph_2", (6.0, 7.6), 4.8, 0.6, plug_depth=10.0,
              bend_radius=12.0, external=False, keywords=("jst", "lipo", "battery"),
              source="JST S2B-PH-K, typical"),
    Signature("jst_ph_3", (8.0, 7.6), 4.8, 0.6, plug_depth=10.0,
              bend_radius=12.0, external=False, keywords=("jst",),
              source="JST S3B-PH-K, typical"),
    Signature("jst_ph_4", (10.0, 7.6), 4.8, 0.6, plug_depth=10.0,
              bend_radius=12.0, external=False, keywords=("jst",),
              source="JST S4B-PH-K, typical"),
    Signature("usb_c", (8.94, 7.35), 3.26, 0.6, plug_depth=20.0,
              bend_radius=15.0, external=True, keywords=("usb c", "usb-c", "type c"),
              source="16-pin USB-C receptacle, typical"),
    Signature("usb_micro_b", (7.5, 5.0), 2.7, 0.6, plug_depth=18.0,
              bend_radius=15.0, external=True, keywords=("micro usb", "microusb", "micro-b"),
              source="micro-B receptacle, typical"),
    Signature("audio_3v5", (12.0, 6.0), 5.0, 0.8, plug_depth=20.0,
              bend_radius=15.0, external=True, keywords=("audio", "3.5mm", "headphone"),
              source="edge-mount 3.5 mm jack (PJ-320 style), typical"),
    _header(2), _header(3), _header(4), _header(5), _header(6),
)

#: outward face for each edge
_FACES = {"left": "-x", "right": "+x", "bottom": "-y", "top": "+y"}


def _edge(body, width: float, depth: float):
    """(edge name, gap) of the board edge nearest this body."""
    gaps = {"left": body.x0, "right": width - body.x1,
            "bottom": body.y0, "top": depth - body.y1}
    edge = min(gaps, key=gaps.get)
    return edge, gaps[edge]


def _mouth(body, edge: str) -> tuple[float, float, float]:
    cx, cy = body.centre
    zc = (body.z0 + body.z1) / 2
    if edge == "left":
        return (body.x0, cy, zc)
    if edge == "right":
        return (body.x1, cy, zc)
    if edge == "bottom":
        return (cx, body.y0, zc)
    return (cx, body.y1, zc)


def propose(bodies, width: float, depth: float, name: str = "") -> list[dict]:
    """Connector entries (as part-YAML dicts) for the bodies that look like
    one. `bodies` are in the part's frame: board's corner at the origin, its
    bottom face at z = 0, as `ingest.draft_part` places them."""
    words = name.lower()
    out: list[dict] = []
    counts: dict[tuple[str, str], int] = {}
    for b in bodies:
        edge, gap = _edge(b, width, depth)
        if gap > EDGE_GAP or gap < -EDGE_GAP:   # overhanging a little is fine
            continue
        size = (b.size[0], b.size[1], b.size[2])
        named = [s for s in SIGNATURES if any(k in words for k in s.keywords)]
        match = next((s for s in named if s.fits(size, slack=1.25)), None) \
            or next((s for s in SIGNATURES if s.fits(size)), None)
        if match is None:
            continue
        n = counts.get((match.type, edge), 0) + 1
        counts[(match.type, edge)] = n
        cname = f"{match.type}_{edge}" + (f"_{n}" if n > 1 else "")
        cx, cy = b.centre
        agrees = any(k in words for k in match.keywords)
        out.append({
            "name": cname,
            "type": match.type,
            "face": _FACES[edge],
            "at": [round(v, 2) for v in _mouth(b, edge)],
            "body": {
                "name": f"{cname}_body", "kind": "body",
                "at": [round(cx, 2), round(cy, 2)],
                "size": [round(b.size[0], 2), round(b.size[1], 2)],
                "z": [round(b.z0, 2), round(b.z1, 2)],
                "src": {"confidence": "estimated"},
            },
            "plug_depth": match.plug_depth,
            "bend_radius": match.bend_radius,
            "external": match.external,
            "src": {"confidence": "estimated",
                    "note": (f"PROPOSED -- a {b.size[0]:.2f} x {b.size[1]:.2f} x "
                             f"{b.size[2]:.2f} body at the {edge} edge matches "
                             f"{match.type} ({match.source})"
                             + ("; the product name agrees" if agrees else "")
                             + ". Confirm it before cutting.")},
        })
    return out
