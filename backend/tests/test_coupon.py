"""The test coupon: what a maker cuts before the real job, at the scene's
kerf and joint clearance."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from hwcase import api, coupon
from hwcase.case import kerf_compensated
from hwcase.fasteners import fastener
from hwcase.schema import CaseSpec, Material, Scene


def _holes(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    return [r for p in polys for r in p.interiors]


@pytest.mark.parametrize("t", [2.0, 3.0, 5.0, 6.0])
def test_each_material_gets_a_square_an_m3_hole_a_tslot_and_a_finger_pair(t):
    m = Material(name=f"ply-{t:g}", thickness=t, kerf=0.2, joint_clearance=0.15)
    a, b, c, d = coupon.coupon_parts(m)
    assert [p.name for p in (a, b, c, d)] == [
        "coupon-1A", "coupon-1B", "coupon-1C", "coupon-1D"]
    sizes = sorted((round(x1 - x0, 3), round(y1 - y0, 3))
                   for x0, y0, x1, y1 in (Polygon(r).bounds
                                          for r in _holes(a.outline)))
    assert (coupon.SQUARE, coupon.SQUARE) in sizes
    m3 = fastener("M3").clearance_d
    assert any(abs(w - m3) < 0.02 and abs(h - m3) < 0.02 for w, h in sizes)
    # every piece is labelled, and the label sits on the piece
    for p in (a, b, c, d):
        assert p.engrave is not None and p.outline.contains(p.engrave)
        assert p.material is m


def test_the_square_lands_on_size_once_the_kerf_is_taken():
    """Drawn nominal; compensated, the hole is a kerf smaller -- which the
    beam then takes back out, so the cut square measures 20.00."""
    m = Material(kerf=0.3)
    a = coupon.coupon_parts(m)[0]
    comp = kerf_compensated(a.outline, m.kerf)
    widths = [Polygon(r).bounds for r in _holes(comp)]
    assert any(abs((x1 - x0) - (coupon.SQUARE - m.kerf)) < 1e-6
               for x0, _y0, x1, _y1 in widths)


def test_the_joints_are_cut_at_the_materials_clearance():
    """A's tab slots are the tab (two sheets wide) plus the clearance, by a
    sheet plus the clearance -- the same rule `joinery` cuts a case with."""
    m = Material(thickness=3.0, joint_clearance=0.25)
    a, b, _c, _d = coupon.coupon_parts(m)
    sizes = [Polygon(r).bounds for r in _holes(a.outline)]
    slots = [(x1 - x0, y1 - y0) for x0, y0, x1, y1 in sizes
             if abs((x1 - x0) - 6.25) < 1e-6]
    assert slots and all(h == pytest.approx(3.25) for _w, h in slots)
    other = coupon.coupon_parts(Material(joint_clearance=0.05))
    assert not other[1].outline.equals(b.outline)


def test_the_cut_list_names_the_values_and_says_what_to_measure():
    mats = [Material(), Material(name="acrylic-5mm", thickness=5.0, kerf=0.12,
                                 joint_clearance=0.05)]
    files = coupon.coupon_files(mats, "svg", CaseSpec())
    names = [n for n, _ in files]
    assert names[-1] == "coupon-cutlist.txt"
    for _n, c in files[:-1]:
        ET.fromstring(c)
    cut = files[-1][1]
    assert "kerf 0.15 mm, joint clearance 0.10 mm" in cut
    assert "kerf 0.12 mm, joint clearance 0.05 mm" in cut
    assert "measure the square cut-out: it should be 20.00 mm" in cut
    assert "M3 x " in cut
    body = "".join(c for _n, c in files[:-1])
    assert 'id="coupon-1A"' in body and 'id="coupon-2D-outline"' in body


def test_the_same_material_twice_is_one_coupon():
    files = coupon.coupon_files([Material(), Material()], "svg", CaseSpec())
    assert "coupon-2" not in files[-1][1]


def _scene(**case) -> dict:
    return Scene.model_validate({"name": "my case", "placements": [],
                                 "case": case}).model_dump(mode="json")


def test_the_coupon_route_answers_in_both_shapes():
    client = TestClient(api.app)
    scene = _scene(materials=[{"name": "birch-3mm", "thickness": 3.0,
                               "kerf": 0.18, "joint_clearance": 0.12}])
    r = client.post("/api/export/coupon/svg?as_json=true", json=scene)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["sheets"][-1]["name"] == "my-case-coupon-cutlist.txt"
    assert "kerf 0.18 mm, joint clearance 0.12 mm" in got["sheets"][-1]["content"]
    r = client.post("/api/export/coupon/dxf", json=scene)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert client.post("/api/export/coupon/png", json=scene).status_code == 400


def test_a_frame_coupon_is_cut_from_the_frames_own_sheet():
    scene = Scene.model_validate({
        "name": "f", "kind": "rack",
        "rack": {"hp": 42, "style": "laser",
                 "material": {"name": "ply-4mm", "thickness": 4.0,
                              "kerf": 0.22}}}).model_dump(mode="json")
    r = TestClient(api.app).post("/api/export/coupon/svg?as_json=true",
                                 json=scene)
    assert r.status_code == 200, r.text
    cut = r.json()["sheets"][-1]["content"]
    assert "ply-4mm, 4 mm, kerf 0.22 mm" in cut
    assert "coupon-2" not in cut
