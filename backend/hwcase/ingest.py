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

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .catalog import CAD_REPO, USER_AGENT, Catalog, CatalogEntry, shared
from .measure import Body, HoleGroup, bodies, step_holes

__all__ = ["CadFile", "Draft", "cad_files", "download_cad", "draft_part",
           "GENERATED"]

CONTENTS = f"https://api.github.com/repos/{CAD_REPO}/contents"

#: Vendor CAD lands here; one folder per product, mirroring the repo.
CAD_ROOT = Path(__file__).resolve().parent.parent.parent / "vendor" / "cad" / "adafruit"

#: Imported parts go in their own file, so a human-written library is never
#: rewritten by a machine and a bad import is one file to delete.
GENERATED = Path(__file__).resolve().parent.parent / "parts" / "imported.yaml"

KEEP = (".stl", ".step", ".stp")

#: Below this footprint a body is an SMD component, not a feature to cut
#: around. They are aggregated into one keepout per face -- the 4741 has 225 of
#: them and 225 volumes would say nothing 1 keepout does not.
COMPONENT_FOOTPRINT = 60.0


def _get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

@dataclass
class CadFile:
    name: str
    url: str
    size: int = 0

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


def cad_files(folder: str) -> list[CadFile]:
    """The CAD files in one product's folder in the vendor repo."""
    url = f"{CONTENTS}/{urllib.parse.quote(folder)}"
    out = []
    for item in json.loads(_get(url)):
        if item.get("type") != "file":
            continue
        name = item.get("name", "")
        if Path(name).suffix.lower() in KEEP and item.get("download_url"):
            out.append(CadFile(name, item["download_url"], item.get("size", 0)))
    return out


def download_cad(entry: CatalogEntry, dest_root: Path = CAD_ROOT,
                 overwrite: bool = False) -> list[Path]:
    """Mirror one product's CAD folder locally, returning what is on disk.

    Files already present are left alone: the vendor repo does not churn, and
    re-downloading eight megabytes to re-measure the same board is rude to
    somebody else's bandwidth.
    """
    if not entry.cad:
        raise ValueError(f"{entry.id}: no vendor CAD published")

    dest = Path(dest_root) / entry.id
    dest.mkdir(parents=True, exist_ok=True)
    got = []
    for f in cad_files(entry.cad):
        target = dest / f.name
        if overwrite or not target.exists():
            target.write_bytes(_get(f.url, timeout=180))
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
               step: Optional[Path] = None) -> Draft:
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
        if not meshes:
            raise ValueError(f"{entry.id}: no STL on disk to measure")
        cad, step = meshes[0], (step or (steps[0] if steps else None))

    found = bodies(cad)
    if not found:
        raise ValueError(f"{cad.name}: no bodies in the model")

    warnings: list[str] = []
    pcb_i = _pick_pcb(found)
    pcb = found[pcb_i]
    flip, why = _should_flip(found, pcb)
    if why:
        warnings.append(why)

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

    holes = []
    if step and Path(step).exists():
        try:
            groups = step_holes(Path(step))
        except Exception as exc:              # pragma: no cover - vendor file
            warnings.append(f"could not read holes from {Path(step).name}: {exc}")
            groups = []
        if groups:
            holes = _holes_from(groups[0], pcb, width, flip)
        else:
            warnings.append("no mounting-hole pattern found in the STEP")
    else:
        warnings.append("no STEP file, so no mounting holes -- STL has none")

    part = {
        "id": slug(entry.name),
        "name": entry.name,
        "vendor": entry.vendor,
        "sku": entry.id,
        "url": entry.url,
        "category": _category(entry),
        "cad": f"adafruit/{entry.id}/{cad.name}",
        "outline": {
            "type": "rect",
            "size": [round(pcb.size[0], 2), round(pcb.size[1], 2)],
            "origin": "min",
        },
        "pcb_thickness": round(pcb.height, 2),
        "volumes": volumes,
        "connectors": [],
        "src": {"confidence": "datasheet",
                "url": "https://github.com/adafruit/Adafruit_CAD_Parts"},
        "notes": _notes(entry, cad, found, flip, warnings),
    }
    if holes:
        part["holes"] = holes

    return Draft(part=part, source=str(cad), warnings=warnings,
                 flipped=flip, bodies=len(found))


def _holes_from(group: HoleGroup, pcb: Body, width: float,
                flip: bool) -> list[dict]:
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
            "src": {"confidence": "datasheet", "note": "from the vendor STEP"},
        })
    return out


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
        "  * there are no connectors. A mesh cannot tell you where a cable "
        "plugs in, so this board currently asks the case for no cable room at "
        "all. Add them before you cut anything.",
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
