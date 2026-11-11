"""A Eurorack panel made by a PCB service: layers, artwork, Gerbers."""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
import zlib

import numpy as np
import pytest
import yaml
from pydantic import ValidationError
from shapely.geometry import LineString, Point, Polygon

from hwcase import case as case_mod
from hwcase import export, panel, pcb, pcbfab
from hwcase.api import _starter_text
from hwcase.artwork import trace
from hwcase.library import PartLibrary
from hwcase.scene import check, resolve
from hwcase.scenefile import dumps
from hwcase.schema import CaseSpec, Engraving, PanelArt, PcbFab, Scene

LIB = PartLibrary.load()

ENCODER = {"id": "enc", "part": "adafruit-5752-quad-encoder",
           "pos": [2.35, 50.0, 0.0], "on_panel": "main",
           "panel_ref": "bushings", "support": "from_lid"}


def _scene(hp=16.0, placements=(), engravings=(), artwork=(), fab="pcb",
           **pcb_opts) -> Scene:
    return Scene.model_validate({
        "name": "p",
        "panels": [{"name": "main", "z": 0.0}],
        "placements": list(placements),
        "engravings": list(engravings),
        "artwork": list(artwork),
        "case": {"materials": [{"name": "aluminium-2mm", "thickness": 2.0}],
                 "eurorack_panel": {"hp": hp, "fab": fab, "pcb": pcb_opts}},
    })


def _build(scene):
    res = resolve(scene, LIB)
    return res, case_mod.build(res)


def _png_like(img: np.ndarray) -> str:
    return "z64:" + base64.b64encode(zlib.compress(img.astype(np.uint8)
                                                   .tobytes())).decode()


def _target(n=200):
    """A grey disc with a white disc inside it, on black, anti-aliased the
    way a browser draws an image down to size."""
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5)
    cover = lambda radius: np.clip(radius - r + 0.5, 0, 1)   # noqa: E731
    return 128 * cover(n * 0.4) + 127 * cover(n * 0.2)


def _art(img, **kw):
    h, w = img.shape
    base = {"pixels": [w, h], "data": _png_like(img), "width": 20.0,
            "at": [40.0, 64.0], "thresholds": [64, 192],
            "layers": ["mask", "silk", "copper"]}
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# the shared table and the schema
# --------------------------------------------------------------------------


def test_the_palette_is_one_table_the_editor_reads_too():
    table = json.loads((pcbfab.TABLE).read_text(encoding="utf-8"))
    assert pcbfab.TABLE.parent.name == "web"
    assert table == pcbfab.palette()
    for kind in ("mask", "silk", "finish"):
        for key, row in table[kind].items():
            assert re.fullmatch(r"#[0-9a-f]{6}", row["color"]), key
    assert {"green", "black", "white", "matte-black"} <= set(table["mask"])
    assert set(table["silk"]) == {"white", "black"}
    assert table["finish"]["enig"]["metal"] == "gold"
    assert 1.6 in table["thickness"]
    for mask in table["mask"].values():
        assert set(mask["silk"]) <= set(table["silk"])


def test_a_laser_panel_is_unchanged_and_saves_without_pcb_keys():
    ep = CaseSpec.model_validate({"eurorack_panel": {"hp": 10}}).eurorack_panel
    assert ep.fab == "laser" and ep.board_thickness == ep.thickness == 2.0
    scene = Scene.model_validate(yaml.safe_load(_starter_text("x", "panel")))
    text = dumps(scene)
    assert "fab:" not in text and "pcb:" not in text
    assert Engraving().layer == "silk"


def test_only_what_services_offer_is_accepted():
    with pytest.raises(ValidationError, match="mask a PCB service offers"):
        PcbFab(mask="pink")
    with pytest.raises(ValidationError, match="not a board thickness"):
        PcbFab(thickness=1.7)
    with pytest.raises(ValidationError):
        PcbFab(finish="osp-gold")
    assert PcbFab(mask="matte-black", finish="hasl").finish == "hasl"


def test_a_pcb_panel_is_the_boards_thickness_in_the_mask_colour():
    _res, model = _build(_scene(placements=[ENCODER]))
    lid = model.layers[0]
    assert lid.z1 == pytest.approx(0.0)
    assert lid.z1 - lid.z0 == pytest.approx(1.6)
    assert lid.material.color == pcbfab.colour("mask", "black")
    # standoffs are measured from the back of the 1.6 mm board, not 2 mm
    laser = _build(_scene(placements=[ENCODER], fab="laser"))[1].layers[0]
    gap = [float(re.search(r"standoff ([\d.]+)", n).group(1))
           for n in lid.notes if "standoff" in n]
    gap_laser = [float(re.search(r"standoff ([\d.]+)", n).group(1))
                 for n in laser.notes if "standoff" in n]
    assert gap and gap[0] == pytest.approx(gap_laser[0] + 0.4, abs=0.06)
    assert any(n.startswith("order as a PCB: 1.6 mm FR4") for n in lid.notes)


def test_the_starter_says_how_to_make_it_a_pcb():
    text = _starter_text("x", "panel")
    assert "fab: pcb" in text


# --------------------------------------------------------------------------
# layers
# --------------------------------------------------------------------------


def test_round_holes_are_drills_and_rail_slots_are_routed():
    _res, model = _build(_scene(placements=[ENCODER]))
    art = model.pcb
    assert len(art.drills) == 8                         # 4 shafts + 4 screws
    assert len(art.routes) == 1 + 4                     # outline + rail slots
    outline = Polygon(art.routes[0])
    for x, y, d in art.drills:
        assert outline.contains(Point(x, y).buffer(d / 2))
    for x, y, _s in panel.rail_holes(16.0):
        assert any(Polygon(r).contains(Point(x, y)) for r in art.routes[1:])


def test_layers_land_where_they_are_asked_for():
    eng = [{"name": "t", "pattern": "text", "text": "VCF", "at": [40, 110],
            "size": [40, 10], "text_size": 5, "stroke": 0.4},
           {"name": "g", "pattern": "rule", "at": [40, 90], "size": [50, 3],
            "stroke": 1.5, "layer": "copper"},
           {"name": "b", "pattern": "rule", "at": [40, 80], "size": [50, 3],
            "stroke": 1.5, "layer": "substrate"}]
    _res, model = _build(_scene(engravings=eng))
    art = model.pcb
    assert art.copper.area == pytest.approx(50 * 1.5, rel=0.05)
    assert art.substrate.area == pytest.approx(50 * 1.5, rel=0.05)
    assert art.mask_open.area == pytest.approx(art.copper.area
                                               + art.substrate.area, rel=1e-6)
    assert art.silk_strokes and all(w == 0.4 for _p, w in art.silk_strokes)
    assert art.silk_regions.is_empty


def test_silk_over_an_opening_is_clipped_and_said():
    eng = [{"name": "word", "pattern": "text", "text": "GOLD", "at": [40, 90],
            "size": [40, 10], "text_size": 6, "stroke": 0.5},
           {"name": "bar", "pattern": "rule", "at": [40, 90], "size": [60, 4],
            "stroke": 3.0, "layer": "copper"}]
    _res, model = _build(_scene(engravings=eng))
    art = model.pcb
    assert not art.silk_strokes                 # clipped: drawn as regions
    assert art.silk.intersection(art.mask_open).area < 1e-6
    assert "pcb_silk_clipped" in {c for _l, c, _m, _r in art.issues}


def test_copper_is_held_off_the_edges():
    eng = [{"name": "band", "pattern": "rule", "at": [40, 64], "size": [200, 3],
            "stroke": 2.0, "layer": "copper"}]
    _res, model = _build(_scene(engravings=eng))
    art = model.pcb
    assert art.board.buffer(-0.29).contains(art.copper)
    assert "pcb_copper_edge" in {c for _l, c, _m, _r in art.issues}


def test_a_ring_round_every_round_hole():
    _res, model = _build(_scene(placements=[ENCODER], ring=0.8))
    art = model.pcb
    for x, y, d in art.drills:
        probe = Point(x, y).buffer(d / 2 + 0.4).exterior
        assert art.copper.intersects(probe) and art.mask_open.intersects(probe)
    assert not art.copper.intersects(Point(*art.drills[0][:2]))
    _res, thin = _build(_scene(placements=[ENCODER], ring=0.1))
    assert "pcb_ring" in {c for _l, c, _m, _r in thin.pcb.issues}


def test_fab_limits_become_issues():
    eng = [{"name": "tiny", "pattern": "text", "text": "x", "at": [40, 100],
            "size": [20, 5], "text_size": 0.8, "stroke": 0.1}]
    res = resolve(_scene(engravings=eng, mask="white", silk="white"), LIB)
    codes = {i.code: i.level for i in check(res, LIB)}
    assert codes["pcb_silk_line"] == "warning"
    assert codes["pcb_silk_text"] == "warning"
    assert codes["pcb_silk_colour"] == "warning"
    laser = resolve(_scene(engravings=eng, fab="laser"), LIB)
    assert not [i for i in check(laser, LIB) if i.code.startswith("pcb")]


# --------------------------------------------------------------------------
# artwork
# --------------------------------------------------------------------------


def test_brightness_bands_trace_to_their_layers():
    art = PanelArt.model_validate(_art(_target()))
    layers, _dropped = trace(art)
    px = 20.0 / 200
    r_out, r_in = 80 * px, 40 * px
    assert layers["copper"].area == pytest.approx(np.pi * r_in ** 2, rel=0.03)
    assert layers["silk"].area == pytest.approx(
        np.pi * (r_out ** 2 - r_in ** 2), rel=0.03)
    assert layers["silk"].intersection(layers["copper"]).area < 1e-6
    # traced outlines follow the circle, not a staircase
    ring = layers["copper"]
    assert ring.length == pytest.approx(2 * np.pi * r_in, rel=0.03)


def test_invert_and_two_bands():
    art = PanelArt.model_validate(_art(_target(), thresholds=[64],
                                       layers=["copper", "mask"], invert=True))
    layers, _ = trace(art)
    # inverted, the white centre is the darkest band, and that is copper
    px = 20.0 / 200
    assert layers["copper"].area == pytest.approx(np.pi * (40 * px) ** 2, rel=0.03)
    assert set(layers) == {"copper"}


def test_min_feature_drops_hairlines_and_says_so():
    img = np.zeros((100, 100))
    img[:, 50] = 255                                  # 0.2 mm wide at 20 mm
    img[20:60, 10:30] = 255
    art = PanelArt.model_validate(_art(img, thresholds=[128],
                                       layers=["mask", "copper"],
                                       min_feature=0.5))
    layers, dropped = trace(art)
    assert dropped["copper"] > 1.0
    assert layers["copper"].area == pytest.approx(4 * 8, rel=0.05)


def test_blur_despeckles():
    rng = np.random.default_rng(3)
    img = np.where(rng.random((120, 120)) > 0.97, 255, 0)
    raw = PanelArt.model_validate(_art(img, thresholds=[128],
                                       layers=["mask", "silk"], min_feature=0))
    soft = PanelArt.model_validate(_art(img, thresholds=[128],
                                        layers=["mask", "silk"], blur=0.4,
                                        min_feature=0))
    assert trace(raw)[0]["silk"].area > 1.0
    assert "silk" not in trace(soft)[0] or trace(soft)[0]["silk"].area < 0.1


def test_artwork_is_clipped_clear_of_holes_and_rail_screws():
    img = np.full((80, 80), 255)
    art = _art(img, thresholds=[128], layers=["mask", "silk"], width=80.0,
               at=[40.0, 20.0], min_feature=0)
    _res, model = _build(_scene(placements=[ENCODER], artwork=[art]))
    pa = model.pcb
    lim = pcbfab.limits()
    for _x, _y, slot in panel.rail_holes(16.0):
        assert pa.silk.distance(slot) >= lim["rail_zone"] - 0.01
    assert pa.board.contains(pa.silk.buffer(-0.01))


def test_a_raster_too_big_or_damaged_is_refused():
    with pytest.raises(ValidationError, match="pixels"):
        PanelArt(pixels=(1000, 1000), data="z64:")
    with pytest.raises(ValidationError, match="bands"):
        PanelArt(pixels=(2, 2), data="b64:AAAAAA==", thresholds=[10, 20],
                 layers=["mask", "silk"])
    bad = PanelArt(pixels=(4, 4), data="b64:AAAA")
    with pytest.raises(ValueError, match="bytes of image"):
        trace(bad)


# --------------------------------------------------------------------------
# the Gerbers
# --------------------------------------------------------------------------


def _parse_gerber(text: str):
    """Enough of RS-274X to check the format: apertures, regions, strokes."""
    lines = text.strip().splitlines()
    assert "%FSLAX46Y46*%" in lines and "%MOMM*%" in lines
    assert lines[-1] == "M02*"
    defined, regions, strokes = {}, [], []
    in_region, current, pen, path = False, None, None, []
    for ln in lines:
        m = re.fullmatch(r"%ADD(\d+)C,([\d.]+)\*%", ln)
        if m:
            defined[int(m.group(1))] = float(m.group(2))
            continue
        if ln == "G36*":
            assert not in_region
            in_region, path = True, []
            continue
        if ln == "G37*":
            assert in_region and len(path) >= 4
            assert path[0] == path[-1], "region contour not closed"
            regions.append(path)
            in_region = False
            continue
        m = re.fullmatch(r"D(\d+)\*", ln)
        if m:
            current = int(m.group(1))
            assert current in defined, f"D{current} used before %ADD"
            continue
        m = re.fullmatch(r"X(-?\d+)Y(-?\d+)D0([12])\*", ln)
        if m:
            pt = (int(m.group(1)) / 1e6, int(m.group(2)) / 1e6)
            if in_region:
                if m.group(3) == "2":
                    path = [pt]
                else:
                    path.append(pt)
            else:
                assert current is not None
                if m.group(3) == "2":
                    pen = [pt]
                    strokes.append((defined[current], pen))
                else:
                    pen.append(pt)
    assert not in_region
    return defined, regions, strokes


def _package(scene):
    _res, model = _build(scene)
    return model, dict(pcb.gerber_files(model, "p"))


def test_the_package_has_the_files_a_fab_expects():
    model, files = _package(_scene(placements=[ENCODER], ring=0.6,
                                   engravings=[{"name": "t", "pattern": "text",
                                                "text": "OSC", "at": [40, 110],
                                                "size": [30, 8], "stroke": 0.3}]))
    assert set(files) == {"p.GTL", "p.GTS", "p.GTO", "p.GBL", "p.GBS",
                          "p.GKO", "p-NPTH.DRL", "p-fab-notes.txt"}
    assert "%TF.FileFunction,Copper,L1,Top*%" in files["p.GTL"]
    assert "%TF.FileFunction,Soldermask,Top*%" in files["p.GTS"]
    assert "%TF.FileFunction,Legend,Top*%" in files["p.GTO"]
    assert "%TF.FileFunction,Profile,NP*%" in files["p.GKO"]
    for name, text in files.items():
        if name.endswith((".GTL", ".GTS", ".GTO", ".GBL", ".GBS", ".GKO")):
            _parse_gerber(text)


def test_regions_with_holes_are_cleared_and_text_is_stroked():
    model, files = _package(_scene(placements=[ENCODER], ring=0.6,
                                   engravings=[{"name": "t", "pattern": "text",
                                                "text": "OSC", "at": [40, 110],
                                                "size": [30, 8], "stroke": 0.3}]))
    _d, regions, _s = _parse_gerber(files["p.GTL"])
    assert "%LPC*%" in files["p.GTL"]            # each ring's hole is cleared
    assert len(regions) == 2 * len(model.pcb.drills)
    defined, _r, strokes = _parse_gerber(files["p.GTO"])
    assert 0.3 in defined.values()
    assert len(strokes) == len(model.pcb.silk_strokes)


def test_the_outline_is_closed_and_every_drill_is_inside_it():
    model, files = _package(_scene(placements=[ENCODER]))
    _d, _r, strokes = _parse_gerber(files["p.GKO"])
    assert all(pen[0] == pen[-1] for _w, pen in strokes), "open contour"
    outline = max((Polygon(pen) for _w, pen in strokes), key=lambda p: p.area)
    assert outline.bounds == pytest.approx((0, 0, 80.9, 128.5), abs=0.01)

    drl = files["p-NPTH.DRL"].splitlines()
    assert drl[0] == "M48" and drl[-1] == "M30" and "METRIC" in drl
    tools = {m.group(1): float(m.group(2)) for ln in drl
             if (m := re.fullmatch(r"T(\d+)C([\d.]+)", ln))}
    holes, tool = [], None
    for ln in drl:
        if (m := re.fullmatch(r"T(\d+)", ln)):
            tool = m.group(1)
        elif (m := re.fullmatch(r"X(-?[\d.]+)Y(-?[\d.]+)", ln)):
            holes.append((float(m.group(1)), float(m.group(2)), tools[tool]))
    assert len(holes) == 8
    for x, y, d in holes:
        assert outline.contains(Point(x, y).buffer(d / 2))
    # the rail slots are routed contours inside the outline
    slots = [Polygon(pen) for _w, pen in strokes if Polygon(pen).area < 50]
    assert len(slots) == 4
    assert all(outline.contains(s) for s in slots)


def test_fab_notes_say_what_to_order():
    _model, files = _package(_scene(mask="white", silk="black", finish="hasl",
                                    thickness=1.2))
    notes = files["p-fab-notes.txt"]
    for needle in ("1.2 mm", "white", "black", "HASL", "order number",
                   "GKO", "NPTH"):
        assert needle in notes, needle


def test_a_laser_panel_has_no_gerbers():
    _res, model = _build(_scene(fab="laser"))
    assert getattr(model, "pcb", None) is None
    with pytest.raises(ValueError, match="not made as a PCB"):
        pcb.gerber_files(model, "p")


def test_the_preview_carries_colours_and_layers():
    eng = [{"name": "g", "pattern": "rule", "at": [40, 90], "size": [50, 3],
            "stroke": 1.5, "layer": "copper"}]
    _res, model = _build(_scene(engravings=eng, mask="blue"))
    j = export.case_to_json(model)["pcb"]
    assert j["colors"]["mask"] == pcbfab.colour("mask", "blue")
    assert j["metal"] == "gold"
    assert j["layers"]["copper"]


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from hwcase import api
    return TestClient(api.app)


def test_the_palette_route_serves_the_shared_table(client):
    r = client.get("/api/pcb/palette")
    assert r.status_code == 200
    assert r.json() == pcbfab.palette()


def test_the_pcb_export_route_returns_the_zip(client):
    scene = _scene(placements=[ENCODER]).model_dump(mode="json")
    r = client.post("/api/export/pcb", json=scene)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "p.GTL" in names and "p-NPTH.DRL" in names

    laser = _scene(fab="laser").model_dump(mode="json")
    r = client.post("/api/export/pcb", json=laser)
    assert r.status_code == 400 and "PCB" in r.json()["detail"]


def test_the_trace_route(client):
    r = client.post("/api/pcb/trace", json={"art": _art(_target())})
    assert r.status_code == 200
    body = r.json()
    assert set(body["layers"]) == {"silk", "copper"}
    assert body["area"]["copper"] > 0
    r = client.post("/api/pcb/trace", json={"art": {"pixels": [5000, 5000],
                                                    "data": "z64:"}})
    assert r.status_code == 422
    r = client.post("/api/pcb/trace", json={"art": {"pixels": [4, 4],
                                                    "data": "b64:AAAA"}})
    assert r.status_code == 400
