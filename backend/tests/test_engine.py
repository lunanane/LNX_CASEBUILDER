"""Guards on the things a wrong answer would quietly cost a sheet of plywood."""

from pathlib import Path

import pytest

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


def test_open_side_removes_the_floor_beyond_that_edge(lib):
    from hwcase.schema import Face

    scene = load_scene(SCENE)
    solid_floor = build(resolve(scene, lib)).layers[0].geom.area

    _pi_side(scene, Face.ny, cutout="open_side")
    res = resolve(scene, lib)
    assert len(res.side_openings) == 1
    opened = build(res)
    assert opened.layers[0].geom.area < solid_floor * 0.95, \
        "opening a side should take a visible bite out of the floor"


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
        return build(resolve(scene, lib)).layers[0].geom.area

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
    low = min(v.z_min() for v in lib["adafruit-4741-oled-1v5"].volumes)
    scene.placements.append(Placement(
        id="over", part="adafruit-4741-oled-1v5", mount="manual",
        pos=(10.0, 0.0, hub_top - low + 0.4)))    # 0.4 mm of clearance
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
