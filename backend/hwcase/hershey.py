"""Single-stroke lettering, for engraving labels on a panel.

A normal font describes the *outline* of a letter, which a laser then has to
fill — slow, and at 3 mm tall it turns into a blob. A **stroke** font describes
the letter as the path a pen takes down its middle, which is what an engraving
head actually wants: one fast pass, and a 3 mm character that is still legible.

The Hershey fonts are the standard answer and have been since 1967. They are
public domain, they were designed for exactly this, and every plotter and
CNC toolchain since has shipped a copy. Two weights are vendored in
`vendor/fonts/`: `futural` (light sans) for small labels, `futuram` (medium)
for anything that has to be read across a room.

The `.jhf` format is a fixed-width text encoding: five characters of glyph
number, three of vertex count, then coordinate pairs where each character is an
offset from `R`. The first pair is the left and right bearing rather than a
point, and a literal ` R` is pen-up. That is the whole specification.

Output is polylines, in millimetres, which the engraver then strokes to a
width. Nothing here knows about fill, because a stroke font has no inside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

__all__ = ["Glyph", "Font", "load_font", "FONTS", "text_polylines"]

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "fonts"

#: The two weights we ship, by the name a user picks.
FONTS = {
    "light": "futural.jhf",
    "medium": "futuram.jhf",
}

#: The .jhf glyphs are stored in ASCII order starting at space.
FIRST_CHAR = 32

@dataclass
class Glyph:
    """One character: how wide it is, and the strokes that draw it."""

    left: float
    right: float
    strokes: list[list[tuple[float, float]]] = field(default_factory=list)

    @property
    def advance(self) -> float:
        return self.right - self.left


@dataclass
class Font:
    name: str
    glyphs: dict[str, Glyph] = field(default_factory=dict)
    #: Where the capitals actually sit, measured off the font rather than
    #: assumed. The band is not symmetric about zero -- in these fonts it runs
    #: from -9 to +12 -- so centring a label on the origin means centring on
    #: this band, not on the origin the glyph data happens to use.
    baseline: float = -9.0
    cap_top: float = 12.0

    @property
    def cap_height(self) -> float:
        return self.cap_top - self.baseline

    @property
    def cap_middle(self) -> float:
        return (self.cap_top + self.baseline) / 2.0

    def glyph(self, ch: str) -> Optional[Glyph]:
        return self.glyphs.get(ch)

    def measure_caps(self) -> None:
        """Find the cap band from letters that have no curve overshoot.

        H and E are flat top and bottom in every sane typeface, so they give
        the band exactly. O and S bulge a fraction past it by design, and
        using one of those would make every label sit slightly low.
        """
        tops, bottoms = [], []
        for ch in "HEFILT":
            g = self.glyphs.get(ch)
            if not g or not g.strokes:
                continue
            ys = [y for stroke in g.strokes for _, y in stroke]
            tops.append(max(ys))
            bottoms.append(min(ys))
        if tops and bottoms:
            self.cap_top = max(tops)
            self.baseline = min(bottoms)


def _parse_line(line: str) -> Optional[Glyph]:
    """One .jhf record.

    Returns None for a line too short to be a glyph -- some distributions of
    these files carry stray blanks, and losing the whole font to one of them
    would be a poor trade.
    """
    if len(line) < 10:
        return None
    body = line[8:]
    if len(body) < 2:
        return None

    left = ord(body[0]) - ord("R")
    right = ord(body[1]) - ord("R")

    strokes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    i = 2
    while i + 1 < len(body):                   # coordinates come in pairs
        pair = body[i:i + 2]
        i += 2
        if pair == " R":                       # pen up: start a new stroke
            if len(current) > 1:
                strokes.append(current)
            current = []
            continue
        x = ord(pair[0]) - ord("R")
        # y is measured downward in the source, and up in every coordinate
        # system in this project, so it is negated once, here.
        y = -(ord(pair[1]) - ord("R"))
        current.append((float(x), float(y)))

    if len(current) > 1:
        strokes.append(current)
    return Glyph(float(left), float(right), strokes)


@lru_cache(maxsize=8)
def load_font(name: str = "light") -> Font:
    """Load one of the vendored stroke fonts."""
    filename = FONTS.get(name)
    if filename is None:
        raise ValueError(
            f"unknown font {name!r}; have {', '.join(sorted(FONTS))}")

    path = FONT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing -- the Hershey fonts live in vendor/fonts/")

    font = Font(name=name)
    lines = path.read_text(encoding="latin-1").splitlines()
    for index, line in enumerate(lines):
        glyph = _parse_line(line)
        if glyph is None:
            continue
        code = FIRST_CHAR + index
        if code > 126:
            break
        font.glyphs[chr(code)] = glyph
    font.measure_caps()
    return font


def text_polylines(text: str, size: float = 6.0, font: str = "light",
                   tracking: float = 0.0, line_spacing: float = 1.45,
                   align: str = "center") -> list[list[tuple[float, float]]]:
    """Lay out `text` and return its strokes, centred on the origin.

    `size` is cap height in millimetres, because that is the dimension you
    measure on a finished panel -- not em size, which is a typographer's
    abstraction and would make a 6 mm label come out about 4 mm tall.

    Newlines start a new line. Characters the font has no glyph for are
    skipped rather than substituted: a tofu box engraved into a faceplate is
    permanent, and a missing character at least looks like a mistake in the
    label rather than a deliberate mark.
    """
    f = load_font(font)
    scale = size / f.cap_height
    track = tracking / scale if scale else 0.0

    laid: list[list[list[tuple[float, float]]]] = []
    widths: list[float] = []

    for raw_line in str(text).split("\n"):
        cursor = 0.0
        strokes: list[list[tuple[float, float]]] = []
        for ch in raw_line:
            g = f.glyph(ch)
            if g is None:
                g = f.glyph(" ")
                if g is None:
                    continue
            for stroke in g.strokes:
                strokes.append([(x - g.left + cursor, y) for x, y in stroke])
            cursor += g.advance + track
        laid.append(strokes)
        widths.append(cursor - track if raw_line else 0.0)

    if not laid:
        return []

    # Stack the lines and centre the block on the origin, so an engraving's
    # `at` means the middle of the label whatever it says.
    step = f.cap_height * line_spacing
    total_height = step * (len(laid) - 1)
    widest = max(widths) or 1.0

    out: list[list[tuple[float, float]]] = []
    for row, (strokes, width) in enumerate(zip(laid, widths)):
        # Alignment is done on advance widths, which is what a ragged edge is
        # measured by -- the margin, not the ink.
        if align == "left":
            dx = -widest / 2.0
        elif align == "right":
            dx = widest / 2.0 - width
        else:
            dx = -width / 2.0
        dy = total_height / 2.0 - row * step - f.cap_middle
        for stroke in strokes:
            out.append([((x + dx) * scale, (y + dy) * scale)
                        for x, y in stroke])

    # ...but the block is centred on its INK. An advance width carries the
    # last glyph's right sidebearing, so centring on it leaves the label
    # visibly off by a fraction of a millimetre -- and `at` is supposed to be
    # the middle of what you can see, not the middle of the typesetting.
    return _centre_on_ink(out)


def _centre_on_ink(strokes: list[list[tuple[float, float]]]):
    if not strokes:
        return strokes
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return [[(x - cx, y - cy) for x, y in s] for s in strokes]


def measure_text(text: str, size: float = 6.0, font: str = "light",
                 tracking: float = 0.0,
                 line_spacing: float = 1.45) -> tuple[float, float]:
    """How big the label will come out, in millimetres.

    Worth knowing before you commit: a label wider than the panel is only
    discovered late otherwise, and by then it has been clipped.
    """
    strokes = text_polylines(text, size, font, tracking, line_spacing)
    if not strokes:
        return (0.0, 0.0)
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    return (max(xs) - min(xs), max(ys) - min(ys))
