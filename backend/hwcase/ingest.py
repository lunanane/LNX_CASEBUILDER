"""Turn a catalogue hit into a measured part.

The catalogue can tell you a product exists; this fetches the vendor model and
reads a part definition off it, so adding a board is "search, click, it is in
the palette" rather than an afternoon with calipers and a text editor.

What comes out is a *draft*, and it says so in its own provenance. The
measurements are real -- taken from the vendor's own model by
`hwcase.measure.bodies` -- but three things are still guesses that only a human
or a datasheet can settle:

* **which way up.** A model is just bodies in space; nothing in it says "this
  face is the front". Two rules decide it, and both are guesses that happen to
  hold: one large flat body alone on a face is a display module and that face
  goes up; otherwise the face the model stands furthest proud of goes up,
  because knobs, sockets and switches are tall and a solder side is flat.
  Whichever fires is written into the part's notes so it can be argued with.
* **what the volumes are.** We know a 4.95 x 6.00 x 2.96 box sits at the left
  edge. We do not know it is a STEMMA QT socket, and a case cares about the
  difference, because a socket needs a cable route and a capacitor does not.
* **connectors.** Not derivable from a mesh at all. A draft has none, so a
  freshly imported board asks the case for no cable room whatsoever until
  somebody adds them.

None of that makes the draft useless -- an accurate envelope with real
mounting holes is most of the work -- but it does mean an import is a starting
point to check, not an answer, and the generated YAML is written to say so.
"""

from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .catalog import USER_AGENT, CadFile, CatalogEntry
from .measure import (Body, HoleGroup, board_footprint, bodies, hole_groups,
                      step_holes)

__all__ = ["CadFile", "CadTooLarge", "Draft", "cad_files", "download_cad",
           "draft_part", "max_cad_bytes", "GENERATED"]

#: Vendor CAD lands here: one folder per product, named by its ID. A product's
#: second CAD folder (six IDs have one) lands under its full repo folder name
#: instead, so two revisions of a board never share a directory and get their
#: files mixed.
CAD_ROOT = Path(__file__).resolve().parent.parent.parent / "vendor" / "cad" / "adafruit"

#: The download cap, in MB, unless HWCASE_MAX_CAD_MB says otherwise. The
#: largest model in the repo is 55 MB and the average 1.7 MB, so 64 lets every
#: real board through and stops one import from pulling a gigabyte into a
#: hosted worker because somebody pushed a scan.
MAX_CAD_MB = 64.0


class CadTooLarge(ValueError):
    """A vendor CAD file is over the download cap."""

#: Imported parts go in their own file, so a human-written library is never
#: rewritten by a machine and a bad import is one file to delete.
GENERATED = Path(__file__).resolve().parent.parent / "parts" / "imported.yaml"

KEEP = (".stl", ".step", ".stp")

#: Below this footprint a body is an SMD component, not a feature to cut
#: around. They are aggregated into one keepout per face -- the 4741 has 225 of
#: them and 225 volumes would say nothing 1 keepout does not.
COMPONENT_FOOTPRINT = 60.0


def _get(url: str, timeout: float = 60.0,
         limit: Optional[int] = None) -> bytes:
    """GET a URL; with `limit`, read at most one byte past it.

    The one extra byte is how an over-size body is told apart from one exactly
    at the cap, without ever holding more than the cap in memory.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if limit is None else r.read(limit + 1)


def max_cad_bytes() -> int:
    """The download cap in bytes, read from HWCASE_MAX_CAD_MB on every call.

    Read per call rather than at import, so a test or an operator changing the
    variable is obeyed without a restart. A value that is not a positive number
    is refused by name -- silently falling back to the default would leave
    somebody believing their cap was in force.
    """
    raw = os.environ.get("HWCASE_MAX_CAD_MB", "").strip()
    if not raw:
        return int(MAX_CAD_MB * 1024 * 1024)
    try:
        mb = float(raw)
    except ValueError:
        mb = 0.0
    if not mb > 0:
        raise ValueError(f"HWCASE_MAX_CAD_MB={raw!r} is not a positive number "
                         f"of megabytes")
    return int(mb * 1024 * 1024)


def _too_large(f: CadFile, cap: int, size: Optional[int] = None) -> CadTooLarge:
    """The error for an over-size file: which file, and which cap.

    `size` is the indexed size when known; when the body itself ran past the
    cap, only "more than" is known, because the rest was never read.
    """
    how = (f"is {size / 2**20:.1f} MB" if size is not None
           else "came down larger than its indexed size, and is")
    return CadTooLarge(
        f"{f.folder}/{f.name} {how} over the {cap / 2**20:.3g} MB download cap "
        f"(HWCASE_MAX_CAD_MB)")


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

def cad_files(entry: CatalogEntry, folder: Optional[str] = None) -> list[CadFile]:
    """The model files in one of a product's CAD folders, from the index.

    No network: the catalogue's git-tree index already lists every file with
    its size, so an import no longer spends an API request -- of sixty an hour
    -- asking GitHub what is in a folder.
    """
    folder = folder or entry.cad
    if not folder:
        raise ValueError(f"{entry.id}: no vendor CAD published")
    if folder not in entry.cad_files:
        raise ValueError(f"{entry.id}: CAD folder {folder!r} is not in the "
                         f"catalogue's index -- refresh the catalogue")
    return [f for f in entry.cad_files[folder] if f.suffix in KEEP]


def cad_dir(entry: CatalogEntry, folder: Optional[str] = None,
            root: Path = CAD_ROOT) -> Path:
    """Where one of a product's CAD folders lives on disk.

    The folder an import uses goes under the bare product ID, which is where
    `local_cad` and the files already in this repo expect it. Any other folder
    for the same ID goes under its own repo name, so it never mixes with it.
    """
    folder = folder or entry.cad
    if folder and folder != entry.cad:
        return Path(root) / folder
    return Path(root) / entry.id


def download_cad(entry: CatalogEntry, dest_root: Path = CAD_ROOT,
                 overwrite: bool = False,
                 folder: Optional[str] = None) -> list[Path]:
    """Mirror one of a product's CAD folders locally, returning what is on disk.

    The file list and sizes come from the catalogue's index, and the files
    from the raw host at the commit that index was built from. Every size is
    checked against the cap before anything is fetched, so an over-size folder
    fails whole instead of leaving half of itself on disk; the bytes are
    checked again as they arrive, because an index can be wrong.

    Files already present are left alone: the vendor repo does not churn, and
    re-downloading eight megabytes to re-measure the same board is rude to
    somebody else's bandwidth.
    """
    files = cad_files(entry, folder)
    cap = max_cad_bytes()
    for f in files:
        if f.size > cap:
            raise _too_large(f, cap, f.size)

    dest = cad_dir(entry, folder, dest_root)
    dest.mkdir(parents=True, exist_ok=True)
    got = []
    for f in files:
        target = dest / Path(f.name).name
        if overwrite or not target.exists():
            data = _get(f.url, timeout=180, limit=cap)
            if len(data) > cap:
                raise _too_large(f, cap)
            target.write_bytes(data)
        got.append(target)
    return got


def local_cad(product_id: str, root: Path = CAD_ROOT) -> list[Path]:
    """CAD already on disk for a product, however it got there."""
    root = Path(root)
    hits: list[Path] = []
    for pattern in (f"{product_id}", f"{product_id} *"):
        folder = root / pattern
        if folder.is_dir():
            hits += [p for p in sorted(folder.iterdir())
                     if p.suffix.lower() in KEEP]
    if not hits:
        for folder in sorted(root.glob(f"{product_id}*")):
            if folder.is_dir():
                hits += [p for p in sorted(folder.iterdir())
                         if p.suffix.lower() in KEEP]
    return hits


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------

def slug(text: str) -> str:
    """A part id from a product name: lowercase, hyphenated, no punctuation."""
    text = text.lower()
    text = text.replace('"', "in").replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")[:60] or "part"


@dataclass
class Draft:
    """A part definition read off a vendor model, plus what to check."""

    part: dict
    source: str
    warnings: list[str] = field(default_factory=list)
    flipped: bool = False
    bodies: int = 0


def _pick_pcb(found: list[Body]) -> Optional[int]:
    """Which body is the board.

    Broadest thing that is board-thick. Getting this wrong moves the datum
    every mount height in the case is measured from, so it is worth being
    fussy: the 1.5" OLED's display module is nearly as broad as its PCB, and
    only the thickness tells them apart.
    """
    if not found:
        return None
    board_like = [i for i, b in enumerate(found) if 0.6 <= b.height <= 2.6]
    if board_like:
        return max(board_like, key=lambda i: found[i].footprint)
    return max(range(len(found)), key=lambda i: found[i].footprint)


def _should_flip(found: list[Body], pcb: Body) -> tuple[bool, str]:
    """Decide which face points up.

    Nothing in a mesh says "front". The rule that works: a board is normally
    mounted components-down, so the face carrying the crowd of small parts goes
    underneath. When one large flat body sits alone on the other face, that is
    a display or a shield and it is the thing that should face the world.
    """
    above = [b for b in found if b.z0 >= pcb.z1 - 1e-6 and b is not pcb]
    below = [b for b in found if b.z1 <= pcb.z0 + 1e-6 and b is not pcb]

    big_below = [b for b in below if b.footprint > 0.4 * pcb.footprint]
    big_above = [b for b in above if b.footprint > 0.4 * pcb.footprint]

    if big_below and not big_above:
        return True, (f"flipped: a large flat body ({big_below[0].size[0]:.1f} x "
                      f"{big_below[0].size[1]:.1f}) sits alone under the board, "
                      f"which is what a display module looks like")

    # Counting bodies per face misses the through-hole parts that matter most:
    # a rotary encoder straddles the PCB, so it is neither above nor below, and
    # the board reads as symmetric while its knobs point the wrong way. Reach
    # instead: whichever face the model stands furthest proud of is the face
    # meant to be seen -- knobs, sockets and switches are tall, and the solder
    # side of a board is flat.
    up = max((b.z1 for b in found if b is not pcb), default=pcb.z1) - pcb.z1
    down = pcb.z0 - min((b.z0 for b in found if b is not pcb), default=pcb.z0)
    if down > up * 1.5 and down > 2.0:
        return True, (f"flipped: the model stands {down:.1f} mm proud below the "
                      f"board against {up:.1f} mm above, and tall features "
                      f"(knobs, sockets, switches) belong on the face you see")
    return False, ""


def draft_part(entry: CatalogEntry, cad: Optional[Path] = None,
               step: Optional[Path] = None,
               flip: Optional[bool] = None) -> Draft:
    """Read a part definition off a vendor model.

    Returns a draft with real numbers and an honest account of what in it is
    still a guess. Never invents connectors: a mesh cannot tell you where a
    cable plugs in, and a made-up socket is worse than a missing one because
    the case would route wiring to it.
    """
    if cad is None:
        local = local_cad(entry.id)
        meshes = [p for p in local if p.suffix.lower() == ".stl"]
        steps = [p for p in local if p.suffix.lower() in (".step", ".stp")]
        if steps:
            # A STEP says more than an STL: its solids are exact, and the
            # holes can be read from the same geometry as the bodies instead
            # of from a text parse in a frame nobody checked.
            cad, step = steps[0], None
        elif meshes:
            cad = meshes[0]
        else:
            raise ValueError(f"{entry.id}: no STL or STEP on disk to measure")

    from_step = Path(cad).suffix.lower() in (".step", ".stp")
    found = bodies(cad)
    if not found:
        raise ValueError(f"{cad.name}: no bodies in the model")

    warnings: list[str] = []
    if len(found) == 1:
        # Some vendor models are one solid -- a battery, a speaker, a board
        # drawn as a block. It imports, and it is nothing like a board: no
        # components, no holes, no way to tell what is on it.
        warnings.append("the model is a single solid, a simplified block -- "
                        "there is no board, no components and no holes to "
                        "read, only an envelope")
    pcb_i = _pick_pcb(found)
    pcb = found[pcb_i]
    guess, why = _should_flip(found, pcb)
    if flip is None:
        flip = guess
        if why:
            warnings.append(why)
    else:
        # a person looked at it and said which way up -- that beats any rule
        warnings.append(("turned over" if flip else "kept the way it is "
                         "modelled") + " by hand at import"
                        + ("" if flip == guess else
                           f" (the heuristic would have {'turned it over' if guess else 'kept it'})"))
    warnings += _plausibility(pcb)

    width = pcb.size[0]
    placed = []
    for i, b in enumerate(found):
        if i == pcb_i:
            continue
        moved = b.shifted(pcb.x0, pcb.y0, 0.0)
        placed.append(moved.flipped(width, pcb.z1) if flip else
                      moved.shifted(0.0, 0.0, pcb.z0))

    volumes = []
    for b in sorted(placed, key=lambda b: -b.footprint):
        if b.footprint < COMPONENT_FOOTPRINT:
            continue
        cx, cy = b.centre
        volumes.append({
            "name": f"body_{len(volumes) + 1}",
            "kind": "body",
            "at": [round(cx, 2), round(cy, 2)],
            "size": [round(b.size[0], 2), round(b.size[1], 2)],
            "z": [round(b.z0, 2), round(b.z1, 2)],
            "src": {"confidence": "datasheet",
                    "note": f"body from {cad.name} -- IDENTIFY ME"},
        })

    # everything small, as one keepout per face
    for label, side in (("under", [b for b in placed if b.z1 <= 1e-6]),
                        ("over", [b for b in placed if b.z0 >= -1e-6])):
        small = [b for b in side if b.footprint < COMPONENT_FOOTPRINT]
        if not small:
            continue
        z0 = min(b.z0 for b in small)
        z1 = max(b.z1 for b in small)
        volumes.append({
            "name": f"components_{label}",
            "kind": "keepout",
            "at": [round(pcb.size[0] / 2, 2), round(pcb.size[1] / 2, 2)],
            "size": [round(pcb.size[0], 2), round(pcb.size[1], 2)],
            "z": [round(z0, 2), round(z1, 2)],
            "src": {"confidence": "datasheet",
                    "note": f"{len(small)} small bodies on this face, "
                            f"tallest {max(abs(z0), abs(z1)):.2f} mm"},
        })

    # Connectors a body's size and place give away: estimated, to be confirmed.
    from .connectors import propose
    proposed = propose(placed, width, pcb.size[1], entry.name)
    if proposed:
        warnings.append(f"{len(proposed)} connector(s) proposed from body "
                        f"sizes ({', '.join(c['name'] for c in proposed)}) -- "
                        f"estimated, confirm each")

    # The board's true shape, from its own mesh. A rectangle unless the board
    # visibly is not one -- a ring or a notched breakout fits a rectangle
    # badly, and the case pockets what the outline says.
    fp = board_footprint(pcb)
    outline, outline_poly = _outline(pcb, fp, width, flip, warnings)

    holes = []
    groups: list[HoleGroup] = []
    how = ""
    if from_step:
        how = "from the STEP geometry"
        if fp is None:
            warnings.append("could not read the board's footprint, so no "
                            "mounting holes")
        else:
            groups = hole_groups(fp)
            if not groups:
                warnings.append("no mounting-hole pattern found in the STEP")
    elif step and Path(step).exists():
        how = "from the vendor STEP"
        try:
            groups = step_holes(Path(step))
        except Exception as exc:              # pragma: no cover - vendor file
            warnings.append(f"could not read holes from {Path(step).name}: {exc}")
        if not groups:
            warnings.append("no mounting-hole pattern found in the STEP")
    else:
        warnings.append("no STEP file, so no mounting holes -- STL has none")

    if groups:
        found_holes = _holes_from(groups[0], pcb, width, flip, how)
        problem = _hole_problem(found_holes, outline_poly, placed, pcb.height)
        if problem:
            warnings.append(f"mounting holes dropped: {problem}")
        else:
            holes = found_holes

    part = {
        "id": slug(entry.name),
        "name": entry.name,
        "vendor": entry.vendor,
        "sku": entry.id,
        "url": entry.url,
        "category": _category(entry),
        "cad": f"adafruit/{entry.id}/{cad.name}",
        "outline": outline,
        "pcb_thickness": round(pcb.height, 2),
        "volumes": volumes,
        "connectors": proposed,
        "src": {"confidence": "datasheet",
                "url": "https://github.com/adafruit/Adafruit_CAD_Parts"},
        "notes": _notes(entry, cad, found, flip, warnings),
    }
    if holes:
        part["holes"] = holes

    return Draft(part=part, source=str(cad), warnings=warnings,
                 flipped=flip, bodies=len(found))


def _holes_from(group: HoleGroup, pcb: Body, width: float,
                flip: bool, how: str = "from the vendor STEP") -> list[dict]:
    """Mounting holes in the part frame."""
    out = []
    for i, (x, y) in enumerate(group.centres, 1):
        px, py = x - pcb.x0, y - pcb.y0
        if flip:
            px = width - px
        out.append({
            "name": f"h{i}",
            "at": [round(px, 2), round(py, 2)],
            "diameter": round(group.diameter, 2),
            "screw": group.screw or "?",
            "src": {"confidence": "datasheet", "note": how},
        })
    return out


#: A board this far from a rectangle is imported as the polygon it is. Below
#: it, corner radii and a chamfer are noise a rectangle is right to ignore.
RECT_FILL = 0.95

#: Boards and sheets outside these are almost certainly a units mistake or the
#: wrong body picked as the board, and an import should say so rather than
#: carry the error into every height in a case.
BOARD_THICKNESS = (0.4, 3.2)
BOARD_SPAN = (5.0, 500.0)

#: How far outside the outline a hole's centre may fall before the hole set is
#: judged to be in a different frame from the board.
HOLE_SLACK = 0.25


def _plausibility(pcb: Body) -> list[str]:
    """Warnings for a board that cannot be what it was read as."""
    out = []
    lo, hi = BOARD_THICKNESS
    if not lo <= pcb.height <= hi:
        out.append(f"the board body is {pcb.height:.2f} mm thick, outside "
                   f"{lo}-{hi} mm -- check the file's units, or whether the "
                   f"right body was taken as the board")
    span = max(pcb.size[0], pcb.size[1])
    if not BOARD_SPAN[0] <= span <= BOARD_SPAN[1]:
        out.append(f"the board is {span:.1f} mm across, outside "
                   f"{BOARD_SPAN[0]:.0f}-{BOARD_SPAN[1]:.0f} mm -- check the "
                   f"file's units")
    return out


def _outline(pcb: Body, fp, width: float, flip: bool,
             warnings: list[str]):
    """The part's outline, and the same shape as a polygon for later checks.

    A rectangle when the board is one (or looks like one within corner radii);
    otherwise the footprint's own polygon in the part frame, simplified to a
    twentieth of a millimetre so a rounded corner is a handful of points.
    """
    from shapely.geometry import Polygon, box

    w, d = pcb.size[0], pcb.size[1]
    rect = {"type": "rect", "size": [round(w, 2), round(d, 2)], "origin": "min"}
    if fp is None or fp.fill >= RECT_FILL:
        return rect, box(0.0, 0.0, w, d)

    shape = fp.outline.simplify(0.05, preserve_topology=True)
    pts = []
    for x, y in list(shape.exterior.coords)[:-1]:
        px, py = x - pcb.x0, y - pcb.y0
        pts.append((round(width - px if flip else px, 2), round(py, 2)))
    warnings.append(f"the board fills {fp.fill:.0%} of its bounding box, so "
                    f"it is imported as a {len(pts)}-point polygon, not a "
                    f"rectangle")
    return {"type": "poly", "points": [list(p) for p in pts]}, Polygon(pts)


def _hole_problem(holes: list[dict], outline, placed: list[Body],
                  thickness: float) -> str:
    """Why this hole set cannot be trusted, or '' when it can.

    Two ways a set goes wrong without any error being raised. Holes read in
    one frame and a board measured in another land off the board entirely;
    and a hole inside a part that straddles the board is not a hole anyone
    could put a screw through. Either way the whole set is dropped: a
    half-right pattern is worse than none, because the case would build posts
    on it.
    """
    from shapely.geometry import Point

    reach = outline.buffer(HOLE_SLACK)
    outside = [h["name"] for h in holes if not reach.contains(Point(*h["at"]))]
    if outside:
        return (f"{len(outside)} of {len(holes)} fall outside the board "
                f"outline ({', '.join(outside[:4])}"
                f"{'...' if len(outside) > 4 else ''}) -- the STEP and the "
                f"mesh are probably not in the same frame")
    for h in holes:
        p = Point(*h["at"])
        for b in placed:
            if b.footprint < COMPONENT_FOOTPRINT:
                continue
            # a body that reaches through the board's own thickness
            if b.z0 < thickness and b.z1 > 0.0 and (
                    b.x0 <= p.x <= b.x1 and b.y0 <= p.y <= b.y1):
                return (f"{h['name']} at ({p.x:.1f}, {p.y:.1f}) is inside a "
                        f"{b.size[0]:.1f} x {b.size[1]:.1f} mm body that "
                        f"passes through the board")
    return ""


def _category(entry: CatalogEntry) -> str:
    text = f"{entry.name} {entry.category}".lower()
    for key, cat in (("oled", "display"), ("display", "display"),
                     ("lcd", "display"), ("tft", "display"),
                     ("encoder", "input"), ("keypad", "input"),
                     ("button", "input"), ("trellis", "input"),
                     ("sensor", "sensor")):
        if key in text:
            return cat
    return "misc"


def _notes(entry: CatalogEntry, cad: Path, found: list[Body], flip: bool,
           warnings: list[str]) -> str:
    lines = [
        f"DRAFT -- imported from the vendor model, not yet checked by a human.",
        f"Measured from {cad.name}: {len(found)} connected bodies.",
        "The envelope and any mounting holes are real. What is NOT real:",
        "  * the volumes are named body_1, body_2... because a mesh does not "
        "say what they are, and a case treats a socket differently from a "
        "capacitor. Name them.",
        "  * connectors are at best PROPOSED. A mesh cannot tell you where a "
        "cable plugs in; any listed were matched from a body's size and place "
        "at an edge, are tagged estimated, and may be wrong or missing. A "
        "board with none asks the case for no cable room at all. Check every "
        "one, and add what it missed, before you cut anything.",
    ]
    if flip:
        lines.append("  * the part has been turned over -- check that the side "
                     "you expect to face the panel really does.")
    lines += [f"  * {w}" for w in warnings]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------

def _load_generated(path: Path) -> dict:
    if not path.exists():
        return {"parts": []}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("parts", [])
    return raw


def save_draft(draft: Draft, path: Path = GENERATED):
    """Write a draft into the imported-parts file and return the live Part.

    Imports live in their own file. A machine never rewrites a library someone
    wrote by hand, a bad import is one file to delete, and `git diff` after an
    import shows exactly what a machine decided.

    Re-importing a product replaces its entry rather than appending a second
    one -- the usual reason to import twice is that the first attempt was
    wrong.
    """
    import yaml

    from .library import _check_names
    from .schema import Part

    part = Part.model_validate(draft.part)
    _check_names(part)                        # fail before touching the file

    path = Path(path)
    doc = _load_generated(path)
    doc["parts"] = [p for p in doc["parts"] if p.get("id") != part.id]
    doc["parts"].append(draft.part)
    doc["parts"].sort(key=lambda p: p.get("id", ""))

    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Parts imported from the vendor catalogue -- MACHINE WRITTEN.\n"
        "#\n"
        "# Every entry here was measured off a vendor model by hwcase.ingest,\n"
        "# and every entry is a draft: the envelope and mounting holes are\n"
        "# real, the orientation is a guess, the volumes are unnamed and there\n"
        "# are no connectors, so these boards ask the case for no cable room\n"
        "# until somebody adds them.\n"
        "#\n"
        "# Promote an entry by moving it into one of the hand-written part\n"
        "# files and filling in what a mesh cannot know. Anything left in here\n"
        "# may be rewritten by the next import of the same product.\n"
    )
    path.write_text(
        header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                                default_flow_style=False, width=100),
        encoding="utf-8")
    return part


def forget_draft(part_id: str, path: Path = GENERATED) -> bool:
    """Remove one imported part. True if it was there."""
    import yaml

    path = Path(path)
    doc = _load_generated(path)
    keep = [p for p in doc["parts"] if p.get("id") != part_id]
    if len(keep) == len(doc["parts"]):
        return False
    doc["parts"] = keep
    if keep:
        path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                                       default_flow_style=False, width=100),
                        encoding="utf-8")
    else:
        path.unlink()                          # an empty file is just clutter
    return True
