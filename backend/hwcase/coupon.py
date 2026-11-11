"""A test coupon: the small job a maker cuts before the real one.

Kerf and joint clearance are the two numbers nobody knows until the sheet is
on the bed (D-2): the kerf depends on the laser, the lens and the focus, the
clearance on the sheet and on taste. Getting them wrong costs a whole case.
So each material gets a coupon cut at the values the scene holds right now:

* **A** -- a plate with a 20.00 mm square through-cut, an M3 clearance hole,
  and the slots and bolt holes of a captive-nut T-slot along one edge;
* **B** -- the plate that stands in those slots, its tabs and T-shaped nut
  trap cut by the same `joinery.tslot_joint` a real case uses;
* **C** and **D** -- a finger-joint pair from `joinery.finger_joint`.

Every piece is engraved with the kerf and clearance it was cut at, so a pile
of coupons from three attempts can still be told apart. The cut list says
what to measure and which way to move each number.
"""

from __future__ import annotations

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from . import joinery as jn
from .fasteners import fastener as _fastener
from .flat import Edge, FlatPart, Labels
from .schema import Material

#: The square through-cut: the one dimension the coupon exists to measure.
SQUARE = 20.0
#: Cap height of the engraved labels.
LABEL_SIZE = 2.5


def _label(text: str, at: tuple[float, float], size: float = LABEL_SIZE,
           upright: bool = False):
    """Stroked single-line lettering centred on `at`, as an engrave pass;
    `upright` runs it up the part instead of across, for a narrow one."""
    from shapely.affinity import rotate, translate

    from .hershey import text_polylines

    strokes = [LineString(s).buffer(0.15, cap_style=1, quad_segs=6)
               for s in text_polylines(text, size=size) if len(s) >= 2]
    if not strokes:
        return None
    ink = unary_union(strokes)
    if upright:
        ink = rotate(ink, 90, origin=(0, 0))
    return translate(ink, *at)


def _values(m: Material) -> str:
    return f"k{m.kerf:.2f} c{m.joint_clearance:.2f}"


def coupon_parts(material: Material, index: int = 0,
                 fastener: str = "M3") -> list[FlatPart]:
    """The four pieces of one material's coupon, in their own frames."""
    f = _fastener(fastener)
    t = material.thickness
    c = material.joint_clearance
    L = max(50.0, 10.0 * t)            # joint length: room for a T-slot
    tag = f"coupon-{index + 1}"
    vals = _values(material)

    # -- the T-slot pair: A carries the slots, B stands in them ------------
    a0 = box(0.0, -12.0, L, t + 40.0)
    b0 = box(0.0, 0.0, 30.0, L)
    edge = jn.Edge(jn.Segment((0.0, 0.0), (L, 0.0), "left"),
                   jn.Segment((30.0, 0.0), (30.0, L), "left"))
    pa, pb, tj = jn.tslot_joint(edge, jn.Plate(a0, material),
                                jn.Plate(b0, material), f)
    bolt = tj.bolts[0].length if tj.bolts else 0.0

    # the square and the screw hole sit clear of the joint band
    square = box(6.0, t + 6.0, 6.0 + SQUARE, t + 6.0 + SQUARE)
    screw = Point(L - 10.0, t + 16.0).buffer(f.clearance_d / 2, quad_segs=24)
    a_geom = pa.geom.difference(square).difference(screw)

    # -- the finger pair ---------------------------------------------------
    c0 = box(0.0, 0.0, L, 25.0)
    d0 = box(0.0, 0.0, 25.0, L)
    fedge = jn.Edge(jn.Segment((0.0, 0.0), (L, 0.0), "left"),
                    jn.Segment((25.0, 0.0), (25.0, L), "left"))
    pc, pd, _fj = jn.finger_joint(fedge, jn.Plate(c0, material),
                                  jn.Plate(d0, material))

    def part(name: str, geom, engrave, i: int, notes: list[str]) -> FlatPart:
        full = f"{tag}{name}"
        x0, y0, x1, _y1 = geom.bounds
        return FlatPart(
            name=full, material=material, outline=geom, role="coupon",
            index=index * 4 + i,
            labels=Labels.plain(full, "coupon", material),
            engrave=engrave, notes=notes,
            datum=Edge((x0, y0), (x1, y0), "bottom"))

    measured = (f"measure the square cut-out: it should be "
                f"{SQUARE:.2f} mm. The kerf that is right is "
                f"{material.kerf:.2f} + (measured - {SQUARE:.2f}); a square "
                f"of {SQUARE + 0.1:.2f} means a kerf of "
                f"{material.kerf + 0.1:.2f}")
    return [
        part("A", a_geom,
             _label(f"{tag}A {vals}", (L / 2, t + 34.0)), 0,
             [measured,
              f"{f.name} hole is {f.clearance_d:g} mm: an {f.name} bolt "
              f"should drop through it",
              f"push {tag}B's tabs through the slots and bolt it with an "
              f"{f.name} x {bolt:g} and a hex nut in the T-slot"]),
        part("B", pb.geom, _label(f"{tag}B", (7.0, L / 2), upright=True), 1,
             [f"tabs cut for a {c:.2f} mm clearance: tight, raise the "
              f"material's joint clearance; loose, lower it",
              f"the nut ({f.name}, {f.nut_af:g} mm across flats) must "
              f"slide into the cross-slot without forcing"]),
        part("C", pc.geom, _label(f"{tag}C {vals}", (L / 2, 15.0)), 2,
             [f"finger joint with {tag}D at {c:.2f} mm clearance: it should "
              f"push together by hand and stay"]),
        part("D", pd.geom, _label(f"{tag}D", (7.0, L / 2), upright=True), 3, []),
    ]


def coupon_files(materials: list[Material], fmt: str, stock,
                 basename: str = "coupon", fastener: str = "M3"
                 ) -> list[tuple[str, str]]:
    """(filename, content) per sheet of coupons, plus their cut list.

    One coupon per distinct material (by name, thickness, kerf and
    clearance), packed onto the scene's own plates like any other job.
    """
    from .export import _sheet_texts, pack_sheets, sheet_manifest

    if fmt not in ("svg", "dxf"):
        raise ValueError(f"unknown sheet format {fmt!r}")
    seen: list[Material] = []
    for m in materials:
        key = (m.name, m.thickness, m.kerf, m.joint_clearance)
        if key not in [(s.name, s.thickness, s.kerf, s.joint_clearance)
                       for s in seen]:
            seen.append(m)
    if not seen:
        raise ValueError("no materials to cut a coupon from")
    parts: list[FlatPart] = []
    for i, m in enumerate(seen):
        try:
            parts += coupon_parts(m, i, fastener)
        except jn.JointError as exc:
            raise ValueError(f"no coupon for {m.name}: {exc}") from None
    sheets = pack_sheets(parts, stock=stock)
    files = _sheet_texts(sheets, fmt, basename)
    head = ["test coupon -- cut this first, at the values the scene holds:"]
    for i, m in enumerate(seen):
        head.append(f"  coupon-{i + 1}: {m.name}, {m.thickness:g} mm, "
                    f"kerf {m.kerf:.2f} mm, joint clearance "
                    f"{m.joint_clearance:.2f} mm")
    head.append("")
    files.append((f"{basename}-cutlist.txt",
                  "\n".join(head) + "\n" + sheet_manifest(sheets, stock)))
    return files
