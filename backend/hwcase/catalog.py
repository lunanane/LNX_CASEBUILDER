"""Search Adafruit's live catalogue, so new products are usable the day they ship.

The part library in `parts/*.yaml` is the set of things somebody has actually
measured and vouched for -- a few dozen entries. Adafruit sell about five and a
half thousand. Listing all of those in the editor's palette would bury the
handful that matter, so the palette shows the library and this module answers
"do you have a ...?" on demand.

Two feeds, because they answer different questions:

* **`/api/products`** -- every product, 8 MB, about two seconds. Names, SKUs,
  images, URLs. This is what search runs against. Not dimensions: the feed has
  none, and the shop pages carry them for about one product in six, so CAD is
  the only honest source of geometry.
* **the CAD repo listing** -- which of those products have a vendor model we
  can measure. Around 470 do. That distinction matters more than anything else
  in a search result: a product with CAD can be imported with real geometry, and
  one without cannot be imported at all without someone reaching for calipers.

Both are cached on disk. A hosted instance refreshes them on a timer and keeps
working from cache when Adafruit is unreachable -- a search box that goes blank
because someone else's site is down is worse than one showing yesterday's
catalogue, so staleness is reported rather than treated as failure.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

__all__ = ["CatalogEntry", "Catalog", "search"]

PRODUCTS_URL = "https://www.adafruit.com/api/products"
PRODUCT_URL = "https://www.adafruit.com/api/products/{pid}"
CAD_INDEX_URL = "https://api.github.com/repos/adafruit/Adafruit_CAD_Parts/contents"
CAD_REPO = "adafruit/Adafruit_CAD_Parts"

USER_AGENT = "hwcase/0.1 (parametric enclosure tool)"

#: How long a cached feed is considered current. Adafruit release weekly, so a
#: day is plenty, and it keeps a self-hosted instance from hammering them.
TTL = 24 * 3600

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "catalog"

#: Categories that could plausibly end up in an enclosure. Everything else --
#: wire, solder, tools, books, kits -- is noise in this context, so it is
#: ranked down rather than removed (a search for "hakko" should still find it).
RELEVANT = (
    "breakout", "display", "sensor", "board", "development", "featherwing",
    "shield", "bonnet", "hat", "wing", "led", "audio", "button", "keypad",
    "encoder", "rgb", "matrix", "oled", "tft", "prototyping", "stemma",
)


def _fetch(url: str, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    """One product, as much as a search result needs to know."""

    id: str
    name: str
    vendor: str = "Adafruit"
    url: str = ""
    image: Optional[str] = None
    category: str = ""
    #: folder in the CAD repo, if the vendor published a model
    cad: Optional[str] = None
    #: id in our own part library, if somebody has already measured this
    part_id: Optional[str] = None
    score: float = 0.0

    @property
    def importable(self) -> bool:
        """Can we produce real geometry, or only a name and a picture?"""
        return self.cad is not None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["importable"] = self.importable
        d["in_library"] = self.part_id is not None
        return d


def _entry_from_product(p: dict) -> Optional[CatalogEntry]:
    pid = str(p.get("product_id") or "").strip()
    name = (p.get("product_name") or "").strip()
    if not pid or not name:
        return None
    return CatalogEntry(
        id=pid,
        name=name,
        vendor=(p.get("product_manufacturer") or "Adafruit").strip() or "Adafruit",
        url=p.get("product_url") or f"https://www.adafruit.com/product/{pid}",
        image=p.get("product_image") or None,
        category=_category_name(p),
    )


def _category_name(p: dict) -> str:
    """A readable category, or nothing.

    `product_master_category` is a bare numeric id -- "98", "536" -- and the
    feed carries no table to look those up in. Showing a number to somebody
    picking a part is worse than showing nothing, and scoring against one is
    noise, so a purely numeric category is dropped.
    """
    raw = str(p.get("product_master_category") or "").strip()
    return "" if raw.isdigit() else raw


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9.]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def score(entry: CatalogEntry, terms: list[str]) -> float:
    """How well one product answers the query.

    Deliberately blunt -- this is a product picker, not a search engine. What
    it must get right is the ordering people actually expect: typing a SKU
    lands on that SKU, typing words matches whole words before fragments, and
    anything we can import with real geometry outranks something we would have
    to guess at.
    """
    if not terms:
        return 0.0
    name = entry.name.lower()
    words = set(_tokens(name)) | set(_tokens(entry.category))
    total = 0.0

    for t in terms:
        if t == entry.id:
            total += 100.0                    # a SKU is an exact request
        elif t in words:
            total += 10.0                     # whole word
        elif t in name:
            total += 4.0                      # fragment
        elif any(w.startswith(t) for w in words):
            total += 3.0                      # prefix, i.e. still typing
        else:
            return 0.0                        # every term has to land

    if entry.cad:
        total += 6.0
    if entry.part_id:
        total += 12.0                         # already measured and trusted
    if any(k in entry.category.lower() or k in name for k in RELEVANT):
        total += 2.0
    # short names are usually the product, long ones the kit it ships in
    total += max(0.0, 3.0 - len(name) / 40.0)
    return total


# ---------------------------------------------------------------------------
# the catalogue itself
# ---------------------------------------------------------------------------

@dataclass
class Catalog:
    """Cached product and CAD feeds, refreshed on a timer."""

    cache_dir: Path = CACHE_DIR
    ttl: float = TTL
    products: list[CatalogEntry] = field(default_factory=list)
    cad_folders: dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0
    stale: bool = False
    error: Optional[str] = None

    # -- disk -------------------------------------------------------------
    @property
    def products_path(self) -> Path:
        return self.cache_dir / "adafruit-products.json"

    @property
    def cad_path(self) -> Path:
        return self.cache_dir / "adafruit-cad.json"

    def age(self) -> Optional[float]:
        if not self.fetched_at:
            return None
        return time.time() - self.fetched_at

    # -- loading ----------------------------------------------------------
    def load(self, refresh: bool = False) -> "Catalog":
        """Fill from cache, refreshing from the network when it is old.

        Never raises for a network problem. A search box that empties itself
        because someone else's server is down is worse than one showing
        yesterday's catalogue, so a failed refresh downgrades to `stale` and
        keeps the old data.
        """
        self.error = None
        fresh_enough = (
            self.products_path.exists()
            and (time.time() - self.products_path.stat().st_mtime) < self.ttl)

        if refresh or not fresh_enough:
            try:
                self._refresh()
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"

        self._read_cache()
        if self.error and self.products:
            self.stale = True
        return self

    def _refresh(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        raw = _fetch(PRODUCTS_URL)
        json.loads(raw)                       # refuse to cache a truncated body
        self.products_path.write_bytes(raw)

        # The CAD listing is a nicety, not a requirement: without it every
        # product simply looks un-importable, which is wrong but not broken.
        try:
            index = json.loads(_fetch(CAD_INDEX_URL, timeout=60))
            folders = {}
            for item in index:
                if item.get("type") != "dir":
                    continue
                name = item.get("name", "")
                head = name.split(" ")[0]
                if head.isdigit():
                    folders[head] = name
            self.cad_path.write_text(json.dumps(folders, indent=1),
                                     encoding="utf-8")
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            self.error = f"CAD index unavailable ({type(exc).__name__})"

    def _read_cache(self) -> None:
        if self.cad_path.exists():
            try:
                self.cad_folders = json.loads(
                    self.cad_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.cad_folders = {}

        if not self.products_path.exists():
            self.products = []
            self.fetched_at = 0.0
            if not self.error:
                self.error = "no cached catalogue and no network"
            return

        try:
            raw = json.loads(self.products_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.products = []
            self.error = f"cached catalogue is corrupt: {exc}"
            return

        out = []
        for p in raw:
            e = _entry_from_product(p)
            if e is None:
                continue
            e.cad = self.cad_folders.get(e.id)
            out.append(e)
        self.products = out
        self.fetched_at = self.products_path.stat().st_mtime

    # -- searching --------------------------------------------------------
    def search(self, query: str, limit: int = 25,
               known: Optional[dict[str, str]] = None) -> list[CatalogEntry]:
        """Best matches for `query`, best first.

        `known` maps SKU -> part id for things already in our library, so a
        result can say "you already have this, measured" instead of offering
        to import a second copy.
        """
        terms = _tokens(query)
        if not terms:
            return []
        known = known or {}

        scored = []
        for e in self.products:
            e.part_id = known.get(e.id)
            s = score(e, terms)
            if s > 0:
                e.score = s
                scored.append(e)
        scored.sort(key=lambda e: (-e.score, e.name))
        return scored[:limit]

    def get(self, product_id: str) -> Optional[CatalogEntry]:
        pid = str(product_id).strip()
        for e in self.products:
            if e.id == pid:
                return e
        return None

    def status(self) -> dict:
        age = self.age()
        return {
            "products": len(self.products),
            "with_cad": sum(1 for e in self.products if e.cad),
            "fetched_at": self.fetched_at or None,
            "age_seconds": round(age) if age is not None else None,
            "stale": self.stale,
            "error": self.error,
        }


#: One shared instance -- the feeds are big and identical for every caller.
_shared: Optional[Catalog] = None


def shared(refresh: bool = False) -> Catalog:
    global _shared
    if _shared is None:
        _shared = Catalog()
        refresh = refresh or not _shared.products_path.exists()
    if refresh or not _shared.products:
        _shared.load(refresh=refresh)
    return _shared


def search(query: str, limit: int = 25,
           known: Optional[dict[str, str]] = None) -> list[CatalogEntry]:
    return shared().search(query, limit=limit, known=known)
