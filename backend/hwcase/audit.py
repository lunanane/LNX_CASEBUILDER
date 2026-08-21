"""Check the whole part library against the vendor models it claims to come from.

A part file is a set of assertions about a physical object, and the vendor's
own model is the closest thing to a second opinion we have. This runs the
measurement over every part that names a `cad:` file and reports where the two
disagree.

Worth doing periodically rather than once, for two reasons. Vendors revise
boards and quietly replace the model; and our own extraction changes -- the
1.5" OLED was wrong for weeks because band slicing merged two connectors into
one imaginary strip, and an audit like this would have shown the outline
disagreeing with the mesh the day it was written.

    python -m hwcase.cli audit
    python -m hwcase.cli audit --tolerance 0.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["Finding", "audit_part", "audit_library"]

CAD_ROOT = Path(__file__).resolve().parent.parent.parent / "vendor" / "cad"


@dataclass
class Finding:
    part: str
    kind: str
    message: str
    severity: str = "warning"       # "error" if it cannot be checked at all


@dataclass
class PartAudit:
    part: str
    source: Optional[str] = None
    findings: list[Finding] = field(default_factory=list)
    checked: bool = False

    @property
    def ok(self) -> bool:
        return self.checked and not self.findings


#: What we can actually measure. A `cad:` field is also used to point at a
#: datasheet or an annotated photograph, which is useful provenance and is not
#: a model -- saying "unreadable" about a PDF is just wrong.
MESH = (".stl", ".step", ".stp")


def _cad_path(cad: str) -> Optional[Path]:
    """Resolve a part's `cad:` reference to a file that exists.

    The reference is a hint, not a guarantee -- files get renamed in the
    vendor repo -- so fall back to any mesh in the folder it points at rather
    than reporting a part as unmeasurable because of a filename.
    """
    direct = CAD_ROOT / cad
    if direct.exists():
        return direct

    folder = direct.parent
    if folder.is_dir():
        meshes = sorted(folder.glob("*.stl")) + sorted(folder.glob("*.STL"))
        if meshes:
            return meshes[0]
    return None


def audit_part(part, tolerance: float = 0.3) -> PartAudit:
    """Compare one part's declared outline and thickness against its model."""
    from .measure import bodies

    result = PartAudit(part=part.id)
    cad = getattr(part, "cad", None)
    if not cad:
        return result                       # nothing claimed, nothing to check

    if Path(cad).suffix.lower() not in MESH:
        result.findings.append(Finding(
            part.id, "not-a-model",
            f"cad: names {Path(cad).name}, which is documentation rather than "
            f"geometry -- nothing here can be checked against it",
            severity="info"))
        return result

    path = _cad_path(cad)
    if path is None:
        result.findings.append(Finding(
            part.id, "missing-cad",
            f"names {cad} but no such file is on disk -- "
            f"run tools/fetch_cad.py", severity="error"))
        return result

    result.source = path.name
    try:
        found = bodies(path)
    except Exception as exc:                # pragma: no cover - vendor file
        result.findings.append(Finding(
            part.id, "unreadable", f"{path.name}: {exc}", severity="error"))
        return result

    if not found:
        result.findings.append(Finding(
            part.id, "empty", f"{path.name} contains no geometry",
            severity="error"))
        return result

    result.checked = True
    pcb = max(found, key=lambda b: b.footprint)

    declared = getattr(part.outline, "size", None)
    if declared:
        dw, dh = float(declared[0]), float(declared[1])
        mw, mh = pcb.size[0], pcb.size[1]
        # A part may legitimately be modelled rotated relative to how we use
        # it, so a swapped pair is a match, not a discrepancy.
        swapped = abs(dw - mh) < tolerance and abs(dh - mw) < tolerance
        if not swapped and (abs(dw - mw) > tolerance or abs(dh - mh) > tolerance):
            result.findings.append(Finding(
                part.id, "outline",
                f"declared {dw:.2f} x {dh:.2f} mm, model measures "
                f"{mw:.2f} x {mh:.2f} mm"))

    thickness = getattr(part, "pcb_thickness", None)
    if thickness and abs(float(thickness) - pcb.height) > tolerance:
        result.findings.append(Finding(
            part.id, "thickness",
            f"declared {float(thickness):.2f} mm, model measures "
            f"{pcb.height:.2f} mm"))

    # A volume standing further proud than anything in the model is usually a
    # number somebody rounded up "to be safe", and it silently makes the case
    # taller than it needs to be.
    if part.volumes:
        # A vendor model may be built either way up, and a part file is
        # entitled to turn it over -- the OLED is modelled display-down and
        # used display-up. So compare against the model's two reaches in
        # whichever pairing fits, rather than assuming ours matches theirs.
        above = max(b.z1 for b in found) - pcb.z1
        below = pcb.z0 - min(b.z0 for b in found)
        # Declared z is measured from the board's UNDERSIDE (z = 0 is the
        # bottom face), while the model reaches are measured from the faces
        # they leave. Subtract the board or the comparison double-counts it,
        # and every part looks 1.6 mm too tall.
        thickness = float(getattr(part, "pcb_thickness", 0.0) or 0.0)
        declared_top = max(max(v.z) for v in part.volumes) - thickness
        declared_bottom = -min(min(v.z) for v in part.volumes)

        slack = tolerance * 3                # volumes carry clearance, so be kind
        same = max(declared_top - above, declared_bottom - below)
        flipped = max(declared_top - below, declared_bottom - above)
        excess = min(same, flipped)

        if excess > slack:
            turned = " (read as turned over)" if flipped < same else ""
            result.findings.append(Finding(
                part.id, "reach",
                f"volumes stand {excess:.2f} mm further proud than anything in "
                f"the model{turned}: declared {declared_bottom:.2f} below / "
                f"{declared_top:.2f} above, model {below:.2f} / {above:.2f}"))

    return result


def audit_library(lib, tolerance: float = 0.3) -> list[PartAudit]:
    return [audit_part(p, tolerance) for p in lib]
