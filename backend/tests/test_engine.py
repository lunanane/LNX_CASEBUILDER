"""Guards on the things a wrong answer would quietly cost a sheet of plywood."""

from pathlib import Path

import pytest

from hwcase import PartLibrary, build, check, load_scene, resolve
from hwcase.case import kerf_compensated
from hwcase.export import case_to_json, scene_to_json, to_svg
from hwcase.schema import (Box, CaseSpec, Material, Panel, Part, Placement,
                           RectOutline, Scene, VolumeKind)

BACKEND = Path(__file__).resolve().parent.parent
SCENE = BACKEND / "scenes" / "soundmachine-v0.yaml"


@pytest.fixture(scope="module")
def lib():
    return PartLibrary.load()


@pytest.fixture(scope="module")
def demo(lib):
    return resolve(load_scene(SCENE), lib)


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------

def test_library_loads(lib):
    assert len(lib) >= 8
    assert "rpi-3b" in lib


def test_every_part_has_a_sane_envelope(lib):
    for part in lib:
        lo, hi = part.z_extent()
        assert hi > lo, f"{part.id} has no height"
        assert hi - lo < 200, f"{part.id} is implausibly tall"


def test_pi_matches_the_official_drawing(lib):
    pi = lib["rpi-3b"]
    assert pi.outline.size == (85.0, 56.0)
    holes = sorted(h.at for h in pi.holes)
    assert holes == [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)]
    header = next(v for v in pi.volumes if v.name == "gpio_header")
    assert header.at == (32.5, 52.5)          # HAT mechanical spec
    assert header.size == (50.8, 5.08)        # 2x20 on 2.54


def test_quad_encoder_is_the_strip_not_the_square(lib):
    """Shop pages say 25.6 x 25.3; the vendor CAD says otherwise. Regression
    guard so nobody 'fixes' this back to the wrong number."""
    enc = lib["adafruit-5752-quad-encoder"]
    assert enc.outline.size == (76.2, 21.59)


# --------------------------------------------------------------------------
# mates
# --------------------------------------------------------------------------

def test_mate_stacks_the_screen_onto_the_header(lib):
    scene = load_scene(SCENE)
    gap = next(p for p in scene.placements if p.id == "screen").mate_gap
    res = resolve(scene, lib)
    pi, screen = lib["rpi-3b"], lib["hyperpixel4-square-touch"]
    pi_mate = next(m for m in pi.mates if m.name == "gpio40")
    # the screen's board bottom sits exactly gap above the Pi's mate plane
    assert res.frames["screen"].pos[2] == pytest.approx(pi_mate.at[2] + gap)


def test_mate_gap_moves_the_whole_stack(lib):
    scene = load_scene(SCENE)
    base = resolve(scene, lib).frames["screen"].pos[2]
    next(p for p in scene.placements if p.id == "screen").mate_gap += 5.0
    assert resolve(scene, lib).frames["screen"].pos[2] == pytest.approx(base + 5.0)


def test_children_follow_their_parent(lib):
    """Moving a Trellis must carry its silicone pad with it.

    Deliberately relative to wherever the scene currently puts the board -- the
    scene file is edited by hand and by the editor, so a hard-coded start
    position would break every time someone drags something.
    """
    scene = load_scene(SCENE)
    trellis = next(p for p in scene.placements if p.id == "trellis_a")
    before = resolve(scene, lib).frames["pad_a"].pos
    trellis.pos = (trellis.pos[0] + 130.0, trellis.pos[1] - 7.0, trellis.pos[2])
    after = resolve(scene, lib).frames["pad_a"].pos
    assert after[0] == pytest.approx(before[0] + 130.0)
    assert after[1] == pytest.approx(before[1] - 7.0)
    assert after[2] == pytest.approx(before[2])


def test_attachment_cycles_are_rejected(lib):
    scene = Scene(placements=[
        Placement(id="a", part="rpi-3b", parent="b", parent_mate="gpio40", mate="gpio40"),
        Placement(id="b", part="rpi-3b", parent="a", parent_mate="gpio40", mate="gpio40"),
    ])
    with pytest.raises(ValueError, match="cycle"):
        resolve(scene, lib)


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

def test_panel_is_derived_from_the_screen(demo):
    assert demo.panels["main"] == pytest.approx(25.4)


def test_everything_on_the_panel_lands_flush(demo):
    z = demo.panels["main"]
    flush = {"screen.active_area", "pad_a.buttons", "pad_b.buttons",
             "encoders.bushings", "oled.active_area", "amy.panel_face"}
    seen = set()
    for s in demo.solids:
        if s.ref in flush:
            seen.add(s.ref)
            assert s.z[1] == pytest.approx(z, abs=1e-6), f"{s.ref} is not flush"
    assert seen == flush


def test_encoder_shafts_stand_proud_for_the_knobs(demo):
    shafts = next(s for s in demo.solids if s.ref == "encoders.shafts")
    assert shafts.z[1] > demo.panels["main"] + 5.0


def test_panel_follows_when_the_screen_moves(lib):
    """The whole point of deriving the panel: change the header stack and every
    keypad, knob and window follows."""
    scene = load_scene(SCENE)
    before = resolve(scene, lib)
    next(p for p in scene.placements if p.id == "screen").mate_gap += 4.0
    after = resolve(scene, lib)
    assert after.panels["main"] == pytest.approx(before.panels["main"] + 4.0)
    for pid in ("trellis_a", "encoders", "oled", "amy"):
        assert after.frames[pid].pos[2] == pytest.approx(before.frames[pid].pos[2] + 4.0)


def test_missing_panel_is_an_error(lib):
    scene = load_scene(SCENE)
    next(p for p in scene.placements if p.id == "oled").on_panel = "nope"
    codes = [i.code for i in resolve(scene, lib).issues]
    assert "panel_missing" in codes


def test_recessed_actuator_is_reported(lib):
    scene = load_scene(SCENE)
    enc = next(p for p in scene.placements if p.id == "encoders")
    enc.panel_offset = -12.0          # sink the collar well below the surface
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "recessed" for i in issues)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def test_the_demo_scene_is_clean(demo, lib):
    errors = [i for i in check(demo, lib) if i.level == "error"]
    assert errors == [], f"demo scene regressed: {[i.message for i in errors]}"


def test_overlapping_parts_collide(lib):
    scene = load_scene(SCENE)
    # drop the hub straight on top of a keypad
    trellis = next(p for p in scene.placements if p.id == "trellis_a")
    next(p for p in scene.placements if p.id == "mux").pos = trellis.pos
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "collision" for i in issues)


def test_blocking_a_port_is_an_error(lib):
    scene = load_scene(SCENE)
    # park the hub right in front of the Pi's HDMI plug run
    next(p for p in scene.placements if p.id == "mux").pos = (-40.0, 20.0, 0.0)
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "connector_blocked" and "hdmi" in i.message for i in issues)


def test_burying_a_display_is_an_error(lib):
    scene = load_scene(SCENE)
    mux = next(p for p in scene.placements if p.id == "mux")
    mux.pos = (-40.0, -30.0, 30.0)    # hovering directly over the screen
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "obstructed" and "screen.active_area" in i.message
               for i in issues)


def test_unverified_parts_are_flagged_once_each(demo, lib):
    unverified = [i for i in check(demo, lib) if i.code == "unverified"]
    assert unverified, "the library is all estimates -- this should warn"
    assert len(unverified) == len({i.message for i in unverified})


# --------------------------------------------------------------------------
# case + export
# --------------------------------------------------------------------------

def test_layers_span_the_whole_stack(demo):
    model = build(demo)
    assert model.layers
    assert model.layers[0].role == "floor"
    assert model.layers[-1].role == "lid"
    assert model.layers[0].z0 == pytest.approx(model.z0)
    total = sum(l.thickness for l in model.layers)
    assert total == pytest.approx(model.height)
    for a, b in zip(model.layers, model.layers[1:]):
        assert b.z0 == pytest.approx(a.z1), "gap or overlap between layers"


def test_layer_count_follows_material_thickness(demo):
    thin = build(demo, CaseSpec(materials=[Material(name="ply-3", thickness=3.0)]))
    thick = build(demo, CaseSpec(materials=[Material(name="ply-6", thickness=6.0)]))
    assert len(thin.layers) > len(thick.layers)


def test_the_floor_stays_solid_and_the_panel_is_pierced(demo):
    model = build(demo)
    floor = model.layers[0]
    panel_z = demo.panels["main"]
    at_panel = min(model.layers, key=lambda l: abs((l.z0 + l.z1) / 2 - panel_z))
    assert floor.geom.area > at_panel.geom.area, \
        "the layer at the panel should have more cut out of it than the floor"


def test_kerf_grows_the_cut_outline(demo):
    layer = build(demo).layers[5]
    assert kerf_compensated(layer.geom, 0.2).area > layer.geom.area
    assert kerf_compensated(layer.geom, 0.0).area == pytest.approx(layer.geom.area)


def test_svg_is_wellformed_and_dimensioned(demo):
    svg = to_svg(build(demo))
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "mm" in svg.split("\n")[0]
    assert svg.count("<path") > 10


def test_json_payloads_carry_what_the_browser_needs(demo, lib):
    payload = scene_to_json(demo, lib, check(demo, lib))
    assert payload["panels"][0]["z"] == pytest.approx(25.4)
    solid = payload["solids"][0]
    assert set(solid) >= {"placement", "kind", "bounds", "outline", "confidence"}
    assert len(solid["bounds"]) == 6
    conn = payload["connectors"][0]
    assert set(conn) >= {"ref", "at", "normal", "external", "reach"}

    case = case_to_json(build(demo))
    assert case["layers"][0]["rings"]
    assert sum(case["bom"].values()) == len(case["layers"])


# --------------------------------------------------------------------------
# geometry conventions
# --------------------------------------------------------------------------

def test_rotation_moves_the_footprint_not_just_the_label(lib):
    part = Part(id="t", name="t", outline=RectOutline(size=(40.0, 10.0), origin="min"),
                volumes=[Box(name="b", at=(20.0, 5.0), size=(40.0, 10.0), z=(0.0, 2.0))])
    small = PartLibrary([part])
    flat = resolve(Scene(placements=[Placement(id="p", part="t")]), small)
    turned = resolve(Scene(placements=[Placement(id="p", part="t", rot_z=90.0)]), small)
    fx0, fy0, fx1, fy1 = flat.solids[0].poly.bounds
    tx0, ty0, tx1, ty1 = turned.solids[0].poly.bounds
    assert (fx1 - fx0) == pytest.approx(ty1 - ty0)
    assert (fy1 - fy0) == pytest.approx(tx1 - tx0)


def test_flip_mirrors_z_about_the_board_plane(lib):
    part = Part(id="t", name="t", outline=RectOutline(size=(10.0, 10.0)),
                pcb_thickness=1.6,
                volumes=[Box(name="tall", at=(0.0, 0.0), size=(5.0, 5.0), z=(1.6, 11.6))])
    small = PartLibrary([part])
    up = resolve(Scene(placements=[Placement(id="p", part="t")]), small)
    down = resolve(Scene(placements=[Placement(id="p", part="t", flip=True)]), small)
    tall_up = next(s for s in up.solids if s.name == "tall")
    tall_down = next(s for s in down.solids if s.name == "tall")
    assert tall_up.z == pytest.approx((1.6, 11.6))
    assert tall_down.z == pytest.approx((-11.6, -1.6))


# --------------------------------------------------------------------------
# the editor's rotate-about-centre formula
# --------------------------------------------------------------------------

def _footprint_center(res, pid):
    xs, ys = [], []
    for s in res.solids:
        if s.placement == pid:
            x0, y0, x1, y1 = s.poly.bounds
            xs += [x0, x1]
            ys += [y0, y1]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


@pytest.mark.parametrize("deg", [90.0, -90.0, 37.5, 180.0])
def test_rotating_about_the_centre_keeps_the_centre_put(lib, deg):
    """The editor's rotate gizmo turns a part about its own centre, which
    `rot_z` alone does not do -- it spins about the local origin, a corner for
    every `origin: min` part. The editor compensates by swinging `pos` around
    the same centre:  pos' = C + Rz(d) * (pos - C).  If that formula is wrong,
    grabbing the ring flings the board across the bench.
    """
    import math

    scene = load_scene(SCENE)
    pl = next(p for p in scene.placements if p.id == "encoders")
    before = _footprint_center(resolve(scene, lib), "encoders")

    a = math.radians(deg)
    dx, dy = pl.pos[0] - before[0], pl.pos[1] - before[1]
    pl.pos = (before[0] + dx * math.cos(a) - dy * math.sin(a),
              before[1] + dx * math.sin(a) + dy * math.cos(a),
              pl.pos[2])
    pl.rot_z = (pl.rot_z + deg) % 360

    after = _footprint_center(resolve(scene, lib), "encoders")
    assert after[0] == pytest.approx(before[0], abs=1e-6)
    assert after[1] == pytest.approx(before[1], abs=1e-6)


# --------------------------------------------------------------------------
# saving a scene without eating its comments
# --------------------------------------------------------------------------

def test_saving_keeps_the_comments(tmp_path, lib):
    """The editor's save button once flattened the scene file and threw away
    every comment in it. The reasoning in those comments is most of the value
    of the file, so saving merges into the existing document instead."""
    from hwcase import scenefile

    path = tmp_path / "s.yaml"
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert before.count("#") > 5

    scene = load_scene(path)
    moved = next(p for p in scene.placements if p.id == "trellis_a")
    moved.pos = (moved.pos[0] + 25.0, moved.pos[1], moved.pos[2])
    scenefile.save(scene, path)

    after = path.read_text(encoding="utf-8")
    for line in before.splitlines():
        if line.strip().startswith("#"):
            assert line.strip() in after, f"lost comment: {line.strip()}"
    assert load_scene(path).placements[
        [p.id for p in scene.placements].index("trellis_a")].pos[0] == pytest.approx(moved.pos[0])


def test_saving_is_idempotent(tmp_path):
    from hwcase import scenefile

    path = tmp_path / "s.yaml"
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    scenefile.save(load_scene(path), path)
    once = path.read_text(encoding="utf-8")
    scenefile.save(load_scene(path), path)
    assert path.read_text(encoding="utf-8") == once


def test_saving_tracks_added_and_removed_placements(tmp_path, lib):
    from hwcase import scenefile
    from hwcase.schema import Placement

    path = tmp_path / "s.yaml"
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    scene = load_scene(path)

    scene.placements = [p for p in scene.placements if p.id not in ("oled",)]
    scene.placements.append(Placement(id="extra", part="adafruit-4741-oled-1v5",
                                      pos=(300.0, 0.0, 0.0)))
    scenefile.save(scene, path)

    ids = [p.id for p in load_scene(path).placements]
    assert "oled" not in ids
    assert "extra" in ids
    resolve(load_scene(path), lib)          # and it is still a valid scene


def test_saving_keeps_vectors_on_one_line(tmp_path):
    from hwcase import scenefile

    path = tmp_path / "s.yaml"
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    scenefile.save(load_scene(path), path)
    text = path.read_text(encoding="utf-8")
    assert "pos: [" in text, "coordinates should stay inline, not explode over 3 lines"
