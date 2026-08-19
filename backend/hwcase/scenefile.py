"""Writing a scene back to disk without throwing away its comments.

The scene files carry a lot of reasoning in their comments -- why the Pi is
rotated, what the panel is derived from, which feature of each part has to sit
flush. A naive `yaml.safe_dump` of the edited model wipes all of it, which is
exactly what happened the first time the editor's save button was used.

So instead of dumping, we *merge*: load the existing file in round-trip mode,
walk it, and write the new values into the nodes that are already there.
ruamel keeps every comment attached to the nodes it did not have to touch.

Two further rules keep the file readable:

* a key is written only if it is non-default **or** already present in the
  file, so saving does not sprinkle `parent: null` over every placement;
* list entries are matched by `id` (placements) or `name` (panels), so
  reordering, adding and deleting all behave.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .schema import Scene


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 100
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def _key_of(entry: Any) -> Optional[str]:
    if isinstance(entry, dict):
        for field in ("id", "name"):
            if field in entry:
                return str(entry[field])
    return None


def _merge_mapping(node: Any, full: dict, minimal: dict) -> Any:
    """Write `full`'s values into `node`, but only for keys worth keeping."""
    if not isinstance(node, dict):
        return _plain(full, minimal)

    keep = set(minimal) | (set(node) & set(full))
    for key in list(node):
        if key not in keep:
            del node[key]              # the value went back to its default
    for key in keep:
        new_full = full[key]
        new_min = minimal.get(key, new_full)
        if key in node:
            node[key] = _merge_value(node[key], new_full, new_min)
        else:
            node[key] = _plain(new_full, new_min)
    return node


def _merge_seq(node: Any, full: list, minimal: list) -> Any:
    if not isinstance(node, list):
        return _plain(full, minimal)

    existing = {}
    for i, entry in enumerate(node):
        k = _key_of(entry)
        if k is not None:
            existing[k] = i

    out = CommentedSeq()
    try:
        out.copy_attributes(node)      # keeps comments bound to the sequence
    except AttributeError:             # older ruamel
        pass

    used = set()
    for f_entry, m_entry in zip(full, minimal):
        k = _key_of(f_entry)
        if k is not None and k in existing:
            idx = existing[k]
            used.add(idx)
            out.append(_merge_value(node[idx], f_entry, m_entry))
        else:
            out.append(_plain(f_entry, m_entry))
    return out


def _is_scalar_seq(value: Any) -> bool:
    """A coordinate, a size, a sheet -- something that belongs on one line."""
    return (isinstance(value, list) and len(value) <= 6
            and all(not isinstance(v, (dict, list)) for v in value))


def _flow_seq(values: list, node: Any = None) -> CommentedSeq:
    """Keep `pos: [12.0, 3.0, 0.0]` on one line.

    Exploding a 3-vector over three lines is not just ugly: ruamel re-anchors
    the comments that followed it, which is how a note about `rot_z` ended up
    in the middle of a coordinate.
    """
    seq = CommentedSeq(values)
    was_flow = getattr(getattr(node, "fa", None), "flow_style", lambda: None)()
    seq.fa.set_flow_style() if (was_flow or was_flow is None) else seq.fa.set_block_style()
    return seq


def _merge_value(node: Any, full: Any, minimal: Any) -> Any:
    if isinstance(full, dict):
        return _merge_mapping(node, full, minimal if isinstance(minimal, dict) else {})
    if _is_scalar_seq(full):
        return _flow_seq(full, node)
    if isinstance(full, list):
        return _merge_seq(node, full, minimal if isinstance(minimal, list) else full)
    return full


def _plain(full: Any, minimal: Any) -> Any:
    """A brand new node: keep only the non-default keys."""
    if isinstance(full, dict):
        src = minimal if isinstance(minimal, dict) else full
        out = CommentedMap()
        for key in src:
            out[key] = _plain(full[key], src.get(key, full[key]))
        return out
    if _is_scalar_seq(full):
        return _flow_seq(full)
    if isinstance(full, list):
        src = minimal if isinstance(minimal, list) and len(minimal) == len(full) else full
        return CommentedSeq(_plain(f, m) for f, m in zip(full, src))
    return full


def dumps(scene: Scene, previous: Optional[str] = None) -> str:
    """Serialise `scene`, reusing the layout and comments of `previous`."""
    full = scene.model_dump(mode="json")
    minimal = scene.model_dump(mode="json", exclude_defaults=True)
    # `name` anchors the file even when it matches the default
    minimal.setdefault("name", full["name"])

    yaml = _yaml()
    doc: Any = None
    if previous:
        try:
            doc = yaml.load(previous)
        except Exception:
            doc = None

    merged = _merge_mapping(doc, full, minimal) if isinstance(doc, dict) \
        else _plain(full, minimal)

    buf = io.StringIO()
    yaml.dump(merged, buf)
    return buf.getvalue()


def save(scene: Scene, path: Path) -> bool:
    """Write the scene to `path`. Returns True if a previous file was reused."""
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(scene, previous), encoding="utf-8")
    return previous is not None
