"""The Adafruit catalogue index: the git tree, freshness, and the feed's flags.

No test here touches the network. `catalog._fetch` is replaced by `FakeVendor`,
which answers the three URLs a refresh asks for -- the product feed, `main`
resolved to a commit, and that commit's tree -- from fixtures shaped like the
real responses (checked against the live API on 2026-10-17: 2,287 entries, 478
top-level folders, flat folders of files, not truncated).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from hwcase import catalog as cat

SHA = "c0ffee" + "0" * 34
SHA2 = "beef" + "1" * 36

#: The six product IDs that really do have two CAD folders, with the real
#: folder names. A dict keyed by product ID kept one of each; all twelve must
#: survive.
DUPLICATES = {
    "1586": ("1586 24x NeoRing", "1586 NeoPixel Ring 24x"),
    "1833": ("1833 micro USB Breakout", "1833 microUSB breakout"),
    "1982": ("1982 MPR121 Stemma Breakout", "1982 MPR121"),
    "2590": ("2590 Metro Mini V2", "2590 Metro Mini"),
    "3010": ("3010 Adafruit Feather M0 WiFi", "3010 Feather M0 Wifi"),
    "4222": ("4222 NeoPixel Triple-Ring Board", "4222 NeoPixel Triple-Ring"),
}


def _tree(folders: dict[str, list[tuple[str, int]]], sha: str = SHA,
          truncated: bool = False) -> dict:
    """A `/git/trees/<sha>?recursive=1` response, as GitHub shapes it."""
    tree = [{"path": ".DS_Store", "type": "blob", "size": 18436},
            {"path": "README.md", "type": "blob", "size": 300}]
    for folder, files in folders.items():
        tree.append({"path": folder, "type": "tree"})
        for name, size in files:
            tree.append({"path": f"{folder}/{name}", "type": "blob",
                         "size": size})
    return {"sha": "tree" + sha[4:], "truncated": truncated, "tree": tree}


def _folders() -> dict[str, list[tuple[str, int]]]:
    out = {
        "4741 1.5-inch OLED": [("4741 1.5-inch OLED.stl", 2_400_000),
                               ("4741 1.5-inch OLED.step", 5_100_000),
                               ("4741 1.5-inch OLED.f3d", 900_000),
                               ("4741 render.jpg", 80_000)],
        # STEP-only: 158 of the real 478 folders look like this
        "562 10K Potentiometer": [("562 10K Potentiometer.step", 700_000),
                                  ("562 10K Potentiometer.f3d", 300_000)],
        "5752 Quad Rotary Breakout": [("5752 Quad Rotary Breakout.stp", 1_000),
                                      ("5752 Quad Rotary Breakout.stl", 2_000)],
    }
    for pid, (a, b) in DUPLICATES.items():
        # the second folder of each pair is made STEP only, and for some
        # pairs it sorts first by name -- so "first by name" alone would pick
        # the folder the importer cannot read
        out[a] = [(f"{a}.stl", 1_000), (f"{a}.step", 2_000)]
        out[b] = [(f"{b}.step", 3_000)]
    return out


def _product(pid, name, status=None, stock="in stock"):
    return {"product_id": int(pid), "product_name": name,
            "product_master_category": "Breakouts",
            "discontinue_status": status, "product_stock": stock}


PRODUCTS = [
    _product(4741, 'Grayscale 1.5" 128x128 OLED Display'),
    _product(562, "Panel Mount 10K potentiometer"),
    _product(5752, "I2C Quad Rotary Encoder Breakout", stock="0"),
    _product(1234, "Hook-up Wire Spool Set", status="Discontinued", stock="-3"),
    _product(9999, "Pending Board", status="Pending", stock="12"),
] + [_product(pid, f"Duplicate Board {pid}") for pid in DUPLICATES]


class FakeVendor:
    """Stands in for `catalog._fetch`: Adafruit's feed and GitHub's API."""

    def __init__(self, products=PRODUCTS, folders=None, sha=SHA,
                 truncated=False):
        self.products = products
        self.folders = _folders() if folders is None else folders
        self.sha = sha
        self.truncated = truncated
        self.calls: list[tuple[str, dict]] = []
        self.down = False

    def __call__(self, url, timeout=120.0, headers=None):
        self.calls.append((url, dict(headers or {})))
        if self.down:
            raise OSError("connection refused")
        if url == cat.PRODUCTS_URL:
            return json.dumps(self.products).encode()
        if url == cat.CAD_COMMIT_URL:
            assert (headers or {}).get("Accept") == "application/vnd.github.sha"
            return self.sha.encode()
        if url == cat.CAD_TREE_URL.format(sha=self.sha):
            return json.dumps(_tree(self.folders, self.sha,
                                    self.truncated)).encode()
        raise AssertionError(f"unexpected fetch: {url}")


def _backdate(cache: Path, seconds: float) -> None:
    t = time.time() - seconds
    for p in cache.iterdir():
        os.utime(p, (t, t))


@pytest.fixture
def vendor(monkeypatch):
    fake = FakeVendor()
    monkeypatch.setattr(cat, "_fetch", fake)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return fake


@pytest.fixture
def loaded(tmp_path, vendor):
    return cat.Catalog(cache_dir=tmp_path / "catalog", ttl=3600).load()


# ---------------------------------------------------------------------------
# acceptance (1): every folder of a product is indexed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pid", sorted(DUPLICATES))
def test_two_folders_for_one_product_id_are_both_indexed(loaded, pid):
    e = loaded.get(pid)
    assert sorted(e.cad_folders) == sorted(DUPLICATES[pid])
    assert set(e.cad_files) == set(DUPLICATES[pid])
    # the import uses the folder it can measure, whatever the names sort to
    assert e.cad == DUPLICATES[pid][0]
    assert e.as_dict()["cad_folders"] == e.cad_folders


def test_the_index_holds_every_folder_and_file_with_sizes(loaded):
    """Every top-level folder, every file in it, sizes as GitHub gave them --
    including folders no product in the feed points at."""
    assert len(loaded.cad_index) == len(_folders())
    files = {f.name: f for f in loaded.cad_index["4741 1.5-inch OLED"]}
    assert files["4741 1.5-inch OLED.stl"].size == 2_400_000
    assert "4741 render.jpg" in files                 # indexed, not a model
    assert loaded.get("4741").formats == ["f3d", "step", "stl"]
    assert ".DS_Store" not in loaded.cad_index        # top-level files skipped


def test_the_index_is_pinned_to_a_commit(loaded, vendor):
    """Files come from the raw host at the commit the index was built from,
    so an import is reproducible and cannot mix two days' files."""
    assert loaded.commit == SHA
    assert loaded.status()["cad_commit"] == SHA
    stl = loaded.get("4741").cad_files["4741 1.5-inch OLED"][0]
    assert stl.url.startswith(
        f"https://raw.githubusercontent.com/adafruit/Adafruit_CAD_Parts/{SHA}/")
    assert "4741%201.5-inch%20OLED/" in stl.url
    assert [u for u, _ in vendor.calls].count(
        cat.CAD_TREE_URL.format(sha=SHA)) == 1        # one tree request


# ---------------------------------------------------------------------------
# acceptance (2): a long-running process refreshes when the TTL lapses
# ---------------------------------------------------------------------------

def test_a_stale_cache_refreshes_on_the_next_search_in_the_same_process(
        tmp_path, vendor, monkeypatch):
    cache = tmp_path / "catalog"
    monkeypatch.setattr(cat, "_shared", cat.Catalog(cache_dir=cache, ttl=3600))

    assert [e.id for e in cat.search("oled")] == ["4741"]
    assert cat.search("brand new") == []

    # a week passes; Adafruit ship a board and push a commit
    vendor.products = PRODUCTS + [_product(6000, "Brand New Sensor")]
    vendor.sha = SHA2
    _backdate(cache, 2 * 3600)

    assert [e.id for e in cat.search("brand new")] == ["6000"]
    assert cat.shared().commit == SHA2


def test_a_fresh_cache_is_not_refetched_per_request(tmp_path, vendor,
                                                    monkeypatch):
    monkeypatch.setattr(cat, "_shared",
                        cat.Catalog(cache_dir=tmp_path / "c", ttl=3600))
    cat.search("oled")
    n = len(vendor.calls)
    for _ in range(5):
        cat.search("oled")
    assert len(vendor.calls) == n


def test_a_failed_refresh_is_not_retried_on_every_request(tmp_path, vendor,
                                                          monkeypatch):
    """Checking the TTL per request must not turn a vendor outage into a
    timeout per keystroke."""
    cache = tmp_path / "catalog"
    monkeypatch.setattr(cat, "_shared", cat.Catalog(cache_dir=cache, ttl=3600))
    cat.search("oled")
    _backdate(cache, 2 * 3600)
    vendor.down = True

    n = len(vendor.calls)
    assert [e.id for e in cat.search("oled")] == ["4741"]   # from cache
    assert cat.shared().stale is True
    assert len(vendor.calls) == n + 1
    cat.search("oled")
    cat.search("oled")
    assert len(vendor.calls) == n + 1

    # once RETRY has passed it tries again, and recovers
    vendor.down = False
    cat.shared().failed_at -= cat.RETRY + 1
    cat.search("oled")
    assert cat.shared().stale is False and cat.shared().error is None


def test_a_cache_from_before_the_tree_index_forces_a_refresh(tmp_path, vendor):
    """The old `adafruit-cad.json` had no files and no commit; it is not read,
    and its successor's absence makes the cache count as stale."""
    cache = tmp_path / "catalog"
    cache.mkdir()
    (cache / "adafruit-products.json").write_text(json.dumps(PRODUCTS),
                                                  encoding="utf-8")
    (cache / "adafruit-cad.json").write_text('{"4741": "4741 OLED"}',
                                             encoding="utf-8")
    c = cat.Catalog(cache_dir=cache, ttl=3600)
    assert not c.fresh()
    c.load()
    assert c.commit == SHA and c.get("4741").importable


# ---------------------------------------------------------------------------
# acceptance (3): importable means "a format we can measure"
# ---------------------------------------------------------------------------

def test_a_step_only_product_is_not_importable(loaded):
    e = loaded.get("562")
    assert e.cad == "562 10K Potentiometer"             # it has CAD...
    assert e.formats == ["f3d", "step"]
    assert e.importable is False                        # ...but none we read
    assert e.as_dict()["importable"] is False
    assert loaded.get("5752").importable is True        # .stp + .stl
    assert loaded.get("1234").importable is False       # no CAD at all


def test_an_unreadable_model_does_not_rank_as_importable(loaded):
    """The ranking bonus is for geometry we can produce, not for a folder."""
    step_only = loaded.search("potentiometer")[0]
    assert step_only.id == "562"
    plain = cat.CatalogEntry(id="562", name=step_only.name,
                             category=step_only.category)
    assert cat.score(step_only, ["potentiometer"]) == \
        cat.score(plain, ["potentiometer"])


# ---------------------------------------------------------------------------
# acceptance (5): discontinued and in-stock ride along in search results
# ---------------------------------------------------------------------------

def test_search_results_carry_discontinued_and_in_stock(loaded):
    rows = {e.id: e.as_dict() for e in loaded.search("board")
            + loaded.search("wire") + loaded.search("oled")
            + loaded.search("quad")}
    assert rows["1234"]["discontinued"] is True
    assert rows["1234"]["in_stock"] is False            # "-3"
    assert rows["4741"]["discontinued"] is False
    assert rows["4741"]["in_stock"] is True             # "in stock"
    assert rows["5752"]["in_stock"] is False            # "0"
    # "Pending" is not gone: 70 such products read "in stock" in the real feed
    assert rows["9999"]["discontinued"] is False
    assert rows["9999"]["in_stock"] is True             # a positive count
    for row in rows.values():
        assert "cad_files" not in row                   # not a search concern


def test_in_stock_is_unknown_when_the_feed_is_silent():
    assert cat._in_stock({}) is None
    assert cat._in_stock({"product_stock": "call us"}) is None


# ---------------------------------------------------------------------------
# GitHub specifics
# ---------------------------------------------------------------------------

def test_a_truncated_tree_is_reported_loudly(tmp_path, monkeypatch):
    fake = FakeVendor(truncated=True)
    monkeypatch.setattr(cat, "_fetch", fake)
    c = cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    assert c.truncated is True
    assert c.status()["cad_truncated"] is True
    assert "truncated" in c.error
    assert c.get("4741").importable                     # what came is kept

    # and on every later load from cache, not just the one that fetched it
    again = cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    assert "truncated" in again.error


def test_github_token_is_sent_to_the_api_and_nowhere_else(tmp_path, vendor,
                                                          monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    for url, headers in vendor.calls:
        sent = headers.get("Authorization")
        if url.startswith("https://api.github.com/"):
            assert sent == "Bearer ghp_example"
        else:
            assert sent is None, url


def test_no_token_means_no_authorization_header(tmp_path, vendor):
    cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    assert all("Authorization" not in h for _, h in vendor.calls)


def test_a_failed_cad_index_keeps_the_previous_one(tmp_path, vendor,
                                                   monkeypatch):
    cache = tmp_path / "c"
    cat.Catalog(cache_dir=cache, ttl=3600).load()

    def tree_down(url, timeout=120.0, headers=None):
        if url == cat.PRODUCTS_URL:
            return json.dumps(PRODUCTS).encode()
        raise OSError("rate limited")

    monkeypatch.setattr(cat, "_fetch", tree_down)
    c = cat.Catalog(cache_dir=cache, ttl=3600).load(refresh=True)
    assert c.stale is True and "CAD index unavailable" in c.error
    assert c.commit == SHA and c.get("4741").importable


def test_a_non_sha_answer_is_refused(tmp_path, vendor):
    vendor.sha = "<html>rate limit</html>"
    c = cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    assert "CAD index unavailable" in c.error
    assert not (tmp_path / "c" / "adafruit-cad-tree.json").exists()
