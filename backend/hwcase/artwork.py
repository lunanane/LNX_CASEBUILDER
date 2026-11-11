"""Raster artwork on a PCB panel: brightness bands traced into polygons.

A board house makes three looks on the front of a panel -- soldermask, the
silkscreen printed on it, and metal where the mask is opened over copper --
plus a fourth, bare FR4, where the mask is opened with no copper under it.
Any graphic can be split into those by brightness: one threshold makes two
bands, two make three, and each band is assigned a layer (dark to mask,
mid to silk, bright to gold, say).

What the scene stores (`schema.PanelArt`) is the greyscale the editor sampled
the upload down to -- at most 512 x 512, zlib'd and base64'd, a few tens of
kilobytes for a typical graphic -- rather than the original file or the
traced outlines. Thresholds and the layer of each band stay editable, the
scene stays inside the API's size limit, and the outlines are traced here,
at build time, from that one source.

Tracing is marching squares on the pixel-centre grid with the crossing
interpolated along each cell edge, so an outline follows the anti-aliased
edge of a shape rather than its pixel staircase. The closed contours are then
nested by containment: even depth is an outline, odd a hole. numpy and
shapely only -- nothing here needs an image library, because the browser has
already decoded the file.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from functools import lru_cache

import numpy as np
import shapely
from shapely.affinity import affine_transform
from shapely.geometry import MultiLineString, Polygon, box
from shapely.ops import unary_union

from .schema import ART_MAX_PIXELS, PanelArt

__all__ = ["decode", "trace", "art_box"]


def decode(art: PanelArt) -> np.ndarray:
    """The stored greyscale, as an (h, w) float array, 0 dark to 255 bright."""
    w, h = art.pixels
    data = art.data
    try:
        if data.startswith("z64:"):
            raw = zlib.decompress(base64.b64decode(data[4:], validate=True),
                                  bufsize=ART_MAX_PIXELS + 1)
        elif data.startswith("b64:"):
            raw = base64.b64decode(data[4:], validate=True)
        else:
            raise ValueError("artwork data starts z64: or b64:")
    except (binascii.Error, zlib.error) as exc:
        raise ValueError(f"artwork {art.name!r}: the image data is "
                         f"damaged ({exc})") from None
    if len(raw) != w * h:
        raise ValueError(f"artwork {art.name!r}: {len(raw)} bytes of image "
                         f"for {w} x {h} pixels")
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float64)


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """A separable Gaussian, edges extended."""
    if sigma < 0.3:
        return img
    r = max(1, int(round(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = img
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        n = out.shape[axis]
        for i, wgt in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(i, i + n)
            acc += wgt * p[tuple(sl)]
        out = acc
    return out


def art_box(art: PanelArt):
    """Where the image sits on the panel."""
    cx, cy = art.at
    return box(cx - art.width / 2, cy - art.height / 2,
               cx + art.width / 2, cy + art.height / 2)


def _above(field: np.ndarray, t: float):
    """Where `field >= t`, in pixel-centre units (x = column, y = row), as
    polygons. The field is padded with a dark border so every contour closes.
    """
    f = np.pad(field, 1, mode="constant", constant_values=-1e3)
    # no node exactly on the level, so every crossing is strictly inside its
    # edge and two contours can never share a vertex
    f = np.where(f == t, t + 1e-3, f)
    inside = f >= t
    if not inside.any():
        return Polygon()

    rows, cols = f.shape
    jj, ii = np.meshgrid(np.arange(cols, dtype=float),
                         np.arange(rows, dtype=float))
    with np.errstate(divide="ignore", invalid="ignore"):
        # crossing on the edge from node (i, j) to (i, j+1), and to (i+1, j)
        fh = (t - f[:, :-1]) / (f[:, 1:] - f[:, :-1])
        fv = (t - f[:-1, :]) / (f[1:, :] - f[:-1, :])
    hx, hy = jj[:, :-1] + np.nan_to_num(fh), ii[:, :-1]
    vx, vy = jj[:-1, :], ii[:-1, :] + np.nan_to_num(fv)

    tl, tr = inside[:-1, :-1], inside[:-1, 1:]
    bl, br = inside[1:, :-1], inside[1:, 1:]
    case = tl * 1 + tr * 2 + br * 4 + bl * 8
    centre = (f[:-1, :-1] + f[:-1, 1:] + f[1:, :-1] + f[1:, 1:]) / 4 >= t

    # the four edge crossings of every cell, as (x, y)
    edge = {
        "T": (hx[:-1, :], hy[:-1, :]),
        "B": (hx[1:, :], hy[1:, :]),
        "L": (vx[:, :-1], vy[:, :-1]),
        "R": (vx[:, 1:], vy[:, 1:]),
    }
    table = {1: ["LT"], 2: ["TR"], 3: ["LR"], 4: ["RB"], 6: ["TB"],
             7: ["LB"], 8: ["BL"], 9: ["TB"], 11: ["RB"], 12: ["LR"],
             13: ["TR"], 14: ["LT"]}
    segs = []

    def emit(mask, pair):
        a, b = edge[pair[0]], edge[pair[1]]
        segs.append(np.stack([np.stack([a[0][mask], a[1][mask]], -1),
                              np.stack([b[0][mask], b[1][mask]], -1)], 1))

    for c, pairs in table.items():
        m = case == c
        if m.any():
            for pr in pairs:
                emit(m, pr)
    # saddles: the centre decides which corners the inside joins
    for c, joined, split in ((5, ("TR", "LB"), ("LT", "RB")),
                             (10, ("LT", "RB"), ("TR", "LB"))):
        for pairs, m in ((joined, (case == c) & centre),
                         (split, (case == c) & ~centre)):
            if m.any():
                for pr in pairs:
                    emit(m, pr)
    if not segs:
        return Polygon()
    seg = np.concatenate(segs)
    seg = np.round(seg, 6)

    merged = shapely.line_merge(MultiLineString(list(seg)))
    lines = list(getattr(merged, "geoms", [merged]))
    rings = [Polygon(ln.coords) for ln in lines
             if ln.is_ring and len(ln.coords) >= 4]
    rings = [r if r.is_valid else r.buffer(0) for r in rings]
    rings = [r for r in rings if not r.is_empty and r.geom_type == "Polygon"]
    if not rings:
        return Polygon()

    # nest: even depth an outline, odd a hole in its direct parent
    tree = shapely.STRtree(rings)
    pairs = tree.query(rings, predicate="within")
    pairs = pairs[:, pairs[0] != pairs[1]]
    depth = np.bincount(pairs[0], minlength=len(rings))
    shells = {k: [] for k in range(len(rings)) if depth[k] % 2 == 0}
    for child, parent in zip(*pairs):
        if depth[child] % 2 == 1 and depth[parent] == depth[child] - 1:
            shells[parent].append(rings[child].exterior.coords)
    polys = [Polygon(rings[k].exterior.coords, holes) for k, holes in shells.items()]
    return unary_union([p if p.is_valid else p.buffer(0) for p in polys])


def _opening(g, r: float):
    """Drop anything thinner than 2r -- the fab's minimum feature."""
    if r <= 0 or g.is_empty:
        return g
    return g.buffer(-r, quad_segs=4).buffer(r, quad_segs=4)


@lru_cache(maxsize=32)
def _trace_cached(key: str):
    art = PanelArt.model_validate(json.loads(key))
    img = decode(art)
    if art.invert:
        img = 255.0 - img
    h_px, w_px = img.shape
    px = art.width / w_px                      # mm per pixel
    img = _blur(img, art.blur / px)

    frame = art_box(art)
    x0, _y0, _x1, y1 = frame.bounds
    # pixel-centre units (padded by one) to panel millimetres; rows run down
    to_mm = [px, 0, 0, -px, x0 + (-1 + 0.5) * px, y1 - (-1 + 0.5) * px]

    # simplified before the bands are cut from each other, so neighbouring
    # bands share their edge exactly and never overlap
    above = [affine_transform(_above(img, t), to_mm).simplify(px * 0.25)
             .intersection(frame) for t in art.thresholds]
    bands = []
    for k in range(len(art.layers)):
        g = frame if k == 0 else above[k - 1]
        if k < len(above):
            g = g.difference(above[k])
        bands.append(g)

    out: dict[str, object] = {}
    dropped: dict[str, float] = {}
    for layer, g in zip(art.layers, bands):
        if layer == "mask" or g.is_empty:
            continue
        clean = _opening(g, art.min_feature / 2)
        lost = g.area - clean.area
        if lost > max(0.01, 0.002 * g.area):
            dropped[layer] = dropped.get(layer, 0.0) + lost
        out[layer] = unary_union([out[layer], clean]) if layer in out else clean
    return out, dropped


def trace(art: PanelArt) -> tuple[dict, dict]:
    """({layer: geometry}, {layer: mm² dropped as thinner than min_feature})
    for one artwork, in panel coordinates, not yet clipped to the panel."""
    return _trace_cached(json.dumps(art.model_dump(mode="json"), sort_keys=True))
