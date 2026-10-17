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


def test_a_failed_cad_index_is_retried_after_retry_not_after_the_ttl(
        tmp_path, vendor, monkeypatch):
    """The product feed is written before GitHub is asked, so a CAD failure
    leaves a fresh feed beside an old index. The index's own age must count,
    or the old one is served for a whole TTL."""
    cache = tmp_path / "catalog"
    monkeypatch.setattr(cat, "_shared", cat.Catalog(cache_dir=cache, ttl=3600))
    cat.search("oled")
    _backdate(cache, 2 * 3600)

    real_fetch = vendor.__call__

    def github_down(url, timeout=120.0, headers=None):
        if url != cat.PRODUCTS_URL:
            vendor.calls.append((url, dict(headers or {})))
            raise OSError("rate limited")
        return real_fetch(url, timeout, headers)

    monkeypatch.setattr(cat, "_fetch", github_down)
    vendor.sha = SHA2
    cat.search("oled")                                  # feed ok, index fails
    assert cat.shared().stale and "CAD index unavailable" in cat.shared().error
    assert cat.shared().commit == SHA                   # old index kept

    now = [time.time()]
    monkeypatch.setattr(cat.time, "time", lambda: now[0])

    n = len(vendor.calls)
    now[0] += cat.RETRY - 1                             # within RETRY: no retry
    cat.search("oled")
    assert len(vendor.calls) == n

    monkeypatch.setattr(cat, "_fetch", vendor)          # GitHub is back
    now[0] += 2                                         # RETRY has passed
    cat.search("oled")
    urls = [u for u, _ in vendor.calls[n:]]
    assert cat.CAD_COMMIT_URL in urls                   # the index is retried
    assert cat.PRODUCTS_URL not in urls                 # the fresh feed is not
    assert cat.shared().commit == SHA2
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

def test_a_step_only_product_is_importable_since_m2(loaded):
    """M1 held this back until STEP could be read; M2 reads it."""
    e = loaded.get("562")
    assert e.cad == "562 10K Potentiometer"
    assert e.formats == ["f3d", "step"]
    assert e.importable is True
    assert e.as_dict()["importable"] is True
    assert loaded.get("5752").importable is True        # .stp + .stl
    assert loaded.get("1234").importable is False       # no CAD at all


def test_a_folder_of_only_fusion_files_is_not_importable():
    fusion = cat.CatalogEntry(id="9", name="x", cad="9 x",
                              formats=["3mf", "f3d"])
    assert fusion.importable is False


def test_an_unreadable_model_does_not_rank_as_importable(loaded):
    """The ranking bonus is for geometry we can produce, not for a folder."""
    step_only = loaded.search("potentiometer")[0]
    assert step_only.id == "562"
    plain = cat.CatalogEntry(id="562", name=step_only.name,
                             category=step_only.category)
    fusion_only = cat.CatalogEntry(id="562", name=step_only.name,
                                   category=step_only.category, cad="562 x",
                                   formats=["3mf", "f3d"])
    terms = ["potentiometer"]
    assert cat.score(step_only, terms) > cat.score(plain, terms)   # STEP reads
    assert cat.score(fusion_only, terms) == cat.score(plain, terms)


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


# ---------------------------------------------------------------------------
# downloads: from the index, under a cap (acceptance 4)
# ---------------------------------------------------------------------------

class FakeRaw:
    """Stands in for `ingest._get`: the raw file host."""

    def __init__(self, body=b"solid x\nendsolid x\n"):
        self.body = body
        self.calls: list[tuple[str, object]] = []

    def __call__(self, url, timeout=60.0, limit=None):
        self.calls.append((url, limit))
        assert not url.startswith("https://api.github.com/"), url
        body = self.body(url) if callable(self.body) else self.body
        return body if limit is None else body[:limit + 1]


@pytest.fixture
def raw(monkeypatch):
    from hwcase import ingest

    fake = FakeRaw()
    monkeypatch.setattr(ingest, "_get", fake)
    monkeypatch.delenv("HWCASE_MAX_CAD_MB", raising=False)
    return fake


def test_downloads_use_the_indexed_file_list(loaded, raw, vendor, tmp_path):
    """No per-import API call: the index already says what is in the folder,
    and files come from the raw host at the pinned commit."""
    from hwcase import ingest

    before = len(vendor.calls)
    got = ingest.download_cad(loaded.get("4741"), dest_root=tmp_path / "cad")
    assert len(vendor.calls) == before                  # no GitHub API at all
    assert sorted(p.name for p in got) == ["4741 1.5-inch OLED.step",
                                           "4741 1.5-inch OLED.stl"]
    assert all(p.parent == tmp_path / "cad" / "4741" for p in got)
    for url, limit in raw.calls:
        assert f"/Adafruit_CAD_Parts/{SHA}/4741%201.5-inch%20OLED/" in url
        assert limit == 64 * 1024 * 1024                # the default cap
    assert len(raw.calls) == 2                          # .f3d and .jpg skipped

    raw.calls.clear()
    ingest.download_cad(loaded.get("4741"), dest_root=tmp_path / "cad")
    assert raw.calls == []                              # already on disk


def test_a_products_second_folder_downloads_beside_the_first(loaded, raw,
                                                              tmp_path):
    from hwcase import ingest

    e = loaded.get("1982")
    first = ingest.download_cad(e, dest_root=tmp_path)
    second = ingest.download_cad(e, dest_root=tmp_path, folder="1982 MPR121")
    assert {p.parent.name for p in first} == {"1982"}
    assert {p.parent.name for p in second} == {"1982 MPR121"}


def test_a_folder_not_in_the_index_is_a_named_error(loaded, raw, tmp_path):
    from hwcase import ingest

    with pytest.raises(ValueError, match="not in the catalogue's index"):
        ingest.download_cad(loaded.get("4741"), dest_root=tmp_path,
                            folder="4741 Something Else")
    bare = cat.CatalogEntry(id="1", name="no cad")
    with pytest.raises(ValueError, match="no vendor CAD"):
        ingest.download_cad(bare, dest_root=tmp_path)


def test_a_download_over_the_cap_fails_naming_the_file_and_the_cap(
        loaded, raw, tmp_path, monkeypatch):
    """Checked against the indexed size before a single byte is fetched, so
    an over-size folder never leaves half of itself on disk."""
    from hwcase import ingest

    monkeypatch.setenv("HWCASE_MAX_CAD_MB", "4")
    with pytest.raises(ingest.CadTooLarge) as err:
        ingest.download_cad(loaded.get("4741"), dest_root=tmp_path / "cad")
    msg = str(err.value)
    assert "4741 1.5-inch OLED/4741 1.5-inch OLED.step" in msg   # 5.1 MB
    assert "4 MB" in msg and "HWCASE_MAX_CAD_MB" in msg
    assert raw.calls == []
    assert not (tmp_path / "cad" / "4741").exists()


def test_a_body_larger_than_its_index_entry_is_stopped_at_the_cap(
        loaded, raw, tmp_path, monkeypatch):
    """An index can be wrong; the bytes are counted as well."""
    from hwcase import ingest

    monkeypatch.setenv("HWCASE_MAX_CAD_MB", "0.01")     # 10,485 bytes
    raw.body = b"x" * 20_000                            # indexed at 1-2 kB
    with pytest.raises(ingest.CadTooLarge, match=r"5752 Quad Rotary Breakout"
                       r"/5752 Quad Rotary Breakout\.st[lp] came down larger"
                       r".*0\.01 MB download cap"):
        ingest.download_cad(loaded.get("5752"), dest_root=tmp_path)
    assert all(limit == int(0.01 * 2**20) for _, limit in raw.calls)
    assert not any(tmp_path.rglob("*.st*"))             # nothing half-written


@pytest.mark.parametrize("bad", ["lots", "0", "-5"])
def test_a_cap_that_is_not_a_positive_number_is_refused(monkeypatch, bad):
    from hwcase import ingest

    monkeypatch.setenv("HWCASE_MAX_CAD_MB", bad)
    with pytest.raises(ValueError, match="HWCASE_MAX_CAD_MB"):
        ingest.max_cad_bytes()


# ---------------------------------------------------------------------------
# the import survey
# ---------------------------------------------------------------------------

def _survey():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tools" / "import_survey.py"
    spec = importlib.util.spec_from_file_location("import_survey", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_survey_sample_is_deterministic(loaded):
    survey = _survey()
    a = survey.sample_folders(loaded.cad_index, 5, seed=1)
    assert a == survey.sample_folders(loaded.cad_index, 5, seed=1)
    assert a != survey.sample_folders(loaded.cad_index, 5, seed=2)
    assert len(a) == 5 and set(a) <= set(loaded.cad_index)
    assert survey.sample_folders(loaded.cad_index, None, 1) == \
        sorted(loaded.cad_index)


def test_a_full_survey_never_writes_over_the_committed_baseline():
    survey = _survey()
    assert survey.default_out(False) == survey.BASELINE
    assert survey.default_out(True) != survey.BASELINE
    assert survey.default_out(True).parent.name == "out"     # gitignored


def test_the_survey_records_an_unreadable_folder_without_downloading(
        tmp_path, monkeypatch, raw):
    """A folder of only Fusion files: nothing to read, so nothing to fetch."""
    fake = FakeVendor(folders={"8888 Fusion Only": [("8888.f3d", 10),
                                                    ("8888.3mf", 10)]})
    monkeypatch.setattr(cat, "_fetch", fake)
    c = cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()

    row = _survey().survey_one(c, "8888 Fusion Only")

    assert row["result"] == "failed"
    assert row["reason"] == "no readable model (3mf f3d)"
    assert row["product_id"] == "8888" and row["commit"] == SHA
    assert raw.calls == []


def test_the_survey_tries_folders_the_feed_no_longer_lists(tmp_path, monkeypatch):
    fake = FakeVendor(folders={"7777 Retired Board": [("7777.stl", 10)]})
    monkeypatch.setattr(cat, "_fetch", fake)
    c = cat.Catalog(cache_dir=tmp_path / "c", ttl=3600).load()
    assert c.get("7777") is None
    e = c.cad_entry("7777 Retired Board")
    assert e.id == "7777" and e.name == "Retired Board" and e.importable
