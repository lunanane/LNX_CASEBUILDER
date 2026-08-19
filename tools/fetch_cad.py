#!/usr/bin/env python3
"""Pull vendor CAD for parts we use into vendor/cad/.

Adafruit publishes STEP + STL + F3D for most of their boards in
https://github.com/adafruit/Adafruit_CAD_Parts, in folders named
"<product id> <description>". That is the single richest source of exact
geometry we have, so we mirror the folders we care about rather than guessing
dimensions off shop pages.

    python tools/fetch_cad.py                 # everything in WANTED
    python tools/fetch_cad.py 3954 5752       # just these product ids
    python tools/fetch_cad.py --list          # what the repo has
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "adafruit/Adafruit_CAD_Parts"
API = f"https://api.github.com/repos/{REPO}/contents"
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "cad" / "adafruit"

# product id -> what it is, for the log
WANTED = {
    "3954": "NeoTrellis RGB driver PCB 4x4",
    "4741": "Grayscale 1.5in 128x128 OLED",
    "5752": "Quad rotary encoder breakout",
}

KEEP_SUFFIXES = (".step", ".stp", ".stl", ".dxf")


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "hwcase-fetch"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def list_folders() -> list[str]:
    return [e["name"] for e in _get_json(API) if e["type"] == "dir"]


def folder_for(product_id: str, folders: list[str]) -> str | None:
    for name in folders:
        if name.split(" ")[0] == product_id:
            return name
    return None


def fetch(product_id: str, folders: list[str]) -> int:
    folder = folder_for(product_id, folders)
    if folder is None:
        print(f"  {product_id}: not in {REPO}")
        return 0
    out = DEST / product_id
    out.mkdir(parents=True, exist_ok=True)
    entries = _get_json(f"{API}/{urllib.parse.quote(folder)}")
    n = 0
    for e in entries:
        if e["type"] != "file" or not e["name"].lower().endswith(KEEP_SUFFIXES):
            continue
        target = out / e["name"]
        if target.exists() and target.stat().st_size == e["size"]:
            print(f"  {product_id}: have {e['name']}")
            n += 1
            continue
        req = urllib.request.Request(e["download_url"],
                                     headers={"User-Agent": "hwcase-fetch"})
        with urllib.request.urlopen(req, timeout=180) as r:
            target.write_bytes(r.read())
        print(f"  {product_id}: got {e['name']} ({e['size'] / 1024:.0f} kB)")
        n += 1
    (out / "SOURCE.txt").write_text(
        f"https://github.com/{REPO}/tree/main/{urllib.parse.quote(folder)}\n",
        encoding="utf-8")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="Adafruit product ids")
    ap.add_argument("--list", action="store_true", help="list repo folders")
    args = ap.parse_args(argv)

    folders = list_folders()
    if args.list:
        for f in sorted(folders):
            print(f)
        return 0

    ids = args.ids or list(WANTED)
    total = 0
    for pid in ids:
        print(f"{pid} {WANTED.get(pid, '')}")
        total += fetch(pid, folders)
    print(f"\n{total} files in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
