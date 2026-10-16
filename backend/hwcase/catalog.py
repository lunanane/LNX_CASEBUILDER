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
* **the CAD repo's git tree** -- which of those products have a vendor model,
  in which folders, as which files. Around 470 folders do. That distinction
  matters more than anything else in a search result: a product with a model
  we can read is imported with real geometry, and one without cannot be
  imported at all without someone reaching for calipers.

The tree comes from one request, `/git/trees/<commit>?recursive=1`, which lists
every file in the repository with its size. The commit is resolved first and
recorded, and every download names it rather than `main`, so an import made
today can be repeated byte for byte next year -- and so the index and the files
it points at can never be from two different days. The contents API this
replaced needed one call per folder and lost all but one folder per product ID.

Both feeds are cached on disk, and the cache's age is checked on every request
(`shared()`), so a long-running server picks up the new week's products by
itself -- there is no timer; the first search after the TTL lapses refreshes.
When Adafruit or GitHub are unreachable it keeps working from cache -- a search
box that goes blank because someone else's site is down is worse than one
showing yesterday's catalogue, so staleness is reported rather than treated as
failure, and a failed refresh is not retried on every keystroke.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["CadFile", "CatalogEntry", "Catalog", "search", "shared"]

PRODUCTS_URL = "https://www.adafruit.com/api/products"
PRODUCT_URL = "https://www.adafruit.com/api/products/{pid}"
CAD_REPO = "adafruit/Adafruit_CAD_Parts"
#: `main` resolved to a commit SHA. With this Accept header the API answers
#: with the bare 40-character SHA instead of a commit object.
CAD_COMMIT_URL = f"https://api.github.com/repos/{CAD_REPO}/commits/main"
CAD_TREE_URL = f"https://api.github.com/repos/{CAD_REPO}/git/trees/{{sha}}?recursive=1"
#: Files come from the raw host at the pinned commit: no API quota spent, and
#: no chance of a file from a newer commit than the index describes.
CAD_RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"

USER_AGENT = "hwcase/0.1 (parametric enclosure tool)"

#: How long a cached feed is considered current. Adafruit release weekly, so a
#: day is plenty, and it keeps a self-hosted instance from hammering them.
TTL = 24 * 3600

#: After a refresh fails, how long requests keep answering from cache before
#: the network is tried again. Checking the TTL on every request is only safe
#: with this: without it, a vendor outage would turn every search into a
#: two-minute wait for a timeout.
RETRY = 5 * 60

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "vendor" / "catalog"

#: Model formats, by file suffix, as a search result names them. Anything else
#: in a folder (renders, gifs, PDFs, .DS_Store) is indexed but is not a model.
MODEL_FORMATS = {
    ".stl": "stl", ".step": "step", ".stp": "step", ".f3d": "f3d",
    ".f3z": "f3z", ".3mf": "3mf", ".fbx": "fbx", ".smt": "smt", ".dxf": "dxf",
}

#: The formats the importer can measure. STL only: STEP needs a CAD kernel to
#: turn into triangles, and until it has one a STEP-only product must not be
#: offered as importable -- 158 of 478 folders are STEP-only, and promising them
#: an import that fails after the download is the bug this replaced.
IMPORTABLE_FORMATS = ("stl",)

#: Categories that could plausibly end up in an enclosure. Everything else --
#: wire, solder, tools, books, kits -- is noise in this context, so it is
#: ranked down rather than removed (a search for "hakko" should still find it).
RELEVANT = (
    "breakout", "display", "sensor", "board", "development", "featherwing",
    "shield", "bonnet", "hat", "wing", "led", "audio", "button", "keypad",
    "encoder", "rgb", "matrix", "oled", "tft", "prototyping", "stemma",
)


def _github_headers() -> dict[str, str]:
    """Headers for the GitHub API, with a token when one is configured.

    Unauthenticated, the API allows 60 requests an hour per address -- plenty
    for one person, since a refresh costs two, but a hosted instance behind a
    shared address can run out. `GITHUB_TOKEN` raises that to 5,000. It is only
    ever sent to api.github.com, never to the raw file host or to Adafruit.
    """
    h = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _fetch(url: str, timeout: float = 120.0,
           headers: Optional[dict[str, str]] = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class CadIndexTruncated(ValueError):
    """GitHub cut the tree listing short, so some folders are missing."""


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------

@dataclass
class CadFile:
    """One file in the CAD repo, as the git tree listed it."""

    name: str
    url: str
    size: int = 0
    #: the repo folder it lives in
    folder: str = ""

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class CatalogEntry:
    """One product, as much as a search result needs to know."""

    id: str
    name: str
    vendor: str = "Adafruit"
    url: str = ""
    image: Optional[str] = None
    category: str = ""
    #: folder in the CAD repo, if the vendor published a model -- the one an
    #: import uses: the first folder holding a format we can measure, else the
    #: first by name. All of them are in `cad_folders`.
    cad: Optional[str] = None
    #: every CAD folder for this product. Usually one; six product IDs have
    #: two (1586, 1833, 1982, 2590, 3010, 4222), revisions of the same board.
    cad_folders: list[str] = field(default_factory=list)
    #: model formats across those folders, e.g. ["f3d", "step", "stl"]
    formats: list[str] = field(default_factory=list)
    #: the feed's `discontinue_status`, read as "do not design around this"
    discontinued: bool = False
    #: the feed's `product_stock`; None when the feed did not say
    in_stock: Optional[bool] = None
    #: id in our own part library, if somebody has already measured this
    part_id: Optional[str] = None
    score: float = 0.0
    #: folder -> its files, from the index. Downloads come from here; a search
    #: result leaves it out, because nobody choosing a board needs the sizes
    #: of 40 files to do it.
    cad_files: dict[str, list[CadFile]] = field(default_factory=dict,
                                                repr=False)

    @property
    def importable(self) -> bool:
        """Can we produce real geometry, or only a name and a picture?

        A model in a format the importer cannot read is no better than no
        model, so this asks about formats, not about whether a folder exists.
        """
        return any(f in IMPORTABLE_FORMATS for f in self.formats)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("cad_files")
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
        discontinued=_discontinued(p),
        in_stock=_in_stock(p),
    )


def _discontinued(p: dict) -> bool:
    """The feed says "Discontinued", "Pending", "Requested" or nothing.

    Only the first means the board is gone. "Pending" products are mostly
    still on sale (70 of them read "in stock"), so flagging them would warn
    people off boards they can buy.
    """
    return str(p.get("discontinue_status") or "").strip().lower() == "discontinued"


def _in_stock(p: dict) -> Optional[bool]:
    """`product_stock` is "in stock" or a count, where zero and below is none.

    The negative counts (-1, -3, -10) are the shop's codes for back-ordered or
    retired stock; none of them means you can order one today.
    """
    raw = p.get("product_stock")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text == "in stock":
        return True
    try:
        return int(text) > 0
    except ValueError:
        return None


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

    if entry.importable:                      # a model we can read, not just any
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

#: What a refresh can fail with that means "the network or the vendor", as
#: opposed to a bug here. A malformed body (ValueError, which JSONDecodeError
#: is) counts: half a JSON file is what a dropped connection looks like.
_FETCH_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


def _product_id(folder: str) -> Optional[str]:
    """The product ID a CAD folder is for: "4741 1.5-inch OLED" -> "4741"."""
    head = folder.split(" ")[0]
    return head if head.isdigit() else None


def parse_tree(tree: dict) -> dict:
    """The cached CAD index, from one `/git/trees/<sha>?recursive=1` response.

    Folder -> files with sizes. Every top-level folder is kept, even one with no
    model in it, so the index says what the repository holds rather than what
    we can use today; what is usable is decided when entries are built.

    A truncated listing is kept -- most of an index is better than none -- but
    marked, and `Catalog` reports it as an error rather than serving it as if
    it were whole. GitHub truncates past 100,000 entries or 7 MB; the repo is
    at about 2,300, so this is a tripwire, not an expectation.
    """
    folders: dict[str, list[dict]] = {}
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if item.get("type") == "tree" and "/" not in path:
            folders.setdefault(path, [])
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if item.get("type") != "blob" or "/" not in path:
            continue                          # README, LICENSE, .DS_Store
        folder, name = path.split("/", 1)
        folders.setdefault(folder, []).append(
            {"name": name, "size": int(item.get("size") or 0)})
    for files in folders.values():
        files.sort(key=lambda f: f["name"].lower())
    return {
        "repo": CAD_REPO,
        "truncated": bool(tree.get("truncated")),
        "entries": len(tree.get("tree", [])),
        "folders": dict(sorted(folders.items())),
    }


def _formats(names: list[str]) -> list[str]:
    return sorted({MODEL_FORMATS[s] for s in (Path(n).suffix.lower()
                                              for n in names)
                   if s in MODEL_FORMATS})


@dataclass
class Catalog:
    """Cached product and CAD feeds, re-checked against the TTL on request.

    There is no timer. `shared()` asks `due()` on every request, and the first
    request after the TTL lapses refreshes both feeds -- so a server left
    running for a month serves this week's catalogue, not the one it started
    with.
    """

    cache_dir: Path = CACHE_DIR
    ttl: float = TTL
    products: list[CatalogEntry] = field(default_factory=list)
    #: the CAD repo, folder -> files, at `commit`
    cad_index: dict[str, list[CadFile]] = field(default_factory=dict)
    #: the commit of the CAD repo the index was built from and files come from
    commit: Optional[str] = None
    #: GitHub cut the tree listing short; some folders may be missing
    truncated: bool = False
    fetched_at: float = 0.0
    stale: bool = False
    error: Optional[str] = None
    #: when the last refresh failed; requests answer from cache until RETRY
    #: has passed, instead of each one waiting on the same timeout
    failed_at: float = 0.0
    #: whether the cache has been read at all in this process
    read: bool = False

    # -- disk -------------------------------------------------------------
    @property
    def products_path(self) -> Path:
        return self.cache_dir / "adafruit-products.json"

    @property
    def cad_path(self) -> Path:
        # A new name, not the old `adafruit-cad.json`: that one was a bare
        # {product id: folder} map with no files, no sizes and no commit, so a
        # cache from before the tree index is simply not read, and its absence
        # here makes the next request refresh.
        return self.cache_dir / "adafruit-cad-tree.json"

    def age(self) -> Optional[float]:
        if not self.fetched_at:
            return None
        return time.time() - self.fetched_at

    def fresh(self) -> bool:
        """Is the cache on disk younger than the TTL, CAD index included?"""
        return (self.products_path.exists() and self.cad_path.exists()
                and (time.time() - self.products_path.stat().st_mtime)
                < self.ttl)

    def due(self) -> bool:
        """Does this request have to load -- read the cache or refresh it?

        Asked on every request. The answer is yes the first time (nothing is
        read yet) and whenever the cache has outlived the TTL, except within
        RETRY of a failed refresh: then the vendor is down, the cache is what
        we have, and asking again on every keystroke only makes searching slow.
        """
        if not self.read:
            return True
        if self.failed_at and time.time() - self.failed_at < RETRY:
            return False
        return not self.fresh()

    # -- loading ----------------------------------------------------------
    def load(self, refresh: bool = False) -> "Catalog":
        """Fill from cache, refreshing from the network when it is old.

        Never raises for a network problem. A search box that empties itself
        because someone else's server is down is worse than one showing
        yesterday's catalogue, so a failed refresh downgrades to `stale` and
        keeps the old data.
        """
        self.error = None
        self.stale = False

        if refresh or not self.fresh():
            try:
                self._refresh()
            except _FETCH_ERRORS as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                self.stale = True
            if self.stale:
                self.failed_at = time.time()
            else:
                self.failed_at = 0.0

        self._read_cache()
        if not self.products:
            self.stale = False                # nothing old is being shown
        return self

    def _refresh(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        raw = _fetch(PRODUCTS_URL)
        json.loads(raw)                       # refuse to cache a truncated body
        self.products_path.write_bytes(raw)

        # The CAD index is a nicety, not a requirement: without it every
        # product simply looks un-importable, which is wrong but not broken.
        # A failure keeps the previous index and says so.
        try:
            index = self._fetch_cad_index()
        except _FETCH_ERRORS as exc:
            self.error = f"CAD index unavailable ({type(exc).__name__}: {exc})"
            self.stale = True
            return
        self.cad_path.write_text(json.dumps(index, indent=1),
                                 encoding="utf-8")

    def _fetch_cad_index(self) -> dict:
        """Resolve `main` to a commit, then list that commit's whole tree.

        Two requests per refresh, against 60 an hour unauthenticated. Pinning
        first means the index, and every file later downloaded through it,
        describe one commit even if Adafruit push in between.
        """
        sha = _fetch(CAD_COMMIT_URL, timeout=60,
                     headers={**_github_headers(),
                              "Accept": "application/vnd.github.sha"})
        sha = sha.decode("ascii", "replace").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"GitHub did not answer with a commit SHA: "
                             f"{sha[:60]!r}")
        tree = json.loads(_fetch(CAD_TREE_URL.format(sha=sha), timeout=60,
                                 headers=_github_headers()))
        index = parse_tree(tree)
        index["commit"] = sha
        index["fetched_at"] = time.time()
        return index

    def _read_cad_index(self) -> None:
        self.cad_index, self.commit, self.truncated = {}, None, False
        if not self.cad_path.exists():
            return
        try:
            raw = json.loads(self.cad_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.error = self.error or f"cached CAD index is corrupt: {exc}"
            return
        self.commit = raw.get("commit") or None
        self.truncated = bool(raw.get("truncated"))
        repo = raw.get("repo") or CAD_REPO
        ref = self.commit or "main"
        for folder, files in (raw.get("folders") or {}).items():
            self.cad_index[folder] = [
                CadFile(name=f["name"], size=int(f.get("size") or 0),
                        folder=folder,
                        url=CAD_RAW_URL.format(
                            repo=repo, ref=ref,
                            path=urllib.parse.quote(f"{folder}/{f['name']}")))
                for f in files]
        if self.truncated and not self.error:
            # Loud, and on every load, not only the one that fetched it: a
            # product silently missing its model looks exactly like a product
            # Adafruit never modelled.
            self.error = (f"CAD index truncated by GitHub at commit "
                          f"{self.commit}: some folders are missing")

    def folders_for(self, product_id: str) -> list[str]:
        """Every CAD folder for one product, the one an import uses first."""
        pid = str(product_id).strip()
        hits = [f for f in self.cad_index if _product_id(f) == pid]
        return sorted(hits, key=lambda f: (
            not any(fmt in IMPORTABLE_FORMATS
                    for fmt in _formats([c.name for c in self.cad_index[f]])),
            f))

    def _attach_cad(self, entries: list[CatalogEntry]) -> None:
        by_id: dict[str, list[str]] = {}
        for folder in self.cad_index:
            pid = _product_id(folder)
            if pid:
                by_id.setdefault(pid, []).append(folder)
        for e in entries:
            if e.id not in by_id:
                continue
            e.cad_folders = self.folders_for(e.id)
            e.cad = e.cad_folders[0]
            e.cad_files = {f: list(self.cad_index[f]) for f in e.cad_folders}
            e.formats = _formats([c.name for f in e.cad_folders
                                  for c in self.cad_index[f]])

    def _read_cache(self) -> None:
        self.read = True
        self._read_cad_index()

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
            if e is not None:
                out.append(e)
        self._attach_cad(out)
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
            "importable": sum(1 for e in self.products if e.importable),
            "cad_folders": len(self.cad_index),
            "cad_commit": self.commit,
            "cad_truncated": self.truncated,
            "fetched_at": self.fetched_at or None,
            "age_seconds": round(age) if age is not None else None,
            "stale": self.stale,
            "error": self.error,
        }


#: One shared instance -- the feeds are big and identical for every caller.
_shared: Optional[Catalog] = None
#: FastAPI runs plain `def` routes on a thread pool, so two searches can arrive
#: together just as the TTL lapses; one refresh is enough.
_lock = threading.Lock()


def shared(refresh: bool = False) -> Catalog:
    """The process's catalogue, re-checked against the TTL on every call."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = Catalog()
        if refresh or _shared.due():
            _shared.load(refresh=refresh)
    return _shared


def search(query: str, limit: int = 25,
           known: Optional[dict[str, str]] = None) -> list[CatalogEntry]:
    return shared().search(query, limit=limit, known=known)
