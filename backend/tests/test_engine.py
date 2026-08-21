"""Guards on the things a wrong answer would quietly cost a sheet of plywood."""

from pathlib import Path

import pytest
from shapely.geometry import Point

from hwcase import PartLibrary, build, check, load_scene, resolve
from hwcase.case import kerf_compensated
from hwcase.export import case_to_json, scene_to_json, to_svg
from hwcase.schema import (Box, CaseSpec, Material, Panel, Part, Placement,
                           RectOutline, Scene, VolumeKind)

BACKEND = Path(__file__).resolve().parent.parent

#: The tests' own scene. NOT backend/scenes/soundmachine-v0.yaml -- that one is
#: a live document the editor writes to, and asserting geometry against it broke
#: these tests every time a board was dragged or an option chosen.
SCENE = Path(__file__).resolve().parent / "scenes" / "fixture.yaml"

#: the real working scene, smoke-tested only
LIVE_SCENE = BACKEND / "scenes" / "soundmachine-v0.yaml"


@pytest.fixture(scope="module")
def lib():
    return PartLibrary.load()


@pytest.fixture(scope="module")
def demo(lib):
    return resolve(load_scene(SCENE), lib)


def lab(**case_kw):
    """A small scene built here rather than read from disk.

    The demo scene is a real working document -- it gets dragged around and
    saved from the editor -- so any test that asserts exact geometry has to
    bring its own, or it fails the moment someone moves a board.
    """
    from hwcase.schema import CaseSpec, Placement, Scene

    return Scene(
        name="lab",
        placements=[
            Placement(id="pi", part="rpi-3b", pos=(0.0, 0.0, 0.0), mount="manual"),
            Placement(id="hub", part="seeed-grove-tca9548a",
                      pos=(140.0, 0.0, 0.0), mount="manual"),
        ],
        case=CaseSpec(**case_kw) if case_kw else CaseSpec(),
    )


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


def _base(name):
    """`buttons[2,3]` -> `buttons`: repeat grids suffix their instances."""
    return name.split("[")[0]


def test_everything_on_the_panel_lands_flush(demo):
    """Flush means panel + whatever offset the placement asked for -- a button
    that has to be pressed is deliberately set proud."""
    z = demo.panels["main"]
    offsets = {p.id: p.panel_offset for p in demo.scene.placements}
    # the pad's buttons are fitted by their parent, so they take its offset
    offsets["pad_a"] = offsets["trellis_a"]
    offsets["pad_b"] = offsets["trellis_b"]

    flush = {"screen.active_area", "pad_a.buttons", "pad_b.buttons",
             "encoders.bushings", "oled.active_area", "amy.jack_threads"}
    seen = set()
    for s in demo.solids:
        ref = f"{s.placement}.{_base(s.name)}"
        if ref in flush:
            seen.add(ref)
            want = z + offsets.get(s.placement, 0.0)
            assert s.z[1] == pytest.approx(want, abs=1e-6), f"{s.ref} is not flush"
    assert seen == flush


def test_encoder_shafts_stand_proud_for_the_knobs(demo):
    shafts = [s for s in demo.solids
              if s.placement == "encoders" and _base(s.name) == "shafts"]
    assert len(shafts) == 4, "four encoders, four shafts"
    for s in shafts:
        assert s.z[1] > demo.panels["main"] + 5.0


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

def test_the_fixture_scene_is_clean(demo, lib):
    errors = [i for i in check(demo, lib) if i.level == "error"]
    assert errors == [], f"fixture scene regressed: {[i.message for i in errors]}"


def test_the_live_scene_still_works(lib):
    """A smoke test on the real working scene: it must load, resolve and build.

    Deliberately says nothing about *where* anything is -- that file belongs to
    whoever is using the editor, and a layout with errors in it is a design in
    progress, not a broken build.
    """
    scene = load_scene(LIVE_SCENE)
    res = resolve(scene, lib)
    check(res, lib)
    model = build(res)
    assert model.layers, "the live scene should still produce a case"


def test_overlapping_parts_collide(lib):
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    hub.pos = (0.0, 0.0, 0.0)          # straight on top of the Pi
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "collision" for i in issues)


def test_blocking_a_port_is_an_error(lib):
    scene = lab()
    hdmi = next(c for c in lib["rpi-3b"].connectors if c.name == "hdmi")
    hub = next(p for p in scene.placements if p.id == "hub")
    # park the hub squarely in the HDMI plug's run, which leaves the board at -Y
    hub.pos = (hdmi.at[0] - 45.0, -25.0, 0.0)
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "connector_blocked" and "hdmi" in i.message for i in issues),         [i.message for i in issues]


def test_burying_a_display_is_an_error(lib):
    scene = load_scene(SCENE)
    mux = next(p for p in scene.placements if p.id == "mux")
    mux.mount = "manual"              # we mean this z, not whatever auto picks
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
    flat = resolve(Scene(placements=[Placement(id="p", part="t", mount="manual")]), small)
    turned = resolve(Scene(placements=[
        Placement(id="p", part="t", rot_z=90.0, mount="manual")]), small)
    fx0, fy0, fx1, fy1 = flat.solids[0].poly.bounds
    tx0, ty0, tx1, ty1 = turned.solids[0].poly.bounds
    assert (fx1 - fx0) == pytest.approx(ty1 - ty0)
    assert (fy1 - fy0) == pytest.approx(tx1 - tx0)


def test_flip_mirrors_z_about_the_board_plane(lib):
    part = Part(id="t", name="t", outline=RectOutline(size=(10.0, 10.0)),
                pcb_thickness=1.6,
                volumes=[Box(name="tall", at=(0.0, 0.0), size=(5.0, 5.0), z=(1.6, 11.6))])
    small = PartLibrary([part])
    up = resolve(Scene(placements=[Placement(id="p", part="t", mount="manual")]), small)
    down = resolve(Scene(placements=[
        Placement(id="p", part="t", flip=True, mount="manual")]), small)
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


# --------------------------------------------------------------------------
# per-side cutout policy
# --------------------------------------------------------------------------

def _pi_side(scene, side, **kw):
    from hwcase.schema import SidePolicy
    pi = next(p for p in scene.placements if p.id == "pi")
    pi.sides = [SidePolicy(side=side, **kw)]
    return pi


def _layer_at(model, z):
    return min(model.layers, key=lambda l: abs((l.z0 + l.z1) / 2 - z))


def test_default_side_policy_matches_the_old_behaviour(lib):
    """No `sides` entry must behave exactly as before: external ports get an
    opening, internal wiring does not."""
    res = resolve(load_scene(SCENE), lib)
    for wc in res.connectors:
        assert wc.cuts_the_wall == wc.conn.external, wc.ref


def test_none_leaves_the_wall_solid(lib):
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    _pi_side(scene, Face.ny, cutout="none")
    res = resolve(scene, lib)
    ny = [c for c in res.connectors if c.placement == "pi" and c.conn.face is Face.ny]
    assert ny, "the Pi's power/HDMI/AV edge should have connectors"
    assert not any(c.cuts_the_wall for c in ny)
    # and the other sides are untouched
    assert any(c.cuts_the_wall for c in res.connectors if c.placement == "pi")


def test_include_list_selects_which_ports_count(lib):
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    _pi_side(scene, Face.ny, cutout="per_connector", include=["hdmi"])
    res = resolve(scene, lib)
    cutting = {c.conn.name for c in res.connectors
               if c.placement == "pi" and c.conn.face is Face.ny and c.cuts_the_wall}
    assert cutting == {"hdmi"}


def test_include_can_add_an_internal_port(lib):
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    _pi_side(scene, Face.px, cutout="per_connector", include=["ethernet"])
    res = resolve(scene, lib)
    eth = next(c for c in res.connectors if c.ref == "pi.ethernet")
    assert not eth.conn.external          # it is internal in the library
    assert eth.cuts_the_wall              # but the user asked for it


def test_open_to_edge_runs_the_slot_out_through_the_wall(lib):
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    plain = resolve(scene, lib)
    before = next(c for c in plain.connectors if c.ref == "pi.hdmi")

    _pi_side(scene, Face.ny, cutout="open_to_edge")
    after = next(c for c in resolve(scene, lib).connectors if c.ref == "pi.hdmi")
    assert after.corridor_poly.area > before.corridor_poly.area * 5


def _band_layer(model, opening):
    """The layer sitting in the middle of an opened band."""
    mid = (opening.z[0] + opening.z[1]) / 2
    return min(model.layers, key=lambda l: abs((l.z0 + l.z1) / 2 - mid))


def test_open_side_stops_above_the_cable(lib):
    """The point of open_side is a seamless faceplate over an open underside --
    so the panel layer must be untouched by it."""
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    _pi_side(scene, Face.ny, cutout="open_side", headroom=2.0)
    res = resolve(scene, lib)
    opening = res.side_openings[0]
    panel_z = res.panels["main"]
    assert opening.z[1] < panel_z, "the opening must stop below the faceplate"

    before = build(resolve(load_scene(SCENE), lib))
    after = build(res)
    top_before = _layer_at(before, panel_z - 1.0).geom.area
    top_after = _layer_at(after, panel_z - 1.0).geom.area
    assert top_after == pytest.approx(top_before, rel=1e-6)


def test_open_side_span_board_is_narrower_than_full(lib):
    from hwcase.schema import Face

    def area(span):
        scene = load_scene(SCENE)
        _pi_side(scene, Face.ny, cutout="open_side", span=span)
        res = resolve(scene, lib)
        model = build(res)
        return _band_layer(model, res.side_openings[0]).geom.area

    assert area("board") > area("full"), "span=board should remove less material"


def test_under_panel_leaves_the_faceplate_unbroken(lib):
    scene = load_scene(SCENE)
    oled = next(p for p in scene.placements if p.id == "oled")
    panel_z = resolve(scene, lib).panels["main"]

    before = build(resolve(scene, lib))
    oled.under_panel = True
    oled.on_panel = None
    oled.mount = "manual"
    oled.pos = (oled.pos[0], oled.pos[1], panel_z - 12.0)   # tuck it well under
    after = build(resolve(scene, lib))

    lid_before = _layer_at(before, panel_z - 1.0).geom.area
    lid_after = _layer_at(after, panel_z - 1.0).geom.area
    assert lid_after > lid_before, "the OLED window should no longer be cut"


def test_under_panel_that_does_not_fit_is_an_error(lib):
    scene = load_scene(SCENE)
    oled = next(p for p in scene.placements if p.id == "oled")
    oled.under_panel = True            # but it is still flush with the panel
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "under_panel_collision" for i in issues)


def test_side_policies_survive_a_save(tmp_path, lib):
    """The editor writes SidePolicy objects straight into the scene; the model
    forbids extra keys, so a stray field would fail the whole save."""
    from hwcase import scenefile
    from hwcase.schema import CutoutPolicy, Face, SidePolicy

    path = tmp_path / "s.yaml"
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    scene = load_scene(path)
    pi = next(p for p in scene.placements if p.id == "pi")
    pi.sides = [
        SidePolicy(side=Face.ny, cutout=CutoutPolicy.open_side, headroom=3.0, span="board"),
        SidePolicy(side=Face.px, cutout=CutoutPolicy.per_connector, include=["usb1"]),
    ]
    pi.under_panel = False
    scenefile.save(scene, path)

    back = next(p for p in load_scene(path).placements if p.id == "pi")
    assert len(back.sides) == 2
    ny = next(s for s in back.sides if s.side is Face.ny)
    assert ny.cutout is CutoutPolicy.open_side
    assert ny.headroom == pytest.approx(3.0)
    assert ny.span == "board"
    px = next(s for s in back.sides if s.side is Face.px)
    assert px.include == ["usb1"]
    resolve(load_scene(path), lib)      # and it still resolves


def test_side_opening_outline_is_clipped_for_the_browser(lib):
    """The raw region reaches 1000 mm out so it is guaranteed to cut any
    outline; what the browser gets must be trimmed to the case."""
    from hwcase.schema import CutoutPolicy, Face, SidePolicy

    scene = load_scene(SCENE)
    next(p for p in scene.placements if p.id == "pi").sides = [
        SidePolicy(side=Face.ny, cutout=CutoutPolicy.open_side)]
    res = resolve(scene, lib)
    payload = scene_to_json(res, lib, [])
    assert payload["side_openings"], "the opening should be reported"
    xs = [p[0] for p in payload["side_openings"][0]["outline"]]
    ys = [p[1] for p in payload["side_openings"][0]["outline"]]
    assert max(xs) - min(xs) < 500 and max(ys) - min(ys) < 500


# --------------------------------------------------------------------------
# repeat grids and round features
# --------------------------------------------------------------------------

def test_keypad_is_sixteen_buttons_not_one_window(demo):
    """A 55 mm square cut in the faceplate is a hole, not a keypad."""
    btns = [s for s in demo.solids
            if s.placement == "pad_a" and _base(s.name) == "buttons"]
    assert len(btns) == 16
    for s in btns:
        assert s.poly.area == pytest.approx(98.07, abs=0.3)      # 10x10, r1.5
    xs = sorted({round(s.poly.centroid.x, 3) for s in btns})
    assert len(xs) == 4
    for a, b in zip(xs, xs[1:]):
        assert b - a == pytest.approx(15.0, abs=1e-3)            # the 15 mm pitch


def test_encoder_holes_are_round_and_on_pitch(demo):
    import math

    holes = [s for s in demo.solids
             if s.placement == "encoders" and _base(s.name) == "bushings"]
    assert len(holes) == 4
    for s in holes:
        assert s.poly.area == pytest.approx(math.pi * 3.4 ** 2, rel=0.01)
    centres = sorted(s.poly.centroid.coords[0] for s in holes)
    for a, b in zip(centres, centres[1:]):
        d = math.dist(a, b)
        assert d == pytest.approx(19.05, abs=1e-3)               # 0.75"


def test_the_faceplate_gets_the_individual_holes(demo):
    """The layer at the panel should carry 16 + 16 button holes and 4 shaft
    holes, not two big rectangles."""
    model = build(demo)
    panel_z = demo.panels["main"]
    lid = min(model.layers, key=lambda l: abs((l.z0 + l.z1) / 2 - (panel_z - 1.0)))
    holes = sum(len(p.interiors) for p in
                (lid.geom.geoms if hasattr(lid.geom, "geoms") else [lid.geom]))
    assert holes >= 30, f"expected the button grid to be cut individually, got {holes}"


def test_repeat_grid_is_centred_on_at():
    from hwcase.schema import Box, Repeat

    b = Box(name="g", at=(30.0, 30.0), size=(10.0, 10.0), z=(0.0, 1.0),
            repeat=Repeat(count=(4, 4), pitch=(15.0, 15.0)))
    xs = sorted({at[0] for _, at in b.instances()})
    assert xs == [7.5, 22.5, 37.5, 52.5]
    assert len(b.instances()) == 16


def test_no_repeat_means_one_instance():
    from hwcase.schema import Box

    b = Box(name="solo", at=(1.0, 2.0), size=(3.0, 4.0), z=(0.0, 1.0))
    assert b.instances() == [("solo", (1.0, 2.0))]


# --------------------------------------------------------------------------
# corridors, overlap, and the screen's offset window
# --------------------------------------------------------------------------

def test_the_floor_has_no_holes_in_it(demo):
    """Regression: connector corridors were centred on `at`, and since the Pi's
    ports had `at` on the board surface rather than at the mouth centre, a
    15.5 mm USB corridor reached 6.35 mm *below the board* and cut the floor."""
    model = build(demo)
    floor = model.layers[0]
    assert floor.notes == [], f"the floor should be solid, got {floor.notes}"
    assert floor.geom.area == pytest.approx(model.outer.area, rel=1e-9)


def test_corridor_follows_the_socket_body(lib):
    """When a part declares a socket body, the corridor takes its z extent
    rather than guessing from the cutout height."""
    res = resolve(load_scene(SCENE), lib)
    pi = lib["rpi-3b"]
    for wc in res.connectors:
        if wc.placement != "pi" or wc.conn.body is None:
            continue
        body = next(s for s in res.solids
                    if s.placement == "pi" and s.name == wc.conn.body.name)
        assert wc.corridor_z[0] == pytest.approx(body.z[0], abs=1e-6), wc.ref
        assert wc.corridor_z[1] == pytest.approx(body.z[1], abs=1e-6), wc.ref


def test_screen_window_is_offset_towards_the_header(lib):
    """The HyperPixel's bezels are 4.5 top / 6.5 bottom, so the lit area is not
    centred on the board -- and a centred window would clip the picture."""
    screen = lib["hyperpixel4-square-touch"]
    active = next(v for v in screen.volumes if v.name == "active_area")
    board_cx, board_cy = screen.outline.size[0] / 2, screen.outline.size[1] / 2
    assert active.at[0] == pytest.approx(board_cx)          # centred in X
    assert active.at[1] == pytest.approx(board_cy + 1.0)    # 1 mm towards the header


def test_boards_may_pass_over_each_other(lib):
    """A flat board sliding under another one is allowed -- it is only a
    collision when they actually share space."""
    from hwcase.schema import Placement

    scene = lab()
    scene.placements = [p for p in scene.placements if p.id == "hub"]
    hub = scene.placements[0]
    hub.pos = (0.0, 0.0, 0.0)
    scene.placements.append(Placement(
        id="over", part="adafruit-4741-oled-1v5", mount="manual",
        pos=(10.0, 0.0, 40.0)))                   # well clear above the hub
    issues = check(resolve(scene, lib), lib)
    assert not any(i.code == "collision" for i in issues)
    assert not any(i.code == "tight_overlap" for i in issues)


def test_passing_too_close_over_is_reported(lib):
    from hwcase.schema import Placement

    scene = lab()
    scene.placements = [p for p in scene.placements if p.id == "hub"]
    scene.placements[0].pos = (0.0, 0.0, 0.0)
    hub_top = max(s.z[1] for s in resolve(scene, lib).solids)

    # Stack it by the board, not by the part's lowest point: the OLED's lowest
    # feature is a 5 mm STEMMA QT socket at one edge, and hanging the part off
    # that would leave nothing above the hub to be too close to it. The PCB
    # bottom is z = 0 in the part frame, so this is 0.4 mm of clearance
    # between the two boards -- which is the thing being warned about.
    scene.placements.append(Placement(
        id="over", part="adafruit-4741-oled-1v5", mount="manual",
        pos=(0.0, 0.0, hub_top + 0.4)))
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "tight_overlap" for i in issues), \
        "0.4 mm of clearance should be called out"
    assert not any(i.code == "collision" for i in issues), "but it is not a collision"


# --------------------------------------------------------------------------
# what the middle layers look like: interior strategies
# --------------------------------------------------------------------------

def _mid_layers(model):
    return [l for l in model.layers if l.role == "body"]


def _pieces(geom):
    return list(geom.geoms) if hasattr(geom, "geoms") else ([geom] if not geom.is_empty else [])


def _built(demo, mode, **kw):
    from hwcase.schema import Interior
    spec = demo.scene.case.model_copy(update={"interior": Interior(mode), **kw})
    return build(demo, spec)


def test_pocketed_is_still_the_default(demo):
    """The *schema* default, not whatever the demo scene happens to be set to --
    that file is edited from the editor and its interior is the user's choice."""
    from hwcase.schema import CaseSpec, Interior
    assert CaseSpec().interior is Interior.pocketed

    spec = demo.scene.case.model_copy(update={"interior": Interior.pocketed})
    a = [l.geom.area for l in _mid_layers(build(demo, spec))]
    b = [l.geom.area for l in _mid_layers(_built(demo, "pocketed"))]
    assert a == pytest.approx(b)


def test_the_strategies_order_by_how_much_material_they_leave(demo):
    def avg(mode):
        mids = _mid_layers(_built(demo, mode))
        return sum(l.geom.area for l in mids) / len(mids)

    hollow, ribs, grown, pocketed = (avg(m) for m in
                                     ("hollow", "ribs", "grown", "pocketed"))
    assert hollow < ribs < grown <= pocketed, (hollow, ribs, grown, pocketed)


def test_hollow_leaves_a_wall_all_the_way_round(demo):
    model = _built(demo, "hollow")
    wall = model.spec.wall
    for layer in _mid_layers(model):
        # eroding by slightly less than the wall must leave something;
        # eroding by more than it must not (there is no thick region left)
        assert not layer.geom.buffer(-wall * 0.45).is_empty, "the wall vanished"
        for piece in _pieces(layer.geom):
            assert piece.intersects(model.outer.boundary.buffer(1e-6)), \
                "a hollow layer should only be the outer wall and pocket edges"


def test_ribs_are_never_left_floating(demo):
    """A rib chopped free by a cable run is an offcut on the cutting bed and no
    help to the faceplate, so the pruning has to hold."""
    model = _built(demo, "ribs")
    edge = model.outer.boundary.buffer(0.05)
    for layer in _mid_layers(model):
        for piece in _pieces(layer.geom):
            assert piece.intersects(edge), \
                f"layer {layer.index} has a piece not joined to the wall"


def test_ribs_add_material_over_hollow_but_stay_off_the_hardware(demo):
    hollow = _built(demo, "hollow")
    ribbed = _built(demo, "ribs")
    for a, b in zip(_mid_layers(hollow), _mid_layers(ribbed)):
        assert b.geom.area >= a.geom.area - 1e-6, "ribs should only add material"
    # and they must not sit on top of a board
    from hwcase.geom import z_overlap
    for layer in _mid_layers(ribbed):
        slab = (layer.z0, layer.z1)
        for s in demo.solids:
            if s.kind.value == "body" and z_overlap(s.z, slab) > 0:
                overlap = layer.geom.intersection(s.poly).area
                assert overlap < s.poly.area * 0.02, \
                    f"a rib is sitting on {s.ref}"


def test_wider_ribs_leave_more_material(demo):
    thin = _built(demo, "ribs", rib_width=3.0)
    thick = _built(demo, "ribs", rib_width=12.0)
    a = sum(l.geom.area for l in _mid_layers(thin))
    b = sum(l.geom.area for l in _mid_layers(thick))
    assert b > a


def test_grown_reserves_room_for_internal_wiring(demo):
    """`pocketed` only opens up for external ports, so an I2C cable has nowhere
    to go. `grown` clears every connector, internal ones included."""
    from hwcase.geom import z_overlap

    pocketed = _built(demo, "pocketed")
    grown = _built(demo, "grown")
    internal = [c for c in demo.connectors if not c.conn.external]
    assert internal, "the scene should have internal wiring"

    improved = 0
    for layer_p, layer_g in zip(_mid_layers(pocketed), _mid_layers(grown)):
        slab = (layer_p.z0, layer_p.z1)
        for wc in internal:
            if z_overlap(wc.corridor_z, slab) <= 0:
                continue
            before = layer_p.geom.intersection(wc.corridor_poly).area
            after = layer_g.geom.intersection(wc.corridor_poly).area
            if after < before - 1e-6:
                improved += 1
    assert improved, "grown should have opened up around at least one cable run"


def test_interior_does_not_touch_the_floor_or_the_lid(demo):
    """Those two are structural faces; hollowing them out would be daft."""
    for mode in ("hollow", "ribs", "grown"):
        model = _built(demo, mode)
        base = build(demo)
        assert model.layers[0].geom.area == pytest.approx(base.layers[0].geom.area)
        assert model.layers[-1].geom.area == pytest.approx(base.layers[-1].geom.area)


# --------------------------------------------------------------------------
# auto mounting: the faceplate or the floor, never a typed-in z
# --------------------------------------------------------------------------

def test_auto_sends_anything_facing_up_to_the_faceplate(lib):
    from hwcase.scene import Mount, mount_of

    scene = load_scene(SCENE)
    for pl in scene.placements:
        pl.on_panel = None                  # let auto decide unaided
    res = resolve(scene, lib)
    panel_z = res.panels["main"]

    for pid in ("encoders", "oled", "amy", "trellis_a"):
        pl = next(p for p in scene.placements if p.id == pid)
        assert mount_of(scene, lib, pl) is Mount.panel, pid
    # and the feature really is level with the plate
    tops = {s.placement: s.z[1] for s in res.solids
            if s.kind.value in ("display", "actuator")}
    for pid in ("encoders", "oled", "amy", "pad_a"):
        assert tops[pid] >= panel_z - 1e-6


def test_auto_reads_the_whole_mate_stack(lib):
    """A NeoTrellis has nothing facing up -- the buttons belong to the pad glued
    on top of it. Judging the board alone would drop the keypad to the floor."""
    from hwcase.scene import Mount, has_top_periphery, mount_of

    scene = load_scene(SCENE)
    trellis = next(p for p in scene.placements if p.id == "trellis_a")
    trellis.on_panel = None
    assert not has_top_periphery(lib["adafruit-3954-neotrellis"])
    assert mount_of(scene, lib, trellis) is Mount.panel


def test_auto_puts_internal_boards_on_the_floor(lib):
    from hwcase.scene import Mount, mount_of

    scene = load_scene(SCENE)
    mux = next(p for p in scene.placements if p.id == "mux")
    assert mount_of(scene, lib, mux) is Mount.floor, "the hub faces nowhere"

    res = resolve(scene, lib)
    bottom = min(s.z[0] for s in res.solids if s.placement == "mux")
    assert bottom == pytest.approx(res.floor, abs=1e-6)


def test_the_floor_sits_under_the_deepest_hardware(lib):
    scene = load_scene(SCENE)
    res = resolve(scene, lib)
    deepest = min(s.z[0] for s in res.solids)
    assert res.floor == pytest.approx(deepest, abs=1e-6)


def test_an_explicit_floor_overrides_the_derived_one(lib):
    scene = load_scene(SCENE)
    scene.floor = -20.0
    res = resolve(scene, lib)
    bottom = min(s.z[0] for s in res.solids if s.placement == "mux")
    assert bottom == pytest.approx(-20.0, abs=1e-6)


def test_locked_placements_are_never_moved(lib):
    from hwcase.scene import Mount, mount_of

    scene = load_scene(SCENE)
    mux = next(p for p in scene.placements if p.id == "mux")
    mux.locked = True
    mux.pos = (mux.pos[0], mux.pos[1], 7.25)
    assert mount_of(scene, lib, mux) is Mount.manual
    assert resolve(scene, lib).frames["mux"].pos[2] == pytest.approx(7.25)


def test_a_mated_board_takes_its_height_from_the_mate(lib):
    """Not from a panel -- otherwise the screen that *defines* the panel would
    also be fitted to it, and the solve would eat its own tail."""
    from hwcase.scene import Mount, mount_of

    scene = load_scene(SCENE)
    screen = next(p for p in scene.placements if p.id == "screen")
    assert mount_of(scene, lib, screen) is Mount.manual
    assert not any(i.code == "panel_cycle" for i in resolve(scene, lib).issues)


def test_the_pi_can_be_moved(lib):
    """It used to be the scene anchor, which pinned it to the origin, and locked
    on top of that. Neither now: only its height is fixed, because the panel is
    derived from the screen mated to it."""
    scene = load_scene(SCENE)
    pi = next(p for p in scene.placements if p.id == "pi")
    assert not pi.locked
    assert scene.anchor is None

    before = resolve(scene, lib)
    pi.pos = (pi.pos[0] - 30.0, pi.pos[1] + 12.0, pi.pos[2])
    after = resolve(scene, lib)
    # the Pi moved...
    assert after.frames["pi"].pos[0] == pytest.approx(before.frames["pi"].pos[0] - 30.0)
    assert after.frames["screen"].pos[1] == pytest.approx(before.frames["screen"].pos[1] + 12.0)
    # ...and nothing else did
    for pid in ("trellis_a", "encoders", "mux", "amy"):
        assert after.frames[pid].pos[0] == pytest.approx(before.frames[pid].pos[0]), pid


def test_an_anchored_placement_is_flagged_as_pinned(lib):
    scene = load_scene(SCENE)
    scene.anchor = "mux"
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "anchor_pinned" for i in issues)


def test_manual_keeps_the_z_you_typed(lib):
    scene = load_scene(SCENE)
    mux = next(p for p in scene.placements if p.id == "mux")
    mux.mount = "manual"
    mux.pos = (mux.pos[0], mux.pos[1], 12.75)
    assert resolve(scene, lib).frames["mux"].pos[2] == pytest.approx(12.75)


def test_amyboard_has_no_hovering_slab(lib):
    """It used to be modelled panel-first, so the outline solid was the 128 mm
    acrylic panel floating 11 mm above the board with nothing under most of it."""
    amy = lib["shorepine-amyboard"]
    assert amy.outline.size == (50.5, 105.0), "the outline is the board, not the panel"
    names = [v.name for v in amy.volumes]
    assert len(names) == len(set(names)), "duplicate volume names"
    assert "pcb" not in names, "that name belongs to the solid made from the outline"

    scene = load_scene(SCENE)
    res = resolve(scene, lib)
    solids = sorted((s for s in res.solids if s.placement == "amy"), key=lambda s: s.z[0])
    for a, b in zip(solids, solids[1:]):
        assert b.z[0] <= a.z[1] + 1e-6 or any(
            o.z[0] <= b.z[0] <= o.z[1] for o in solids if o is not b), \
            f"{b.name} floats above {a.name}"


def test_duplicate_volume_names_are_rejected(lib):
    from hwcase.library import PartLibrary
    from hwcase.schema import Box, Part, RectOutline

    def make(names):
        return Part(id="x", name="x", outline=RectOutline(size=(10.0, 10.0)),
                    volumes=[Box(name=n, at=(0.0, 0.0), size=(1.0, 1.0), z=(0.0, 1.0))
                             for n in names])

    with pytest.raises(ValueError, match="two volumes"):
        PartLibrary([make(["a", "a"])])
    with pytest.raises(ValueError, match="collides"):
        PartLibrary([make(["pcb"])])


# --------------------------------------------------------------------------
# the faceplate is the top of the case
# --------------------------------------------------------------------------

def test_no_layers_above_the_faceplate(demo):
    """The case used to be sized to the tallest solid, which is the encoder
    shafts -- and those deliberately stand proud of the panel for the knobs. So
    it grew upwards to 'enclose' the knobs and drew three layers above the
    faceplate that could not exist."""
    model = build(demo)
    panel_z = demo.panels["main"]
    above = [l for l in model.layers if l.z0 >= panel_z - 1e-6]
    assert above == [], f"{len(above)} layers float above the faceplate"
    assert model.z1 == pytest.approx(panel_z, abs=1e-6)


def test_the_lid_is_the_faceplate(demo):
    model = build(demo)
    assert model.layers[-1].z1 == pytest.approx(demo.panels["main"], abs=1e-6)
    assert model.layers[-1].role == "lid"


def test_things_poking_through_do_not_grow_the_case(lib):
    """Lengthen the knob shafts and the case must not get taller."""
    scene = load_scene(SCENE)
    before = build(resolve(scene, lib)).z1

    enc = next(p for p in scene.placements if p.id == "encoders")
    enc.panel_offset = 0.0
    tall = lib["adafruit-5752-quad-encoder"].model_copy(deep=True)
    shafts = next(v for v in tall.volumes if v.name == "shafts")
    shafts.z = (shafts.z[0], shafts.z[1] + 25.0)      # much longer shafts
    from hwcase.library import PartLibrary
    bigger = PartLibrary([p for p in lib if p.id != tall.id] + [tall])

    after = build(resolve(scene, bigger)).z1
    assert after == pytest.approx(before, abs=1e-6)


def test_without_panels_the_case_still_closes_over_everything(lib):
    """No panel means no faceplate to pin to, so fall back to enclosing the lot."""
    scene = load_scene(SCENE)
    scene.panels = []
    for pl in scene.placements:
        pl.on_panel = None
        pl.mount = "manual"
    res = resolve(scene, lib)
    model = build(res)
    assert model.z1 >= res.bounds()[5] - 1e-6


def test_the_floor_keeps_its_clearance(demo):
    """Slack from rounding the sheet stack goes under the floor, never on top."""
    model = build(demo)
    deepest = min(s.z[0] for s in demo.solids)
    assert deepest - model.z0 >= demo.scene.case.floor_gap - 1e-6


# --------------------------------------------------------------------------
# a case size that stops chasing the hardware
# --------------------------------------------------------------------------

def test_freezing_the_outline_pins_the_wall(lib):
    """Derived, the outline grows with the hardware, so a connector can never
    be brought flush with the outside -- the wall runs away as you push. Frozen,
    moving a board moves it relative to the case."""
    from hwcase.case import outer_shape
    from hwcase.schema import RectOutline

    scene = lab()
    res = resolve(scene, lib)
    derived = outer_shape(res, scene.case)
    x0, y0, x1, y1 = derived.bounds

    scene.case.outline = RectOutline(
        size=(x1 - x0, y1 - y0), corner_radius=scene.case.corner_radius,
        origin="custom", origin_offset=(-x0, -y0))

    frozen = outer_shape(resolve(scene, lib), scene.case)
    assert frozen.bounds == pytest.approx(derived.bounds, abs=1e-6), \
        "freezing must not move the wall"

    # now push a board outward: the wall must stay put
    pi = next(p for p in scene.placements if p.id == "pi")
    pi.pos = (pi.pos[0] - 25.0, pi.pos[1], pi.pos[2])
    after = outer_shape(resolve(scene, lib), scene.case)
    assert after.bounds == pytest.approx(derived.bounds, abs=1e-6)


def test_a_derived_outline_does_chase_the_hardware(lib):
    """The behaviour freezing exists to escape."""
    from hwcase.case import outer_shape

    scene = lab()
    before = outer_shape(resolve(scene, lib), scene.case).bounds
    pi = next(p for p in scene.placements if p.id == "pi")
    pi.pos = (pi.pos[0] - 25.0, pi.pos[1], pi.pos[2])
    after = outer_shape(resolve(scene, lib), scene.case).bounds
    assert after[0] == pytest.approx(before[0] - 25.0)


def test_a_frozen_outline_survives_a_save(tmp_path, lib):
    from hwcase import scenefile
    from hwcase.schema import RectOutline

    path = tmp_path / "s.yaml"
    scene = lab()
    scene.case.outline = RectOutline(size=(300.0, 200.0), origin="custom",
                                     origin_offset=(-10.0, -20.0))
    scenefile.save(scene, path)
    back = load_scene(path)
    assert back.case.outline.size == (300.0, 200.0)
    assert back.case.outline.origin_offset == (-10.0, -20.0)


# --------------------------------------------------------------------------
# standing a board on edge
# --------------------------------------------------------------------------

def test_tilt_swaps_footprint_depth_for_height(lib):
    """A 90 x 20 mm hub on edge becomes 90 mm wide and ~20 mm tall."""
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    flat = next(s for s in resolve(scene, lib).solids if s.ref == "hub.pcb")
    fx0, fy0, fx1, fy1 = flat.poly.bounds

    hub.tilt = 90
    up = next(s for s in resolve(scene, lib).solids if s.ref == "hub.pcb")
    ux0, uy0, ux1, uy1 = up.poly.bounds

    assert (ux1 - ux0) == pytest.approx(fx1 - fx0), "the long edge is untouched"
    assert (uy1 - uy0) == pytest.approx(flat.z[1] - flat.z[0]), \
        "its depth becomes the board thickness"
    assert (up.z[1] - up.z[0]) == pytest.approx(fy1 - fy0), \
        "and its height becomes what used to be its depth"


def test_a_board_on_edge_takes_far_less_floor(lib):
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    flat = sum(s.poly.area for s in resolve(scene, lib).solids if s.placement == "hub")
    hub.tilt = 90
    up = sum(s.poly.area for s in resolve(scene, lib).solids if s.placement == "hub")
    assert up < flat * 0.3, "standing it up is the whole point"


def test_every_quarter_turn_is_a_box(lib):
    """Only multiples of 90 keep the 2.5D model honest, so all four must work
    and none may lose volume."""
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")

    def volume():
        out = 0.0
        for s in resolve(scene, lib).solids:
            if s.placement == "hub":
                out += s.poly.area * (s.z[1] - s.z[0])
        return out

    hub.tilt = 0
    base = volume()
    for t in (90, 180, 270):
        hub.tilt = t
        assert volume() == pytest.approx(base, rel=0.02), f"tilt {t} changed the volume"


def test_tilt_turns_the_connectors_with_the_board(lib):
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    flat = {c.ref: c.face_normal for c in resolve(scene, lib).connectors
            if c.placement == "hub"}
    hub.tilt = 90
    up = {c.ref: c.face_normal for c in resolve(scene, lib).connectors
          if c.placement == "hub"}
    # the +y ports pointed sideways when flat; on edge they point up
    ref = "hub.i2c0"
    assert flat[ref][2] == pytest.approx(0.0)
    assert up[ref][2] == pytest.approx(1.0, abs=1e-9)


def test_flip_still_means_a_half_turn(lib):
    """Old scenes say `flip: true`; it has to keep working."""
    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    hub.flip = True
    a = next(s for s in resolve(scene, lib).solids if s.ref == "hub.pcb")
    hub.flip = False
    hub.tilt = 180
    b = next(s for s in resolve(scene, lib).solids if s.ref == "hub.pcb")
    assert a.z == pytest.approx(b.z)
    assert a.poly.bounds == pytest.approx(b.poly.bounds)


def test_only_quarter_turns_are_accepted():
    from pydantic import ValidationError
    from hwcase.schema import Placement

    Placement(id="a", part="x", tilt=90)
    with pytest.raises(ValidationError):
        Placement(id="a", part="x", tilt=45)


# --------------------------------------------------------------------------
# how close the wall comes to a connector
# --------------------------------------------------------------------------

def _wall_gap(res, spec, ref, axis, sign):
    """Distance from a connector's mouth to the outside of the case."""
    from hwcase.case import outer_shape

    x0, y0, x1, y1 = outer_shape(res, spec).bounds
    edge = (x1 if sign > 0 else x0) if axis == 0 else (y1 if sign > 0 else y0)
    mouth = next(c for c in res.connectors if c.ref == ref).at[axis]
    return abs(edge - mouth)


def test_without_a_margin_the_wall_is_measured_from_the_bounding_box(lib):
    """The problem the margin exists to solve.

    The wall is offset from the bounding box of the WHOLE scene, so a port on a
    board that is not the outermost one ends up far inside -- here the hub sits
    140 mm right of the Pi, and its left-facing port is a long way from the left
    wall no matter how thin that wall is made.
    """
    scene = lab()                       # pi at x 0, hub at x 140
    res = resolve(scene, lib)
    gap = _wall_gap(res, scene.case, "hub.up", 0, -1)
    assert gap > scene.case.wall + 100.0, \
        f"expected the hub port to be buried; it is only {gap:.1f} mm in"


def test_a_margin_brings_the_wall_to_the_connector(lib):
    from hwcase.schema import Face, SidePolicy

    scene = lab()
    scene.placements = [p for p in scene.placements if p.id == "hub"]
    hub = scene.placements[0]
    hub.sides = [SidePolicy(side=Face.nx, margin=2.5, include=["up"])]
    res = resolve(scene, lib)
    assert len(res.wall_targets) == 1
    assert _wall_gap(res, scene.case, "hub.up", 0, -1) == pytest.approx(2.5, abs=1e-6)


def test_a_margin_only_moves_its_own_side(lib):
    from hwcase.case import outer_shape
    from hwcase.schema import Face, SidePolicy

    scene = lab()
    scene.placements = [p for p in scene.placements if p.id == "hub"]
    hub = scene.placements[0]
    before = outer_shape(resolve(scene, lib), scene.case).bounds
    hub.sides = [SidePolicy(side=Face.nx, margin=2.0, include=["up"])]
    after = outer_shape(resolve(scene, lib), scene.case).bounds
    assert after[0] != pytest.approx(before[0]), "the -x edge should move"
    for i in (1, 2, 3):
        assert after[i] == pytest.approx(before[i]), "and nothing else should"


def test_a_margin_never_slices_through_the_hardware(lib):
    """Ask for an impossible margin and the wall stops at the boards."""
    from hwcase.case import outer_shape
    from hwcase.schema import Face, SidePolicy

    scene = lab()
    scene.placements = [p for p in scene.placements if p.id == "hub"]
    hub = scene.placements[0]
    hub.sides = [SidePolicy(side=Face.nx, margin=-50.0, include=["up"])]
    res = resolve(scene, lib)
    x0 = outer_shape(res, scene.case).bounds[0]
    parts_x0 = res.bounds()[0]
    assert x0 <= parts_x0 + 1e-6, "the wall must stay outside the hardware"


def test_a_side_that_no_longer_faces_an_axis_is_reported(lib):
    from hwcase.schema import Face, SidePolicy

    scene = lab()
    hub = next(p for p in scene.placements if p.id == "hub")
    hub.rot_z = 37.0
    hub.sides = [SidePolicy(side=Face.nx, margin=2.0, include=["up"])]
    codes = [i.code for i in resolve(scene, lib).issues]
    assert "margin_skewed" in codes


# --------------------------------------------------------------------------
# round holes for round connectors
# --------------------------------------------------------------------------

def test_a_round_connector_cuts_a_round_hole(lib):
    import math

    amy = lib["shorepine-amyboard"]
    jack = next(c for c in amy.connectors if c.name == "spdif_in")
    assert jack.cutout_shape == "circle"

    res = resolve(load_scene(SCENE), lib)
    wc = next(c for c in res.connectors if c.ref == "amy.spdif_in")
    p = wc.corridor_poly
    roundness = 4 * math.pi * p.area / (p.length ** 2)
    assert roundness > 0.99, f"expected a circle, got roundness {roundness:.3f}"


def test_an_explicit_cutout_is_not_widened(lib):
    """A measured 6.5 mm hole must stay 6.5 mm -- it used to be clamped to 8."""
    import math

    res = resolve(load_scene(SCENE), lib)
    wc = next(c for c in res.connectors if c.ref == "amy.spdif_in")
    dia = 2 * math.sqrt(wc.corridor_poly.area / math.pi)
    assert dia == pytest.approx(6.5, abs=0.05)


def test_a_square_connector_still_cuts_a_square(lib):
    import math

    res = resolve(load_scene(SCENE), lib)
    wc = next(c for c in res.connectors if c.ref == "amy.i2c_accessories")
    p = wc.corridor_poly
    assert 4 * math.pi * p.area / (p.length ** 2) < 0.95


# --------------------------------------------------------------------------
# carrying a board on its own mounting holes
# --------------------------------------------------------------------------

def _supported(scene, mode, *ids):
    from hwcase.schema import Support
    for pid in ids:
        next(p for p in scene.placements if p.id == pid).support = Support(mode)
    return scene


def _notes(model, prefix):
    return [n for l in model.layers for n in l.notes if n.startswith(prefix)]


def test_no_support_by_default(demo):
    assert demo.supports == []
    assert _notes(build(demo), "boss") == []
    assert _notes(build(demo), "countersink") == []


def test_from_floor_posts_up_to_the_board(lib):
    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    res = resolve(scene, lib)
    model = build(res)
    holes = lib["adafruit-3954-neotrellis"].holes
    assert len(res.supports) == len(holes) == 8

    board_bottom = min(s.z[0] for s in res.solids if s.placement == "trellis_a")
    for layer in model.layers:
        bosses = [n for n in layer.notes if n.startswith("boss")]
        if layer.z0 >= board_bottom:
            assert not bosses, f"layer {layer.index} posts past the board"
        elif layer.role != "floor":
            assert len(bosses) == 8, f"layer {layer.index} is missing bosses"


def test_the_bottom_plate_is_countersunk_not_bossed(lib):
    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    model = build(resolve(scene, lib))
    floor = model.layers[0]
    assert len([n for n in floor.notes if n.startswith("countersink")]) == 8
    assert not [n for n in floor.notes if n.startswith("boss")]


def test_the_countersink_is_wider_than_the_screw(lib):
    """So a head finishes flush with the outside instead of standing proud."""
    from shapely.geometry import Polygon

    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    spec = scene.case
    res = resolve(scene, lib)
    model = build(res)

    def hole_areas(layer):
        polys = [layer.geom] if not hasattr(layer.geom, "geoms") else list(layer.geom.geoms)
        return sorted(Polygon(r).area for p in polys for r in p.interiors)

    screw = res.supports[0].screw_d + spec.screw_clearance
    assert spec.screw_head > screw
    import math
    want_csk = math.pi * (spec.screw_head / 2) ** 2
    assert any(a == pytest.approx(want_csk, rel=0.02) for a in hole_areas(model.layers[0])), \
        "the floor should carry screw-head sized holes"


def test_from_lid_says_so_when_the_board_is_inside_the_lid(lib):
    """A slab is material or void at a given XY -- it cannot be thick above a
    board and hollow below it. The OLED's PCB lands inside the 3 mm lid slab,
    so there is no lid material over its screw holes to bear on, and screwing
    down from above cannot work. Saying that is the whole point; drilling four
    holes that hold nothing is worse than drilling none."""
    scene = _supported(load_scene(SCENE), "from_lid", "oled")
    model = build(resolve(scene, lib))
    lid = model.layers[-1]
    assert not [n for n in lid.notes if n.startswith("countersink for oled")]
    refused = [n for n in lid.notes if n.startswith("cannot support oled")]
    assert len(refused) == 4
    assert "inside the lid layer" in refused[0]


def test_from_lid_drills_a_board_that_sits_below_the_lid(lib):
    """The case it is actually for: a board clear of the lid gets its four
    countersinks. This used to measure a zero overlap and drill nothing."""
    scene = _supported(load_scene(SCENE), "from_lid", "oled")
    scene.case = scene.case.model_copy(deep=True)
    scene.case.materials[-1] = scene.case.materials[-1].model_copy(
        update={"thickness": 1.0})
    model = build(resolve(scene, lib))
    lid = model.layers[-1]
    assert len([n for n in lid.notes
                if n.startswith("countersink for oled")]) == 4


def test_from_lid_does_not_post_below_the_board(lib):
    """Measured against the board's own top face, not the top of its
    components: a standoff sits at the hole, which is clear of them."""
    scene = _supported(load_scene(SCENE), "from_lid", "trellis_a")
    res = resolve(scene, lib)
    model = build(res)
    board_top = res.supports[0].board_top
    for layer in model.layers:
        if [n for n in layer.notes if n.startswith("boss")]:
            assert layer.z0 >= board_top - 1e-6, \
                f"layer {layer.index} posts down past the board"


def test_bosses_survive_hollowing(lib):
    """The whole point of adding them after the interior is carved out: a post
    that gets eaten by the void is not holding anything up."""
    from hwcase.schema import Interior

    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    res = resolve(scene, lib)
    spec = scene.case.model_copy(update={"interior": Interior.hollow})
    model = build(res, spec)

    board_bottom = min(s.z[0] for s in res.solids if s.placement == "trellis_a")
    mid = next(l for l in model.layers
               if l.role == "body" and l.z1 < board_bottom)
    for sp in res.supports:
        ring = mid.geom.intersection(
            __import__("shapely.geometry", fromlist=["Point"]).Point(sp.at)
            .buffer(spec.support_boss / 2.0))
        assert ring.area > 1.0, f"the boss at {sp.ref} was hollowed away"


def test_bosses_survive_the_board_pocket(lib):
    """A boss sits under the board, inside the very pocket cut for it."""
    from shapely.geometry import Point

    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    res = resolve(scene, lib)
    model = build(res)
    board_bottom = min(s.z[0] for s in res.solids if s.placement == "trellis_a")
    mid = next(l for l in model.layers if l.role == "body" and l.z1 < board_bottom)
    for sp in res.supports:
        disc = Point(sp.at).buffer(scene.case.support_boss / 2.0)
        assert mid.geom.intersection(disc).area > 1.0, f"pocket ate {sp.ref}"


def test_a_board_with_no_holes_says_so(lib):
    scene = _supported(load_scene(SCENE), "from_floor", "screen")
    codes = [i.code for i in resolve(scene, lib).issues]
    assert "no_mounting_holes" in codes


def test_a_board_on_edge_cannot_be_posted_to(lib):
    scene = _supported(load_scene(SCENE), "from_floor", "mux")
    next(p for p in scene.placements if p.id == "mux").tilt = 90
    codes = [i.code for i in resolve(scene, lib).issues]
    assert "support_on_edge" in codes


def test_support_uses_the_real_hole_positions(lib):
    """Read out of the vendor STEP, not guessed -- eight on the Trellis."""
    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    res = resolve(scene, lib)
    frame = res.frames["trellis_a"]
    for h in lib["adafruit-3954-neotrellis"].holes:
        want = frame.point((h.at[0], h.at[1], 0.0))
        assert any(sp.at[0] == pytest.approx(want[0])
                   and sp.at[1] == pytest.approx(want[1]) for sp in res.supports)


# --------------------------------------------------------------------------
# supports that are actually manufacturable
# --------------------------------------------------------------------------

def _all_pieces(geom):
    return list(geom.geoms) if hasattr(geom, "geoms") else ([geom] if not geom.is_empty else [])


@pytest.mark.parametrize("interior", ["pocketed", "hollow", "ribs", "grown"])
def test_no_support_leaves_a_loose_ring(lib, interior):
    """A boss floating in a hollow layer is a washer on the cutting bed. Every
    piece of every layer has to reach the outer wall."""
    from hwcase.schema import Interior

    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a", "encoders")
    scene.case.interior = Interior(interior)
    model = build(resolve(scene, lib))
    edge = model.outer.boundary.buffer(0.05)
    for layer in model.layers:
        for piece in _all_pieces(layer.geom):
            assert piece.intersects(edge), \
                f"layer {layer.index} ({interior}) has a piece not joined to the wall"


def test_a_stranded_boss_gets_a_rib(lib):
    from hwcase.schema import Interior

    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    scene.case.interior = Interior.hollow
    model = build(resolve(scene, lib))
    assert _notes(model, "rib tying"), \
        "a boss in the middle of a hollow layer must be tied back"


def test_a_boss_never_shares_a_layer_with_its_board(lib):
    """A layer straddling the board's underside contains the board, so a post
    there would be driven straight through it."""
    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    res = resolve(scene, lib)
    model = build(res)
    bottom = res.supports[0].board_bottom
    for layer in model.layers:
        if [n for n in layer.notes if n.startswith("boss")]:
            assert layer.z1 <= bottom + 1e-6, \
                f"layer {layer.index} posts into the board"


def test_support_measures_the_board_not_its_knobs(lib):
    """`from_lid` on the encoder strip found no layer at all, because the part's
    top was taken as the shaft tips standing proud of the faceplate."""
    scene = _supported(load_scene(SCENE), "from_lid", "encoders")
    res = resolve(scene, lib)
    part = lib["adafruit-5752-quad-encoder"]
    sp = res.supports[0]
    assert sp.board_top - sp.board_bottom == pytest.approx(part.pcb_thickness)

    shaft_top = max(s.z[1] for s in res.solids if s.placement == "encoders")
    assert sp.board_top < shaft_top - 10.0, "the shafts are not the board"

    lid = build(res).layers[-1]
    assert len([n for n in lid.notes if n.startswith("countersink")]) == len(part.holes)


# --------------------------------------------------------------------------
# bolts through the whole stack
# --------------------------------------------------------------------------

def test_case_bolts_are_off_by_default(demo):
    assert demo.scene.case.case_screws.value == "none"
    assert _notes(build(demo), "case bolt") == []


def test_case_bolts_go_through_every_layer(demo):
    spec = demo.scene.case.model_copy(update={"case_screws": "corners"})
    model = build(demo, spec)
    for layer in model.layers:
        assert len([n for n in layer.notes if n.startswith("case bolt")]) == 4, \
            f"layer {layer.index} is missing bolt holes"


def test_case_bolts_are_countersunk_at_both_faces(demo):
    import math

    spec = demo.scene.case.model_copy(update={"case_screws": "corners"})
    model = build(demo, spec)

    def smallest_round_hole(layer):
        best = None
        for p in _all_pieces(layer.geom):
            for r in p.interiors:
                from shapely.geometry import Polygon
                poly = Polygon(r)
                if 4 * math.pi * poly.area / (poly.length ** 2) > 0.99:
                    best = poly.area if best is None else min(best, poly.area)
        return best

    mid = next(l for l in model.layers if l.role == "body")
    shank = math.pi * (spec.case_screw_d / 2) ** 2
    head = math.pi * (spec.case_screw_head / 2) ** 2
    assert head > shank
    assert smallest_round_hole(mid) == pytest.approx(shank, rel=0.02)
    assert smallest_round_hole(model.layers[0]) == pytest.approx(head, rel=0.02)


def test_case_bolts_sit_inside_the_wall(demo):
    """Inset from the corners, so they land in material rather than fresh air."""
    from shapely.geometry import Point

    spec = demo.scene.case.model_copy(update={"case_screws": "corners"})
    model = build(demo, spec)
    x0, y0, x1, y1 = model.outer.bounds
    i = spec.case_screw_inset
    for cx, cy in ((x0 + i, y0 + i), (x1 - i, y0 + i),
                   (x0 + i, y1 - i), (x1 - i, y1 - i)):
        assert model.outer.contains(Point(cx, cy))


# --------------------------------------------------------------------------
# getting at a side port without losing the side
# --------------------------------------------------------------------------

def _pi_side_scene(side, policy, **kw):
    from hwcase.schema import Face, SidePolicy
    scene = load_scene(SCENE)
    next(p for p in scene.placements if p.id == "pi").sides = [
        SidePolicy(side=Face(side), cutout=policy, **kw)]
    return scene


def _opening_width(res, side_axis=0):
    """How wide an opening is, across the axis it faces."""
    o = res.side_openings[0]
    x0, y0, x1, y1 = o.poly.bounds
    return min(x1 - x0, y1 - y0)


def test_open_side_reaches_the_underside(lib):
    """Sliding a plug in through a slot from the side alone does not work: you
    drop it in from below and push it home. So the opening goes all the way
    down through the bottom plate."""
    scene = _pi_side_scene("-y", "open_side")
    res = resolve(scene, lib)
    plain = build(resolve(load_scene(SCENE), lib))
    opened = build(res)

    assert res.side_openings[0].z[0] < -1000.0, "it must reach the underside"
    assert opened.layers[0].geom.area < plain.layers[0].geom.area,         "the bottom plate has to be opened where the ports are"


def test_open_side_only_takes_the_width_of_its_ports(lib):
    """Not the whole side of the case. Everything either side keeps its floor."""
    scene = _pi_side_scene("-y", "open_side")
    res = resolve(scene, lib)
    plain = build(resolve(load_scene(SCENE), lib))
    opened = build(res)

    ports = [c for c in res.connectors
             if c.placement == "pi" and c.conn.face.value == "-y" and c.included]
    widest = max(max(c.conn.cutout) for c in ports if c.conn.cutout)
    span = _opening_width(res)
    assert span < widest * len(ports) + 30.0, "the opening is far too wide"

    lost = plain.layers[0].geom.area - opened.layers[0].geom.area
    assert lost < plain.layers[0].geom.area * 0.2,         "most of the bottom plate must survive"


def test_open_side_leaves_everything_above_the_ports_closed(lib):
    scene = _pi_side_scene("-y", "open_side")
    res = resolve(scene, lib)
    plain = build(resolve(load_scene(SCENE), lib))
    opened = build(res)
    top = res.side_openings[0].z[1]
    for a, b in zip(plain.layers, opened.layers):
        if a.z0 > top:
            assert b.geom.area == pytest.approx(a.geom.area),                 f"layer {a.index} is above the ports and should be untouched"


def test_open_side_is_one_opening_for_the_whole_bank(lib):
    scene = _pi_side_scene("-y", "open_side")
    res = resolve(scene, lib)
    assert len(res.side_openings) == 1
    for name in ("hdmi", "pwr_micro_usb", "av_jack"):
        assert name in res.side_openings[0].reason


def test_a_channel_is_one_groove_per_port(lib):
    scene = _pi_side_scene("-y", "channel", channel_width=12.0)
    res = resolve(scene, lib)
    ports = [c for c in res.connectors
             if c.placement == "pi" and c.conn.face.value == "-y" and c.included]
    assert len(res.side_openings) == len(ports) > 1


def test_a_channel_also_reaches_the_underside(lib):
    scene = _pi_side_scene("-y", "channel", channel_width=12.0)
    res = resolve(scene, lib)
    for o in res.side_openings:
        assert o.z[0] < -1000.0, f"{o.reason} does not reach the underside"


def test_a_channel_keeps_the_case_shape(lib):
    """It is a groove, not a missing side: the outline is untouched and far
    less of the bottom goes than with a full opening."""
    from hwcase.case import outer_shape

    plain = load_scene(SCENE)
    chan = _pi_side_scene("-y", "channel", channel_width=12.0)
    side = _pi_side_scene("-y", "open_side")

    a = outer_shape(resolve(plain, lib), plain.case).bounds
    b = outer_shape(resolve(chan, lib), chan.case).bounds
    assert a == pytest.approx(b)

    base = build(resolve(plain, lib)).layers[0].geom.area
    grooved = build(resolve(chan, lib)).layers[0].geom.area
    opened = build(resolve(side, lib)).layers[0].geom.area
    assert base > grooved > opened, (base, grooved, opened)


def test_a_channel_runs_out_through_the_wall(lib):
    scene = _pi_side_scene("-y", "channel", channel_width=12.0)
    res = resolve(scene, lib)
    for o in res.side_openings:
        x0, y0, x1, y1 = o.poly.bounds
        assert max(x1 - x0, y1 - y0) > 500.0, "it should reach past any outline"


def test_a_channel_is_never_narrower_than_its_port(lib):
    """Asking for 6 mm at a 17 mm HDMI would make the port unusable."""
    scene = _pi_side_scene("-y", "channel", channel_width=6.0)
    res = resolve(scene, lib)
    hdmi = next(o for o in res.side_openings if "hdmi" in o.reason)
    x0, y0, x1, y1 = hdmi.poly.bounds
    assert min(x1 - x0, y1 - y0) >= 17.0 - 1e-6
    assert any(i.code == "channel_widened" for i in res.issues)


def test_a_channel_widens_a_narrow_port(lib):
    scene = _pi_side_scene("-y", "channel", channel_width=20.0)
    res = resolve(scene, lib)
    jack = next(o for o in res.side_openings if "av_jack" in o.reason)
    x0, y0, x1, y1 = jack.poly.bounds
    assert min(x1 - x0, y1 - y0) >= 20.0 - 1e-6,         "a 9 mm jack should get the full 20 mm of access"


def test_a_wider_channel_removes_more(lib):
    def area(w):
        scene = _pi_side_scene("-y", "channel", channel_width=w)
        return sum(l.geom.area for l in build(resolve(scene, lib)).layers)

    assert area(24.0) < area(12.0)


@pytest.mark.parametrize("policy", ["open_side", "channel", "open_to_edge"])
def test_side_policies_leave_nothing_loose(lib, policy):
    scene = _pi_side_scene("-y", policy)
    model = build(resolve(scene, lib))
    edge = model.outer.boundary.buffer(0.05)
    for layer in model.layers:
        for piece in _all_pieces(layer.geom):
            assert piece.intersects(edge), \
                f"{policy} left an island in layer {layer.index}"


# --------------------------------------------------------------------------
# scene files: new, duplicate, rename
# --------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from hwcase import api

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "base.yaml").write_text(SCENE.read_text(encoding="utf-8"),
                                      encoding="utf-8")
    monkeypatch.setattr(api, "SCENES_DIR", scenes)
    return TestClient(api.app), scenes


def test_a_new_scene_is_empty_but_valid(client):
    c, scenes = client
    assert c.post("/api/scenes", json={"name": "fresh"}).status_code == 200
    assert (scenes / "fresh.yaml").exists()
    body = c.get("/api/scenes/fresh").json()
    assert body["name"] == "fresh"
    assert body["placements"] == []
    assert c.post("/api/resolve", json=body).status_code == 200


def test_a_duplicate_keeps_the_comments(client):
    c, scenes = client
    before = (scenes / "base.yaml").read_text(encoding="utf-8")
    assert before.count("#") > 5

    r = c.post("/api/scenes", json={"name": "variant", "copy_from": "base"})
    assert r.status_code == 200
    after = (scenes / "variant.yaml").read_text(encoding="utf-8")
    for line in before.splitlines():
        if line.strip().startswith("#"):
            assert line.strip() in after, f"lost: {line.strip()}"


def test_a_duplicate_is_independent(client):
    c, scenes = client
    c.post("/api/scenes", json={"name": "variant", "copy_from": "base"})
    variant = c.get("/api/scenes/variant").json()
    variant["placements"][0]["pos"] = [111.0, 222.0, 0.0]
    c.put("/api/scenes/variant", json=variant)

    assert c.get("/api/scenes/base").json()["placements"][0]["pos"] != [111.0, 222.0, 0.0]
    assert c.get("/api/scenes/variant").json()["placements"][0]["pos"] == [111.0, 222.0, 0.0]


def test_rename_moves_the_file_and_the_name_inside_it(client):
    c, scenes = client
    assert c.post("/api/scenes/base/rename", json={"to": "renamed"}).status_code == 200
    assert not (scenes / "base.yaml").exists()
    assert (scenes / "renamed.yaml").exists()
    assert c.get("/api/scenes/renamed").json()["name"] == "renamed"
    assert c.get("/api/scenes/base").status_code == 404


def test_rename_keeps_the_comments(client):
    c, scenes = client
    before = (scenes / "base.yaml").read_text(encoding="utf-8")
    c.post("/api/scenes/base/rename", json={"to": "renamed"})
    after = (scenes / "renamed.yaml").read_text(encoding="utf-8")
    for line in before.splitlines():
        if line.strip().startswith("#"):
            assert line.strip() in after


def test_you_cannot_overwrite_by_accident(client):
    c, _ = client
    c.post("/api/scenes", json={"name": "taken"})
    assert c.post("/api/scenes", json={"name": "taken"}).status_code == 409
    assert c.post("/api/scenes/base/rename", json={"to": "taken"}).status_code == 409


BAD_NAMES = ("../evil", r"..\evil", "/etc/passwd", "a/b", "", ".", "..",
             "con.yaml", "x" * 200)


def test_scene_names_cannot_escape_the_directory(client, tmp_path):
    """A scene name becomes a filename, so it is checked rather than trusted."""
    c, _ = client
    for bad in BAD_NAMES:
        if bad in ("con.yaml",):
            continue                    # legal characters; only odd on Windows
        assert c.post("/api/scenes", json={"name": bad}).status_code == 400, bad
        assert c.post("/api/scenes/base/rename",
                      json={"to": bad}).status_code == 400, bad
    assert not (tmp_path / "evil.yaml").exists()
    assert not (tmp_path.parent / "evil.yaml").exists()


def test_the_name_validator_itself_rejects_traversal(monkeypatch, tmp_path):
    """Checked at the source rather than only through the router, which
    normalises `.` and `..` away before a request ever reaches us."""
    from fastapi import HTTPException
    from hwcase import api

    monkeypatch.setattr(api, "SCENES_DIR", tmp_path)
    for bad in BAD_NAMES:
        if bad == "con.yaml":
            continue
        with pytest.raises(HTTPException) as caught:
            api._scene_path(bad)
        assert caught.value.status_code == 400, bad

    ok = api._scene_path("good name-1.2")
    assert ok.parent == tmp_path.resolve()


def test_ordinary_names_are_accepted(client):
    c, _ = client
    for good in ("v2", "sound machine", "rev_3", "a.b", "MK-II"):
        assert c.post("/api/scenes", json={"name": good}).status_code == 200, good


def test_the_listing_shows_new_scenes(client):
    c, _ = client
    c.post("/api/scenes", json={"name": "another"})
    assert set(c.get("/api/scenes").json()["scenes"]) == {"base", "another"}


# --------------------------------------------------------------------------
# material too thin to survive
# --------------------------------------------------------------------------

def test_slivers_are_opened_out(demo):
    """Two cutouts passing close together leave a thread of plywood that snaps
    the first time it is handled. It is better not to be there."""
    wide = build(demo, demo.scene.case.model_copy(update={"min_segment": 4.0}))
    raw = build(demo, demo.scene.case.model_copy(update={"min_segment": 0.0}))
    assert sum(l.geom.area for l in wide.layers) < sum(l.geom.area for l in raw.layers)
    assert _notes(wide, "opened out"), "it should say what it removed"


def test_nothing_narrower_than_the_minimum_survives(demo):
    """Eroding by half the minimum must not wipe a layer out: whatever is left
    is at least that wide."""
    w = 4.0
    model = build(demo, demo.scene.case.model_copy(update={"min_segment": w}))
    for layer in model.layers:
        if layer.geom.is_empty:
            continue
        core = layer.geom.buffer(-w / 2.0 + 0.01, join_style=2)
        assert not core.is_empty, f"layer {layer.index} is thinner than {w} mm"


def test_a_bigger_minimum_removes_more(demo):
    def area(w):
        return sum(l.geom.area for l in
                   build(demo, demo.scene.case.model_copy(update={"min_segment": w})).layers)

    assert area(8.0) < area(4.0) < area(1.0)


def test_zero_leaves_the_geometry_alone(demo):
    off = build(demo, demo.scene.case.model_copy(update={"min_segment": 0.0}))
    assert not _notes(off, "opened out")


def test_opening_runs_before_the_bosses(demo):
    """A boss ring around an M2.5 screw is legitimately narrow. Eroding it away
    would take the very thing holding the board up."""
    scene = _supported(load_scene(SCENE), "from_floor", "trellis_a")
    scene.case.min_segment = 6.0          # wider than the 9 mm boss's 3.1 mm ring
    model = build(resolve(scene, lib_for(scene)))
    assert _notes(model, "boss for"), "the bosses must survive"


def lib_for(_scene):
    return PartLibrary.load()


# --------------------------------------------------------------------------
# bolts along the edges, not only at the corners
# --------------------------------------------------------------------------

def test_perimeter_adds_bolts_between_the_corners(demo):
    from hwcase.case import case_screw_points, outer_shape

    corners = demo.scene.case.model_copy(update={"case_screws": "corners"})
    around = demo.scene.case.model_copy(
        update={"case_screws": "perimeter", "case_screw_spacing": 80.0})
    outer = outer_shape(demo, corners)
    assert len(case_screw_points(corners, outer)) == 4
    assert len(case_screw_points(around, outer)) > 4


def test_closer_spacing_means_more_bolts(demo):
    from hwcase.case import case_screw_points, outer_shape

    def count(spacing):
        spec = demo.scene.case.model_copy(
            update={"case_screws": "perimeter", "case_screw_spacing": spacing})
        return len(case_screw_points(spec, outer_shape(demo, spec)))

    assert count(40.0) > count(120.0) >= 4


def test_every_case_bolt_lands_in_the_wall(demo):
    from shapely.geometry import Point
    from hwcase.case import case_screw_points, outer_shape

    spec = demo.scene.case.model_copy(
        update={"case_screws": "perimeter", "case_screw_spacing": 50.0})
    outer = outer_shape(demo, spec)
    model = build(demo, spec)
    for cx, cy in case_screw_points(spec, outer):
        assert outer.contains(Point(cx, cy))
    for layer in model.layers:
        assert len([n for n in layer.notes if n.startswith("case bolt")]) == \
            len(case_screw_points(spec, outer))


def test_opening_never_adds_material(demo):
    """Dilating back after eroding rounds off the inner end of a narrow slot,
    which was filling the tip of a 5 mm connector pocket with plywood. An
    opening has to be a subset of what it started from.

    Checked on the operation itself rather than by diffing two whole builds:
    changing min_segment also changes where the cable channels get routed, so
    two builds legitimately differ in ways this has nothing to do with.
    """
    from hwcase.case import _open_out_slivers

    spec = demo.scene.case.model_copy(update={"min_segment": 4.0})
    raw = build(demo, demo.scene.case.model_copy(update={"min_segment": 0.0}))
    for layer in raw.layers:
        opened = _open_out_slivers(layer.geom, spec, [])
        assert opened.area <= layer.geom.area + 1e-6, \
            f"layer {layer.index} gained material"
        assert opened.difference(layer.geom).area < 1e-6, \
            f"layer {layer.index} put material somewhere new"


def test_opening_keeps_out_of_the_connector_pockets(demo):
    """The Pi's 5.08 mm header pocket is the narrowest thing in the scene."""
    from hwcase.geom import z_overlap

    model = build(demo, demo.scene.case.model_copy(update={"min_segment": 4.0}))
    header = next(s for s in demo.solids if s.ref == "pi.gpio_header")
    for layer in model.layers:
        if z_overlap(header.z, (layer.z0, layer.z1)) <= 0 or layer.role != "body":
            continue
        assert layer.geom.intersection(header.poly).area < 1.0, \
            f"layer {layer.index} has material inside the header pocket"


def test_a_severed_stiffener_is_dropped_not_bridged(lib):
    """Bridging an offcut back would run a strip of plywood straight across the
    hardware, which is worse than losing a fragment of stiffener."""
    from hwcase.geom import z_overlap
    from hwcase.schema import Interior

    scene = load_scene(SCENE)
    scene.case.interior = Interior.ribs
    res = resolve(scene, lib)
    model = build(res)

    for layer in model.layers:
        if layer.role != "body":
            continue
        for s in res.solids:
            if s.kind.value != "body" or z_overlap(s.z, (layer.z0, layer.z1)) <= 0:
                continue
            assert layer.geom.intersection(s.poly).area < s.poly.area * 0.02, \
                f"layer {layer.index} has material sitting on {s.ref}"


# --------------------------------------------------------------------------
# no board may be walled in with its leads
# --------------------------------------------------------------------------

def _isolated_layers(model, res):
    """Layers where the internal leads cannot all reach each other."""
    from shapely.geometry import Point
    from hwcase.geom import z_overlap

    bad = []
    for layer in model.layers:
        slab = (layer.z0, layer.z1)
        mouths = [(c.ref, Point(c.at[0], c.at[1])) for c in res.connectors
                  if not c.conn.external and z_overlap(c.corridor_z, slab) > 0]
        if len({r.split(".")[0] for r, _ in mouths}) < 2:
            continue
        void = model.outer.difference(layer.geom)
        parts = _all_pieces(void)
        where = {ref: next((i for i, p in enumerate(parts)
                            if p.intersects(pt.buffer(0.05))), None)
                 for ref, pt in mouths}
        if len(set(where.values())) > 1:
            bad.append(layer.index)
    return bad


@pytest.mark.parametrize("interior", ["pocketed", "hollow", "ribs", "grown"])
def test_no_board_is_walled_in_with_its_leads(demo, interior):
    """Every interior strategy has to leave the internal wiring connected. They
    disagree about this by nature -- `hollow` connects everything for free while
    `pocketed` cuts each board its own recess -- so the void is checked after
    the fact rather than each strategy being special-cased."""
    spec = demo.scene.case.model_copy(
        update={"interior": interior, "link_cables": True})
    assert _isolated_layers(build(demo, spec), demo) == []


def test_pocketed_really_would_isolate_them(demo):
    """The behaviour the linking exists to fix -- otherwise the test above
    could be passing for the wrong reason."""
    spec = demo.scene.case.model_copy(
        update={"interior": "pocketed", "link_cables": False})
    assert _isolated_layers(build(demo, spec), demo), \
        "expected pocketed to strand some leads with linking off"


def test_a_buried_mouth_is_cut_open(demo):
    """A strategy that only opens up for external ports leaves an internal
    socket embedded in solid plywood. Routing a channel *to* it is not enough;
    the mouth itself has to be cut."""
    from shapely.geometry import Point
    from hwcase.geom import z_overlap

    spec = demo.scene.case.model_copy(
        update={"interior": "pocketed", "link_cables": True})
    model = build(demo, spec)
    checked = 0
    for layer in model.layers:
        slab = (layer.z0, layer.z1)
        mouths = [c for c in demo.connectors
                  if not c.conn.external and z_overlap(c.corridor_z, slab) > 0]
        # Linking only has something to do where two boards meet. A layer
        # holding one board's leads has nothing to connect them to, so it is
        # left alone rather than having surprise holes punched in it.
        if len({c.placement for c in mouths}) < 2:
            continue
        for c in mouths:
            checked += 1
            assert not layer.geom.contains(Point(c.at[0], c.at[1])), \
                f"{c.ref} is buried in layer {layer.index}"
    assert checked, "no layer had leads from two boards to check"


def test_the_channel_is_as_wide_as_asked(demo):
    def area(w):
        spec = demo.scene.case.model_copy(
            update={"interior": "pocketed", "link_cables": True, "cable_channel": w})
        return sum(l.geom.area for l in build(demo, spec).layers)

    assert area(12.0) < area(4.0), "a wider channel should remove more"


def test_linking_can_be_turned_off(demo):
    on = demo.scene.case.model_copy(update={"link_cables": True})
    off = demo.scene.case.model_copy(update={"link_cables": False})
    assert (sum(l.geom.area for l in build(demo, off).layers) >
            sum(l.geom.area for l in build(demo, on).layers))


# --------------------------------------------------------------------------
# the outline is what makes this a case rather than a tray
# --------------------------------------------------------------------------

def _wall_gaps(model, layer):
    return model.outer.exterior.difference(layer.geom.buffer(0.02))


def _coverage(model):
    per = model.outer.exterior.length
    return [100.0 * model.outer.exterior.intersection(l.geom.buffer(0.02)).length / per
            for l in model.layers]


@pytest.mark.parametrize("interior", ["pocketed", "hollow", "ribs", "grown"])
def test_every_hole_in_the_wall_is_deliberate(demo, interior):
    """No pocket, hollow or grown cable route may thin the outside. Only ports
    and side openings are meant to breach it."""
    from shapely.ops import unary_union
    from hwcase.case import case_screw_points
    from hwcase.geom import z_overlap

    spec = demo.scene.case.model_copy(update={"interior": interior})
    model = build(demo, spec)
    for layer in model.layers:
        gaps = _wall_gaps(model, layer)
        if gaps.length < 1.0:
            continue
        slab = (layer.z0, layer.z1)
        allowed = [wc.corridor_poly.buffer(spec.part_clearance + 0.5)
                   for wc in demo.connectors
                   if wc.cuts_the_wall and z_overlap(wc.corridor_z, slab) > 0]
        allowed += [so.poly.buffer(0.5) for so in demo.side_openings
                    if z_overlap(so.z, slab) > 0]
        allowed += [Point(p).buffer(spec.case_screw_head)
                    for p in case_screw_points(spec, model.outer)]
        unexplained = gaps.difference(unary_union(allowed)).length if allowed else gaps.length
        assert unexplained < 1.0, \
            f"{interior}: layer {layer.index} has {unexplained:.1f} mm of wall " \
            f"missing for no reason"


def test_the_wall_does_not_depend_on_the_interior(demo):
    """Hollowing out the middle is not licence to open the sides."""
    runs = {m: _coverage(build(demo, demo.scene.case.model_copy(update={"interior": m})))
            for m in ("pocketed", "hollow", "ribs", "grown")}
    ref = runs["pocketed"]
    for mode, cov in runs.items():
        assert cov == pytest.approx(ref, abs=0.5), \
            f"{mode} gives a different wall from pocketed"


def test_internal_wiring_does_not_breach_the_wall(demo):
    """`grown` reserves room for every lead including the internal ones. Eight
    I2C ports on a hub near the edge were each punching straight through."""
    from hwcase.case import outer_shape
    from hwcase.geom import z_overlap

    spec = demo.scene.case.model_copy(update={"interior": "grown"})
    outer = outer_shape(demo, spec)
    # The guarantee is a MINIMUM wall, not the full nominal one: `wall` places
    # the outline, `min_segment` is the hard floor a pocket may not eat past.
    # A grown cable route taking the wall from 8 mm down to 4 mm is fine.
    inner = outer.buffer(-spec.min_segment)
    model = build(demo, spec)
    for layer in model.layers:
        slab = (layer.z0, layer.z1)
        for wc in demo.connectors:
            if wc.cuts_the_wall or z_overlap(wc.corridor_z, slab) <= 0:
                continue
            outside = wc.corridor_poly.difference(inner).intersection(outer)
            if outside.area < 1.0:
                continue
            still_there = layer.geom.intersection(outside).area
            assert still_there > outside.area * 0.5, \
                f"{wc.ref} ate the wall in layer {layer.index}"


def test_the_minimum_wall_survives_a_thin_setting(demo):
    """Even with the wall set thinner than min_segment, what is left is a wall
    rather than a row of slivers."""
    spec = demo.scene.case.model_copy(update={"wall": 3.0, "min_segment": 4.0})
    model = build(demo, spec)
    assert min(_coverage(model)) > 80.0


def test_hardware_inside_the_band_is_not_buried(lib):
    """If a board really sits within min_segment of the outline then the wall
    cannot be there, and the board wins -- pressing plywood into it would be
    worse than an honest gap."""
    from hwcase.geom import z_overlap

    scene = lab()
    scene.case.wall = 1.0                 # squeeze the outline onto the hardware
    scene.case.min_segment = 6.0
    res = resolve(scene, lib)
    model = build(res)
    for layer in model.layers:
        slab = (layer.z0, layer.z1)
        for s in res.solids:
            if s.kind.value != "body" or z_overlap(s.z, slab) <= 0:
                continue
            assert layer.geom.intersection(s.poly).area < s.poly.area * 0.05, \
                f"layer {layer.index} has material inside {s.ref}"


# ---------------------------------------------------------------------------
# case bolts through the stack
# ---------------------------------------------------------------------------

def test_centre_bolt_is_optional_and_central(lib):
    """The centre bolt adds exactly one hole, in the middle."""
    from hwcase import case as C

    scene = lab()
    base = scene.case.model_copy(update={"case_screws": "corners",
                                         "case_screw_center": False})
    res = resolve(scene, lib)
    outer = C.outer_shape(res, base)
    corners = C.case_screw_points(base, outer)
    assert len(corners) == 4

    with_c = C.case_screw_points(
        base.model_copy(update={"case_screw_center": True}), outer)
    assert len(with_c) == 5
    assert outer.contains(Point(*with_c[-1]))


def test_perimeter_bolts_respect_spacing(lib):
    """Tightening the spacing may only ever add bolts, never lose the corners."""
    from hwcase import case as C

    scene = lab()
    res = resolve(scene, lib)
    spec = scene.case.model_copy(update={"case_screws": "perimeter"})
    outer = C.outer_shape(res, spec)

    wide = C.case_screw_points(spec.model_copy(
        update={"case_screw_spacing": 500.0}), outer)
    tight = C.case_screw_points(spec.model_copy(
        update={"case_screw_spacing": 25.0}), outer)
    assert len(wide) == 4                      # nothing fits between corners
    assert len(tight) > len(wide)
    assert set(wide) <= set(tight)             # corners survive


def test_a_bolt_through_a_board_is_reported(lib):
    """Silently drilling through the hardware is the failure mode to avoid."""
    from hwcase import case as C

    from hwcase.schema import CaseSpec, Placement, Scene

    # One board, and a bolt asked for in the middle of the case -- which is
    # exactly where the board is. That is the case worth catching.
    scene = Scene(
        name="collide",
        placements=[Placement(id="pi", part="rpi-3b", pos=(0.0, 0.0, 0.0),
                              mount="manual")],
        case=CaseSpec(case_screws="corners", case_screw_center=True),
    )
    model = C.build(resolve(scene, lib), scene.case)
    notes = [n for layer in model.layers for n in layer.notes
             if "runs into" in n]
    assert notes, "a bolt landing on a board must say so"


def test_bolt_holes_go_through_every_layer(lib):
    """A bolt that stops half way through the stack holds nothing together."""
    from hwcase import case as C

    scene = lab()
    scene.case = scene.case.model_copy(update={"case_screws": "corners"})
    res = resolve(scene, lib)
    model = C.build(res, scene.case)
    pts = C.case_screw_points(scene.case, C.outer_shape(res, scene.case))
    assert pts

    for layer in model.layers:
        geom = layer.geom
        for x, y in pts:
            # either the hole is there, or there is no material to drill
            probe = Point(x, y)
            assert not geom.contains(probe), (
                f"layer {layer.index} has no bolt hole at ({x:.1f}, {y:.1f})")


# ---------------------------------------------------------------------------
# the OLED used to be modelled as two planes crossing each other
# ---------------------------------------------------------------------------

def test_oled_volumes_do_not_cross(lib):
    """Volumes may nest or stack, but two boxes cutting through each other
    describe a shape that does not exist and produce a nonsense window."""
    import itertools

    from hwcase.geom import box_polygon

    part = lib["adafruit-4741-oled-1v5"]
    for a, b in itertools.combinations(part.volumes, 2):
        za, zb = (min(a.z), max(a.z)), (min(b.z), max(b.z))
        if min(za[1], zb[1]) - max(za[0], zb[0]) <= 1e-9:
            continue                            # stacked, not crossing
        pa, pb = box_polygon(a), box_polygon(b)
        if not pa.intersects(pb):
            continue
        assert pa.contains(pb) or pb.contains(pa), (
            f"{part.id}: {a.name} and {b.name} cross each other")


def test_oled_window_covers_the_display_module(lib):
    """The lit area is a datasheet number placed inside a measured module.
    The previous attempt derived it from a bounding box that had merged two
    connectors into an imaginary strip, so nothing fitted inside anything."""
    from hwcase.geom import box_polygon

    part = lib["adafruit-4741-oled-1v5"]
    by_name = {v.name: v for v in part.volumes}
    module, active = by_name["display_module"], by_name["active_area"]
    assert box_polygon(module).buffer(1e-6).contains(box_polygon(active)), (
        "the lit area has to be inside the module it is part of")


# ---------------------------------------------------------------------------
# reading geometry out of vendor CAD
# ---------------------------------------------------------------------------

CAD = BACKEND.parent / "vendor" / "cad" / "adafruit"
needs_cad = pytest.mark.skipif(not CAD.exists(), reason="vendor CAD not fetched")


def _stl(product: str) -> Path:
    hits = sorted(CAD.glob(f"{product}*/*.stl")) + sorted(CAD.glob(f"{product}/*.stl"))
    if not hits:
        pytest.skip(f"no STL for {product}")
    return hits[0]


@needs_cad
def test_bodies_finds_the_board_and_the_display(lib):
    """Splitting on connectivity asks the model what objects it contains.

    Band slicing inferred them instead, and inferred wrong: it merged the two
    STEMMA QT connectors on opposite edges of the 4741 into a single 34 mm
    box, which is a thing that does not exist.
    """
    from hwcase.measure import bodies

    found = bodies(_stl("4741"))
    assert len(found) > 100                      # a board and its components

    pcb = found[0]
    assert pcb.size[0] == pytest.approx(35.56, abs=0.05)
    assert pcb.size[1] == pytest.approx(46.99, abs=0.05)
    assert pcb.size[2] == pytest.approx(1.57, abs=0.05)

    module = found[1]
    assert module.size[0] == pytest.approx(33.80, abs=0.05)
    assert module.size[1] == pytest.approx(36.50, abs=0.05)

    # the two QT connectors are two bodies, not one strip
    qt = [b for b in found if abs(b.size[0] - 4.95) < 0.1
          and abs(b.size[1] - 6.0) < 0.1]
    assert len(qt) == 2
    assert abs(qt[0].centre[0] - qt[1].centre[0]) > 25.0, "opposite edges"


@needs_cad
def test_flipping_a_part_keeps_it_the_same_part():
    """Mirroring z alone turns a part into its mirror image, which quietly
    moves every connector to the wrong edge. Mirror two axes or none."""
    from hwcase.measure import Body

    b = Body(1.0, 5.0, 10.0, 16.0, -1.0, 0.0)
    f = b.flipped(width=35.56, pcb_top=1.57)
    assert f.size == pytest.approx(b.size)               # same box
    assert (f.z0, f.z1) == pytest.approx((1.57, 2.57))   # above the board now
    assert f.flipped(35.56, 1.57).centre == pytest.approx(b.centre)


@needs_cad
def test_z_bands_report_position_not_just_extent():
    """'Something 34 mm wide up there' is not enough to cut a window for it."""
    from hwcase.measure import load_points, z_bands

    bands = [b for b in z_bands(load_points(_stl("4741")), 0.5) if b.count]
    assert bands
    for b in bands:
        assert b.x1 >= b.x0 and b.y1 >= b.y0
        assert b.width == pytest.approx(b.x1 - b.x0)


@needs_cad
def test_step_holes_finds_the_oled_mounting_pattern():
    """A mounting hole is a screw-sized circle, repeated, spread out."""
    from hwcase.measure import step_holes

    steps = sorted(CAD.glob("4741*/*.step")) + sorted(CAD.glob("4741*/*.stp"))
    if not steps:
        pytest.skip("no STEP for 4741")
    groups = step_holes(steps[0])
    assert groups, "the 4741 has four M2 holes"
    best = groups[0]
    assert len(best.centres) >= 4
    assert best.diameter == pytest.approx(2.5, abs=0.3)


# ---------------------------------------------------------------------------
# the vendor catalogue and the importer
# ---------------------------------------------------------------------------

def _fake_catalog(tmp_path, products, cad=None):
    """A Catalog backed by files on disk, so no test touches the network."""
    import json

    from hwcase.catalog import Catalog

    cache = Path(tmp_path) / "catalog"
    cache.mkdir(parents=True)
    (cache / "adafruit-products.json").write_text(json.dumps(products),
                                                  encoding="utf-8")
    (cache / "adafruit-cad.json").write_text(json.dumps(cad or {}),
                                             encoding="utf-8")
    c = Catalog(cache_dir=cache, ttl=10 ** 9)
    c._read_cache()
    return c


PRODUCTS = [
    {"product_id": 4741, "product_name": 'Grayscale 1.5" 128x128 OLED Display',
     "product_master_category": "Displays"},
    {"product_id": 3954, "product_name": "NeoTrellis RGB Driver PCB for 4x4 Keypad",
     "product_master_category": "Breakouts"},
    {"product_id": 1234, "product_name": "Hook-up Wire Spool Set",
     "product_master_category": "Wire"},
    {"product_id": 5752, "product_name": "I2C Quad Rotary Encoder Breakout",
     "product_master_category": "Breakouts"},
]


def test_catalog_search_puts_the_sku_first(tmp_path):
    """Typing a product number is an exact request, not a fuzzy one."""
    c = _fake_catalog(tmp_path, PRODUCTS)
    hits = c.search("5752")
    assert hits[0].id == "5752"
    assert hits[0].score > 50


def test_catalog_search_requires_every_term(tmp_path):
    """"quad keypad" matches nothing: two products each match one word, and
    neither of them is what was asked for."""
    c = _fake_catalog(tmp_path, PRODUCTS)
    assert c.search("quad keypad") == []
    assert [e.id for e in c.search("quad rotary")] == ["5752"]


def test_catalog_ranks_importable_and_known_parts_higher(tmp_path):
    """A product we can measure beats one we would have to guess at, and one
    already in the library beats both."""
    plain = _fake_catalog(tmp_path / "a", PRODUCTS)
    withcad = _fake_catalog(tmp_path / "b", PRODUCTS, cad={"4741": "4741 OLED"})

    base = plain.search("oled")[0].score
    cadded = withcad.search("oled")[0].score
    assert cadded > base

    known = withcad.search("oled", known={"4741": "adafruit-4741-oled-1v5"})[0]
    assert known.score > cadded
    assert known.part_id == "adafruit-4741-oled-1v5"
    assert known.as_dict()["in_library"] is True


def test_catalog_survives_the_vendor_being_down(tmp_path, monkeypatch):
    """A search box that empties itself because someone else's server is down
    is worse than one showing yesterday's catalogue."""
    import json

    from hwcase import catalog as cat

    cache = Path(tmp_path) / "catalog"
    cache.mkdir(parents=True)
    (cache / "adafruit-products.json").write_text(json.dumps(PRODUCTS),
                                                  encoding="utf-8")

    def explode(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(cat, "_fetch", explode)
    c = cat.Catalog(cache_dir=cache, ttl=0).load()    # ttl 0 forces a refresh
    assert c.stale is True
    assert c.error and "connection refused" in c.error
    assert len(c.products) == 4                       # still usable
    assert c.search("oled")[0].id == "4741"


def test_catalog_reports_having_nothing_at_all(tmp_path, monkeypatch):
    from hwcase import catalog as cat

    def explode(*a, **kw):
        raise OSError("no route to host")

    monkeypatch.setattr(cat, "_fetch", explode)
    c = cat.Catalog(cache_dir=Path(tmp_path) / "empty", ttl=0).load()
    assert c.products == []
    assert c.error
    assert c.search("oled") == []


def test_a_truncated_download_is_not_cached(tmp_path, monkeypatch):
    """Writing half a JSON file to the cache would break every later search,
    including the offline ones."""
    from hwcase import catalog as cat

    monkeypatch.setattr(cat, "_fetch", lambda *a, **kw: b'[{"product_id": 1,')
    c = cat.Catalog(cache_dir=Path(tmp_path) / "c", ttl=0).load()
    assert not (Path(tmp_path) / "c" / "adafruit-products.json").exists()
    assert c.error


def test_slug_makes_a_usable_part_id():
    from hwcase.ingest import slug

    assert slug('Adafruit Grayscale 1.5" 128x128 OLED') == \
        "adafruit-grayscale-1-5in-128x128-oled"
    assert slug("!!!") == "part"
    assert len(slug("x" * 200)) <= 60


def test_flip_puts_a_display_module_face_up():
    """One large flat body alone under the board is a display, and a display
    is the thing that should face the world."""
    from hwcase.ingest import _should_flip
    from hwcase.measure import Body

    pcb = Body(0, 35.6, 0, 47, 0, 1.57)
    module = Body(1, 34.6, 5, 42, -1.1, 0)
    flip, why = _should_flip([pcb, module], pcb)
    assert flip and "display module" in why


def test_flip_uses_reach_not_body_count():
    """A rotary encoder straddles the PCB, so counting bodies per face reads
    the board as symmetric while its knobs point the wrong way."""
    from hwcase.ingest import _should_flip
    from hwcase.measure import Body

    pcb = Body(0, 76.2, 0, 21.6, 0, 1.57)
    knobs = [Body(x, x + 12, 4, 17, -21.4, 3.7) for x in (3, 22, 41, 60)]
    flip, why = _should_flip([pcb] + knobs, pcb)
    assert flip and "proud below" in why

    # and the other way round: tall things already on top stay on top
    tall = [Body(x, x + 12, 4, 17, 1.57, 6.4) for x in (3, 22)]
    assert _should_flip([pcb] + tall, pcb)[0] is False


def test_saving_a_draft_replaces_rather_than_duplicates(tmp_path):
    """The usual reason to import a product twice is that the first go was
    wrong, so a second entry for the same id is never what anyone wants."""
    from hwcase.ingest import Draft, forget_draft, save_draft

    part = {
        "id": "demo-board", "name": "Demo Board",
        "outline": {"type": "rect", "size": [20.0, 30.0], "origin": "min"},
        "volumes": [], "connectors": [],
    }
    path = Path(tmp_path) / "imported.yaml"
    save_draft(Draft(part=part, source="a.stl"), path)
    save_draft(Draft(part={**part, "name": "Demo Board rev B"}, source="a.stl"),
               path)

    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(doc["parts"]) == 1
    assert doc["parts"][0]["name"] == "Demo Board rev B"
    assert "MACHINE WRITTEN" in path.read_text(encoding="utf-8")

    assert forget_draft("demo-board", path) is True
    assert not path.exists()                  # an empty file is just clutter
    assert forget_draft("demo-board", path) is False


@needs_cad
def test_a_draft_never_invents_connectors():
    """A mesh cannot say where a cable plugs in, and a made-up socket is worse
    than a missing one because the case would route wiring to it."""
    from hwcase.catalog import CatalogEntry
    from hwcase.ingest import draft_part

    entry = CatalogEntry(id="4741", name="Test OLED", cad="4741 OLED")
    draft = draft_part(entry, cad=_stl("4741"))
    assert draft.part["connectors"] == []
    assert "no connectors" in draft.part["notes"]
    assert "DRAFT" in draft.part["notes"]


@needs_cad
def test_a_draft_validates_as_a_part():
    from hwcase.catalog import CatalogEntry
    from hwcase.ingest import draft_part
    from hwcase.library import _check_names

    entry = CatalogEntry(id="4741", name="Test OLED", cad="4741 OLED")
    part = Part.model_validate(draft_part(entry, cad=_stl("4741")).part)
    _check_names(part)
    assert part.outline.size == pytest.approx((35.56, 46.99), abs=0.05)
    assert part.pcb_thickness == pytest.approx(1.57, abs=0.05)


# ---------------------------------------------------------------------------
# front-panel engraving
# ---------------------------------------------------------------------------

def _engraving():
    from hwcase.schema import Engraving
    return Engraving(name="vent", pattern="fins", at=(0.0, 0.0),
                     size=(70.0, 40.0), stroke=1.0, pitch=2.5)


def _engraved(*engravings):
    scene = load_scene(SCENE)
    scene.engravings = list(engravings)
    return scene


def test_every_pattern_draws_something():
    """Six patterns, all of which have to produce marks inside their own box.
    A pattern that silently produces nothing is worse than no pattern."""
    from shapely.geometry import box as shbox

    from hwcase.engrave import build_engraving
    from hwcase.schema import Engraving, Pattern

    clip = shbox(-100, -60, 100, 60)
    for pattern in Pattern:
        e = Engraving(name=pattern.value, pattern=pattern,
                      at=(0.0, 0.0), size=(80.0, 40.0), stroke=1.2, pitch=3.0)
        g = build_engraving(e, clip)
        assert not g.is_empty, f"{pattern.value} drew nothing"
        assert g.area > 1.0
        x0, y0, x1, y1 = g.bounds
        # `size` has to mean what it says, or two engravings side by side
        # silently overlap
        assert x0 >= -40.001 and x1 <= 40.001
        assert y0 >= -20.001 and y1 <= 20.001


def test_an_engraving_is_clipped_to_the_plate():
    """A grill that runs off the edge is not a grill, it is a row of nicks in
    the outline."""
    from shapely.geometry import box as shbox

    from hwcase.engrave import build_engraving
    from hwcase.schema import Engraving

    plate = shbox(0, 0, 50, 50)
    e = Engraving(name="over", pattern="fins", at=(50.0, 25.0), size=(80.0, 40.0))
    g = build_engraving(e, plate)
    assert not g.is_empty
    assert plate.buffer(1e-9).contains(g)


def test_marks_do_not_change_the_part(lib):
    """The whole point of a separate pass: an engrave marks the surface and
    changes nothing structural."""
    plain = build(resolve(load_scene(SCENE), lib))
    marked = build(resolve(_engraved(_engraving()), lib))

    assert plain.layers[-1].geom.area == pytest.approx(
        marked.layers[-1].geom.area, abs=1e-6)
    assert marked.layers[-1].engrave is not None
    assert marked.layers[-1].engrave.area > 1.0


def test_cutting_through_does_change_the_part(lib):
    """And when you ask for it explicitly, it comes out of the material."""
    from hwcase.schema import Engraving

    through = Engraving(**{**_engraving().model_dump(), "through": True})
    plain = build(resolve(load_scene(SCENE), lib))
    cut = build(resolve(_engraved(through), lib))

    assert cut.layers[-1].geom.area < plain.layers[-1].geom.area
    assert cut.layers[-1].engrave is None       # it is a cut, not a mark
    assert any("cut-through" in n for n in cut.layers[-1].notes)


def test_engraving_exports_as_its_own_pass(lib, tmp_path):
    """Cut and engrave separated by colour and by layer, because that is what
    every cutter's software wants -- and because a grill sawn clean through a
    faceplate is not a grill."""
    from hwcase.export import to_dxf

    model = build(resolve(_engraved(_engraving()), lib))

    svg = to_svg(model)
    assert 'data-role="engrave"' in svg
    assert "#0000ff" in svg                     # not the red cut stroke
    assert "do not cut" in svg

    path = to_dxf(model, tmp_path / "out.dxf")
    import ezdxf
    doc = ezdxf.readfile(path)
    engrave_layers = [l.dxf.name for l in doc.layers
                      if l.dxf.name.endswith("_ENGRAVE")]
    assert engrave_layers, "the DXF needs a separate engrave layer"
    assert any(e.dxf.layer.endswith("_ENGRAVE")
               for e in doc.modelspace().query("LWPOLYLINE"))


def test_an_engraving_off_the_plate_is_reported(lib):
    """Silently drawing nothing is how you discover it after the cut."""
    from hwcase.schema import Engraving

    far = Engraving(name="miles away", pattern="rule",
                    at=(5000.0, 5000.0), size=(40.0, 10.0))
    model = build(resolve(_engraved(far), lib))
    assert any("falls outside the lid" in n for n in model.layers[-1].notes)


def test_engraving_survives_a_scene_round_trip(tmp_path):
    """It is part of the document, not a preview setting."""
    from hwcase import scenefile
    from hwcase.schema import Scene

    scene = _engraved(_engraving())
    path = tmp_path / "s.yaml"
    path.write_text(scenefile.dumps(scene), encoding="utf-8")
    back = Scene.model_validate(
        __import__("yaml").safe_load(path.read_text(encoding="utf-8")))
    assert len(back.engravings) == 1
    assert back.engravings[0].name == _engraving().name
    assert back.engravings[0].pattern == _engraving().pattern


# ---------------------------------------------------------------------------
# the photographic renderer
# ---------------------------------------------------------------------------
#
# Mitsuba is optional and almost certainly not installed on the machine
# running these, so what is tested is the translation: the scene we hand it,
# and the meshes we write beside it. Everything here runs without it.

def _traced(lib, **kw):
    import tempfile

    from hwcase.raytrace import RenderSettings, build_scene_dict

    scene = load_scene(SCENE)
    res = resolve(scene, lib)
    model = build(res)
    tmp = tempfile.mkdtemp()
    return build_scene_dict(res, model, RenderSettings(**kw), Path(tmp)), Path(tmp)


def test_the_renderer_is_optional(lib):
    """Nothing in hwcase may import Mitsuba at module scope: it is a 100 MB
    optional extra, and the editor is the product."""
    import hwcase.raytrace as rt

    assert "mitsuba" not in [m.__name__ for m in vars(rt).values()
                             if hasattr(m, "__name__")]
    if not rt.available():
        with pytest.raises(RuntimeError, match="pip install mitsuba"):
            rt.render(resolve(load_scene(SCENE), lib), build(resolve(
                load_scene(SCENE), lib)), Path("nope.png"))


def test_a_scene_translates_without_mitsuba_installed(lib):
    """The translation is the part we wrote, so it is the part to test."""
    scene, _ = _traced(lib)
    assert scene["type"] == "scene"
    assert scene["integrator"]["type"] == "path"
    assert scene["sensor"]["film"]["width"] == 1280
    assert any(k.startswith("layer") for k in scene)
    assert any(k.startswith("part") for k in scene), "an empty box is not the machine"


def test_the_render_is_lit_by_the_same_light_as_the_editor(lib):
    """A final image that looks nothing like what you designed under is not
    much use, so both use the same vendored CC0 environments."""
    scene, _ = _traced(lib, env="daylight")
    env = scene["environment"]
    assert env["type"] == "envmap"
    assert Path(env["filename"]).name == "daylight.hdr"
    assert Path(env["filename"]).exists()


def test_a_missing_environment_still_renders(lib):
    """Someone who deleted the HDRIs should get a duller picture, not a black
    frame and a stack trace."""
    scene, _ = _traced(lib, env="no-such-environment")
    assert scene["environment"]["type"] == "constant"


def test_triangulation_covers_the_polygon_exactly():
    """Holes are the whole difficulty: a case layer is mostly holes."""
    from shapely.geometry import Point
    from shapely.geometry import box as shbox

    from hwcase.raytrace import _triangulate

    poly = (shbox(0, 0, 50, 30)
            .difference(shbox(10, 10, 20, 20))
            .difference(Point(40, 15).buffer(4, quad_segs=16)))
    tris = _triangulate(poly)
    assert tris
    from shapely.geometry import Polygon as Shp
    covered = sum(Shp(t).area for t in tris)
    assert covered == pytest.approx(poly.area, rel=1e-6)


def test_the_meshes_are_valid_ply(lib):
    """Written by hand, so worth checking byte for byte -- a malformed header
    fails deep inside someone else's loader with a useless message."""
    import numpy as np

    _, work = _traced(lib)
    files = sorted(work.glob("*.ply"))
    assert files

    raw = files[0].read_bytes()
    cut = raw.index(b"end_header\n") + len(b"end_header\n")
    header = raw[:cut].decode("ascii")
    assert header.startswith("ply\nformat binary_little_endian 1.0")

    nv = int(next(l for l in header.splitlines()
                  if l.startswith("element vertex")).split()[-1])
    nf = int(next(l for l in header.splitlines()
                  if l.startswith("element face")).split()[-1])
    body = raw[cut:]
    assert len(body) == nv * 12 + nf * 13      # 3 floats; 1 count + 3 uint32

    faces = np.frombuffer(body[nv * 12:], np.uint8).reshape(nf, 13)
    assert (faces[:, 0] == 3).all(), "every face has to be a triangle"
    idx = faces[:, 1:].copy().view(np.uint32)
    assert int(idx.max()) < nv, "an index past the end of the vertex list"


def test_the_camera_looks_at_the_machine(lib):
    """Built with numpy rather than by Mitsuba, so it is ours to get wrong."""
    import numpy as np

    from hwcase.raytrace import _transform_look_at

    m = np.array(_transform_look_at((100.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    assert m[:3, 3] == pytest.approx([100, 0, 0])
    assert m[:3, 2] == pytest.approx([-1, 0, 0])          # forward is +Z local
    for col in range(3):                                   # orthonormal
        assert np.linalg.norm(m[:3, col]) == pytest.approx(1.0)
    assert float(np.dot(m[:3, 0], m[:3, 1])) == pytest.approx(0.0, abs=1e-9)


def test_looking_straight_down_does_not_degenerate():
    """The up vector and the view direction go parallel, and a naive cross
    product gives a zero-length right vector and a matrix full of NaN."""
    import numpy as np

    from hwcase.raytrace import _transform_look_at

    m = np.array(_transform_look_at((0.0, 0.0, 200.0), (0.0, 0.0, 0.0)))
    assert np.isfinite(m).all()
    assert np.linalg.norm(m[:3, 0]) == pytest.approx(1.0)


def test_engraving_reaches_the_render(lib):
    """It is on the panel in the export, so it is on the panel in the picture."""
    import tempfile

    from hwcase.raytrace import RenderSettings, build_scene_dict
    from hwcase.schema import Engraving

    scene = load_scene(SCENE)
    scene.engravings = [Engraving(name="vent", pattern="fins",
                                  at=(0.0, 0.0), size=(70.0, 40.0))]
    res = resolve(scene, lib)
    model = build(res)
    sd = build_scene_dict(res, model, RenderSettings(), Path(tempfile.mkdtemp()))
    assert any(k.startswith("engrave") for k in sd)


def test_acrylic_renders_as_glass_not_as_alpha(lib):
    """The reason to reach for a raytracer on an acrylic-lidded box is the
    refraction; alpha blending gives none of it."""
    from hwcase.raytrace import _bsdf
    from hwcase.schema import Material

    class _L:
        material = Material(name="acrylic-clear-3mm", thickness=3.0)

    assert _bsdf(_L()).get("type") == "roughdielectric"

    class _P:
        material = Material(name="plywood-3mm", thickness=3.0)

    assert _bsdf(_P()).get("type") == "roughplastic"


# ---------------------------------------------------------------------------
# auditing the library against the models it claims to come from
# ---------------------------------------------------------------------------

@needs_cad
def test_the_measured_parts_agree_with_their_models(lib):
    """The one automated second opinion on a part file.

    The 1.5" OLED was wrong for weeks; this is what would have caught it on
    the day it was written, because the outline it declared disagreed with the
    mesh it named.
    """
    from hwcase.audit import audit_library

    bad = []
    for a in audit_library(lib):
        for f in a.findings:
            if f.severity != "info" and f.kind in ("outline", "thickness"):
                bad.append(f"{a.part}: {f.message}")
    assert not bad, "declared geometry disagrees with the vendor model:\n" + \
                    "\n".join(bad)


@needs_cad
def test_the_oled_agrees_with_its_own_mesh(lib):
    """It is the part that started all this, so it gets its own check."""
    from hwcase.audit import audit_part

    result = audit_part(lib["adafruit-4741-oled-1v5"])
    assert result.checked
    assert result.ok, [f.message for f in result.findings]


def test_a_datasheet_is_not_a_failed_measurement(lib):
    """`cad:` is also used for provenance -- a PDF or an annotated photo. That
    is information, not a part we failed to read."""
    from hwcase.audit import audit_part

    result = audit_part(lib["rpi-3b"])
    assert not result.checked
    kinds = {(f.kind, f.severity) for f in result.findings}
    assert ("not-a-model", "info") in kinds
    assert not [f for f in result.findings if f.severity == "error"]


@needs_cad
def test_the_audit_understands_a_part_built_upside_down(lib):
    """A vendor model may be built either way up and a part file is entitled
    to turn it over. Comparing reaches without allowing for that flagged the
    OLED and the encoder, both of which were correct."""
    from hwcase.audit import audit_part

    for pid in ("adafruit-4741-oled-1v5", "adafruit-5752-quad-encoder"):
        result = audit_part(lib[pid])
        assert not [f for f in result.findings if f.kind == "reach"], pid


def test_the_audit_catches_a_part_that_lies(lib, tmp_path):
    """And it has to actually fail when the numbers are wrong, or it is just
    a slow way of printing the library."""
    from hwcase.audit import audit_part

    part = lib["adafruit-4741-oled-1v5"].model_copy(deep=True)
    part.outline = part.outline.model_copy(update={"size": (99.0, 99.0)})
    result = audit_part(part)
    assert result.checked
    assert any(f.kind == "outline" for f in result.findings)
