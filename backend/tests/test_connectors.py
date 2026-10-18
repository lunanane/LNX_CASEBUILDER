"""M3: an import is reviewed before it is saved, and proposes connectors."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwcase import api, ingest
from hwcase.catalog import CatalogEntry
from hwcase.connectors import EDGE_GAP, SIGNATURES, propose
from hwcase.library import PartLibrary
from hwcase.measure import Body
from hwcase.scene import check, resolve
from hwcase.schema import Part, Scene

CAD = Path(__file__).resolve().parents[2] / "vendor" / "cad" / "adafruit"
OLED_STEP = CAD / "4741" / "4741 1.5-inch OLED.step"
OLED_STL = CAD / "4741" / "4741 1.5-inch OLED.stl"
OLED_NAME = "Grayscale 1.5\" 128x128 OLED Graphic Display - STEMMA QT / Qwiic"

vendored = pytest.mark.skipif(not OLED_STEP.exists(),
                              reason="vendored Adafruit CAD is not on disk")

STEMMA = next(s for s in SIGNATURES if s.type == "stemma_qt")


def _body(x0, y0, sx, sy, h=2.96):
    return Body(x0, x0 + sx, y0, y0 + sy, -h, 0.0)


# --------------------------------------------------------------------------
# the signatures
# --------------------------------------------------------------------------


@vendored
def test_row1_the_oled_proposes_two_stemma_qt_on_opposite_edges():
    draft = ingest.draft_part(CatalogEntry(id="4741", name=OLED_NAME),
                              cad=OLED_STEP)
    conns = {c["name"]: c for c in draft.part["connectors"]}
    assert set(conns) == {"stemma_qt_left", "stemma_qt_right"}
    assert conns["stemma_qt_left"]["face"] == "-x"
    assert conns["stemma_qt_right"]["face"] == "+x"
    width = draft.part["outline"]["points"] and max(
        p[0] for p in draft.part["outline"]["points"])
    assert conns["stemma_qt_left"]["at"][0] < EDGE_GAP
    assert conns["stemma_qt_right"]["at"][0] > width - EDGE_GAP
    for c in conns.values():
        assert c["src"]["confidence"] == "estimated"
        assert "PROPOSED" in c["src"]["note"]
        assert "product name agrees" in c["src"]["note"]
    Part.model_validate(draft.part)
    # what a person measured by hand says the same
    lib = PartLibrary.load()
    hand = {c.face.value for c in lib["adafruit-4741-oled-1v5"].connectors
            if c.type == "stemma_qt"}
    assert hand == {"-x", "+x"}


def test_row2_a_body_away_from_every_edge_is_never_a_connector():
    middle = _body(20.0, 20.0, 4.95, 6.0)          # a perfect STEMMA, inland
    assert propose([middle], 60.0, 60.0, "STEMMA QT board") == []
    at_edge = _body(0.3, 20.0, 4.95, 6.0)
    assert [c["face"] for c in propose([at_edge], 60.0, 60.0)] == ["-x"]


@pytest.mark.parametrize("edge,expect", [
    ("left", "-x"), ("right", "+x"), ("bottom", "-y"), ("top", "+y")])
def test_the_face_is_the_edge_the_body_sits_at(edge, expect):
    W = D = 40.0
    x0, y0 = {"left": (0.2, 15.0), "right": (W - 0.2 - 4.95, 15.0),
              "bottom": (15.0, 0.2), "top": (15.0, D - 0.2 - 6.0)}[edge]
    (c,) = propose([_body(x0, y0, 4.95, 6.0)], W, D)
    assert c["face"] == expect


def test_a_capacitor_is_not_a_connector():
    cap = _body(0.2, 10.0, 3.2, 1.6, h=1.6)
    assert propose([cap], 40.0, 40.0) == []


def test_the_name_lets_a_slightly_off_body_through():
    """1.2 tolerances off: refused blind, accepted when the name says so."""
    off = _body(0.2, 10.0, 4.95 + 1.2 * STEMMA.tol, 6.0)
    assert propose([off], 40.0, 40.0, "plain breakout") == []
    assert len(propose([off], 40.0, 40.0, "board with STEMMA QT")) == 1


def test_names_count_per_edge():
    two_left = [_body(0.2, 5.0, 4.95, 6.0), _body(0.2, 20.0, 4.95, 6.0)]
    names = [c["name"] for c in propose(two_left, 40.0, 40.0)]
    assert names == ["stemma_qt_left", "stemma_qt_left_2"]


@vendored
def test_a_model_without_connector_bodies_proposes_none():
    """The 5752's vendor model leaves its STEMMA QT sockets out entirely, so
    there is nothing to recognise -- an honest empty list, not a guess."""
    step = CAD / "5752" / "5752 Quad Rotary Breakout.step"
    draft = ingest.draft_part(CatalogEntry(id="5752", name="Quad encoder "
                                           "STEMMA QT"), cad=step)
    assert draft.part["connectors"] == []


@vendored
def test_row5_proposed_connectors_produce_unverified_issues():
    """The issues pane keeps asking until somebody confirms them."""
    part = Part.model_validate(ingest.draft_part(
        CatalogEntry(id="4741", name=OLED_NAME), cad=OLED_STEP).part)
    assert part.min_confidence().value == "estimated"
    scene = Scene(name="t", parts=[part],
                  placements=[{"id": "oled", "part": part.id}])
    lib = api._lib_for(scene)                  # scene-carried parts, as hosted
    issues = check(resolve(scene, lib), lib)
    unverified = [i for i in issues if i.code == "unverified"]
    assert unverified and part.id in unverified[0].message
    assert "estimated" in unverified[0].message


# --------------------------------------------------------------------------
# the review: preview, file choice, turning over
# --------------------------------------------------------------------------


@vendored
def test_row3_turning_over_by_hand_beats_the_heuristic_and_is_noted():
    entry = CatalogEntry(id="4741", name="x")
    guessed = ingest.draft_part(entry, cad=OLED_STEP)
    assert guessed.flipped is True
    kept = ingest.draft_part(entry, cad=OLED_STEP, flip=False)
    assert kept.flipped is False
    assert any("by hand" in w and "would have turned it over" in w
               for w in kept.warnings)
    assert "by hand" in kept.part["notes"]


@pytest.fixture
def oled_in_catalogue(monkeypatch):
    from hwcase import catalog as cat

    entry = CatalogEntry(id="4741", name=OLED_NAME, cad="4741 1.5-inch OLED",
                         formats=["step", "stl"])

    class Shared:
        def get(self, pid):
            return entry if pid == "4741" else None

    monkeypatch.setattr(cat, "shared", lambda refresh=False: Shared())
    monkeypatch.setattr(ingest, "local_cad", lambda pid: [OLED_STL, OLED_STEP])
    return entry


@vendored
def test_row4_the_preview_offers_every_model_and_measures_the_chosen_one(
        oled_in_catalogue):
    first = api.catalog_preview("4741", download=False)
    assert first["files"] == [OLED_STL.name, OLED_STEP.name]
    assert first["file"] == OLED_STEP.name             # STEP preferred
    assert {c["name"] for c in first["connectors"]} == {"stemma_qt_left",
                                                        "stemma_qt_right"}
    chosen = api.catalog_preview("4741", download=False, file=OLED_STL.name,
                                 flip=False)
    assert chosen["file"] == OLED_STL.name and chosen["flipped"] is False


@vendored
def test_a_file_that_is_not_the_products_is_refused(oled_in_catalogue):
    with pytest.raises(api.HTTPException) as err:
        api.catalog_preview("4741", download=False, file="nope.step")
    assert err.value.status_code == 404
