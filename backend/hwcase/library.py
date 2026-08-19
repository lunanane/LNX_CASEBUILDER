"""Loading the part library from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import yaml

from .schema import Part, Scene

DEFAULT_PARTS_DIR = Path(__file__).resolve().parent.parent / "parts"
DEFAULT_SCENES_DIR = Path(__file__).resolve().parent.parent / "scenes"


def load_scene(path: Path | str) -> Scene:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Scene.model_validate(raw)


class PartLibrary:
    def __init__(self, parts: Iterable[Part] = ()):
        self._parts: dict[str, Part] = {}
        for p in parts:
            self.add(p)

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "PartLibrary":
        root = Path(path) if path else DEFAULT_PARTS_DIR
        lib = cls()
        files = sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
        for f in files:
            lib.load_file(f)
        return lib

    def load_file(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return
        entries = raw["parts"] if isinstance(raw, dict) and "parts" in raw else raw
        if isinstance(entries, dict):
            entries = [entries]
        for entry in entries:
            try:
                self.add(Part.model_validate(entry))
            except Exception as exc:  # pragma: no cover - surfaced to the user
                raise ValueError(f"{path.name}: {entry.get('id', '?')}: {exc}") from exc

    def add(self, part: Part) -> None:
        if part.id in self._parts:
            raise ValueError(f"duplicate part id {part.id!r}")
        self._parts[part.id] = part

    # -- access ------------------------------------------------------------

    def __getitem__(self, part_id: str) -> Part:
        try:
            return self._parts[part_id]
        except KeyError:
            raise KeyError(f"unknown part {part_id!r}") from None

    def __contains__(self, part_id: object) -> bool:
        return part_id in self._parts

    def __iter__(self) -> Iterator[Part]:
        return iter(self._parts.values())

    def __len__(self) -> int:
        return len(self._parts)

    @property
    def ids(self) -> list[str]:
        return sorted(self._parts)

    def by_category(self) -> dict[str, list[Part]]:
        out: dict[str, list[Part]] = {}
        for p in self._parts.values():
            out.setdefault(p.category, []).append(p)
        return out
