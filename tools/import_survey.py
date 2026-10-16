#!/usr/bin/env python3
"""How often does importing a board from Adafruit's CAD repo actually work?

Picks folders from the catalogue's CAD index, downloads each one into the
normal vendor CAD cache, runs the same `draft_part` the editor's import button
runs, and writes one CSV row per folder: imported or failed and why, whether
the board was turned over, how many mounting holes and bodies were found.
Then one summary line on stdout. It turns "the importer is reliable" into a
number that a change to `measure.py` can be seen to move.

    cd backend
    python ../tools/import_survey.py --sample 40 --seed 1      # the baseline
    python ../tools/import_survey.py --all     # every folder, into out/

The sample is deterministic for a given seed and a given index: folders are
sorted by name before sampling, and the index is pinned to a commit, which the
summary names. The whole repository is about a gigabyte, hence the sample.

Folders with no model the importer can read (STEP-only, today) are recorded
as failed without downloading them -- the import route refuses them the same
way. Downloads land in vendor/cad/adafruit/, which is a cache: only the
handful of boards the tests use are committed, and the rest is not source.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from hwcase import catalog as cat  # noqa: E402
from hwcase import ingest  # noqa: E402

BASELINE = ROOT / "tools" / "import_survey_baseline.csv"
#: A full survey is a different row set from the committed sample, so by
#: default it goes to the ignored out/ rather than over the baseline.
ALL_OUT = ROOT / "out" / "import_survey_all.csv"

COLUMNS = ["folder", "product_id", "formats", "result", "reason", "flipped",
           "holes", "bodies", "commit"]


def sample_folders(index: dict, n: int | None, seed: int) -> list[str]:
    """`n` folders chosen by `seed`, or all of them; product folders only."""
    folders = sorted(f for f in index if cat._product_id(f))
    if n is None or n >= len(folders):
        return folders
    return sorted(random.Random(seed).sample(folders, n))


def default_out(survey_all: bool) -> Path:
    """Where the CSV goes when --out is not given."""
    return ALL_OUT if survey_all else BASELINE


def survey_one(c: cat.Catalog, folder: str) -> dict:
    """Download one folder and try to draft a part from it."""
    entry = c.cad_entry(folder)
    formats = cat._formats([f.name for f in c.cad_index[folder]])
    row = {"folder": folder, "product_id": entry.id if entry else "",
           "formats": " ".join(formats), "result": "failed", "reason": "",
           "flipped": "", "holes": "", "bodies": "", "commit": c.commit or ""}

    if entry is None:
        row["reason"] = "folder name carries no product id"
        return row
    if not any(f in cat.IMPORTABLE_FORMATS for f in formats):
        row["reason"] = f"no STL ({' '.join(formats) or 'no model files'})"
        return row

    try:
        paths = ingest.download_cad(entry, folder=folder)
        stls = sorted(p for p in paths if p.suffix.lower() == ".stl")
        steps = sorted(p for p in paths
                       if p.suffix.lower() in (".step", ".stp"))
        # the importer's own choice when a folder holds several STLs: the
        # first by name (review F5) -- surveyed as it behaves, not as it should
        draft = ingest.draft_part(entry, cad=stls[0],
                                  step=steps[0] if steps else None)
    except Exception as exc:                  # every failure is a result here
        row["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return row

    row.update(result="imported",
               flipped="yes" if draft.flipped else "no",
               holes=len(draft.part.get("holes", [])),
               bodies=draft.bodies,
               reason="; ".join(draft.warnings)[:200])
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--sample", type=int, metavar="N",
                      help="survey N folders chosen by --seed")
    pick.add_argument("--all", action="store_true",
                      help="survey every folder (about a gigabyte)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None,
                    help=f"CSV to write (default {BASELINE.relative_to(ROOT)} "
                         f"for a sample, {ALL_OUT.relative_to(ROOT)} for --all)")
    args = ap.parse_args(argv)
    args.out = args.out or default_out(args.all)

    c = cat.shared()
    if not c.cad_index:
        print(f"import survey: no CAD index ({c.error})", file=sys.stderr)
        return 2
    if c.truncated or c.stale:
        # surveyed anyway, but never quietly: the numbers describe a partial
        # or old index
        print(f"import survey: warning: {c.error}", file=sys.stderr)

    folders = sample_folders(c.cad_index, None if args.all else args.sample,
                             args.seed)
    rows = []
    for i, folder in enumerate(folders, 1):
        row = survey_one(c, folder)
        rows.append(row)
        print(f"[{i}/{len(folders)}] {row['result']:8} {folder}"
              f"{'  -- ' + row['reason'] if row['result'] == 'failed' else ''}",
              file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    ok = sum(r["result"] == "imported" for r in rows)
    why = Counter(r["reason"].split(" (")[0].split(":")[0]
                  for r in rows if r["result"] == "failed")
    reasons = ", ".join(f"{n} {k}" for k, n in why.most_common())
    holes = sum(1 for r in rows if r["result"] == "imported" and r["holes"])
    print(f"import survey: {ok}/{len(rows)} imported "
          f"({100 * ok / max(1, len(rows)):.0f}%), {holes} with mounting holes; "
          f"failed: {reasons or 'none'}; commit {(c.commit or '?')[:12]}, "
          f"{'all folders' if args.all else f'sample {args.sample} seed {args.seed}'}"
          f" -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
