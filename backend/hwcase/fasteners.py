"""Bolts and nuts a joint is built around, as table entries.

A captive-nut T-slot is sized entirely by its fastener: the bolt hole by the
thread, the slot that carries the shank into the edge of the mating plate by
the thread again, and the cross-slot that traps the nut by the nut's width
across flats and its thickness. None of those numbers is allowed to be a
constant in the joint code (A-6) -- M3 is what the laser-cut frame uses today,
M2.5 and M4 are one table row each away.

Sizes are ISO: ISO 4032 hex nuts, ISO 273 "medium" clearance holes, and the
stock lengths a hardware shop actually sells for ISO 4762 / ISO 7380 bolts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Fastener:
    """A metric bolt and the hex nut that goes on it."""

    #: table key and BOM name, e.g. "M3"
    name: str
    #: nominal thread diameter
    thread_d: float
    #: through-hole for the shank (ISO 273 medium). Bigger than the thread so
    #: two plates cut separately still line up when the bolt goes in.
    clearance_d: float
    #: nut width across flats (ISO 4032) -- what the trap's width is cut to
    nut_af: float
    #: nut thickness (ISO 4032) -- what the trap's depth along the bolt is cut to
    nut_t: float
    #: bolt lengths you can buy, shortest first
    lengths: tuple[float, ...]
    #: thread that must reach past the nut for it to hold: two pitches
    thread_past_nut: float

    @property
    def nut_ac(self) -> float:
        """Width across corners, the most room the nut ever takes up."""
        return self.nut_af / math.cos(math.radians(30.0))

    def bolt_length(self, needed: float) -> float:
        """The shortest stock bolt at least `needed` long, under the head.

        Raises when no stock length is long enough: a joint that needs a bolt
        nobody sells is a design error, not something to round down.
        """
        for length in self.lengths:
            if length >= needed - 1e-9:
                return length
        raise ValueError(
            f"{self.name}: a joint needs a bolt of at least {needed:.1f} mm "
            f"and the longest stocked is {self.lengths[-1]:g} mm")


#: Keyed by name. Add a row to support another size; nothing else changes.
FASTENERS: dict[str, Fastener] = {
    "M2.5": Fastener("M2.5", thread_d=2.5, clearance_d=2.9, nut_af=5.0,
                     nut_t=2.0, thread_past_nut=0.9,
                     lengths=(6.0, 8.0, 10.0, 12.0, 16.0, 20.0)),
    "M3": Fastener("M3", thread_d=3.0, clearance_d=3.4, nut_af=5.5,
                   nut_t=2.4, thread_past_nut=1.0,
                   lengths=(6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 25.0, 30.0)),
    "M4": Fastener("M4", thread_d=4.0, clearance_d=4.5, nut_af=7.0,
                   nut_t=3.2, thread_past_nut=1.4,
                   lengths=(8.0, 10.0, 12.0, 16.0, 20.0, 25.0, 30.0)),
}


def fastener(name: str) -> Fastener:
    """Look a size up, naming the sizes there are when it is not one of them."""
    try:
        return FASTENERS[name]
    except KeyError:
        raise KeyError(
            f"unknown fastener {name!r}; the table has "
            f"{', '.join(sorted(FASTENERS))}") from None
