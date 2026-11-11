"""What a PCB service offers for a panel, read from the one shared table.

A Eurorack panel ordered from a board house is FR4 with soldermask, a
silkscreen, and copper wherever the artwork wants metal to show -- finished
gold (ENIG) or silver (HASL) where the mask is opened over it. The colours a
service will actually make are a short list, and the editor's swatches, the
3D preview and the fab notes must all agree on it, so the list lives in
`web/pcb-fab.json` and both sides read that file. Nothing here imports
shapely: the schema validates against it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).resolve().parent.parent.parent / "web" / "pcb-fab.json"

#: what one decoration element can be on a PCB panel
LAYERS = ("silk", "copper", "substrate")


@lru_cache(maxsize=1)
def palette() -> dict:
    """The shared table, as the editor gets it from /api/pcb/palette."""
    return json.loads(TABLE.read_text(encoding="utf-8"))


def limits() -> dict:
    return palette()["limits"]


def colour(kind: str, key: str) -> str:
    """Preview sRGB of a mask, silk or finish key."""
    return palette()[kind][key]["color"]


def label(kind: str, key: str) -> str:
    return palette()[kind][key]["label"]


def metal_word(finish: str) -> str:
    """'gold' or 'silver': what exposed copper looks like with this finish."""
    return palette()["finish"][finish]["metal"]
