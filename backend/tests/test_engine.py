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


def _base(name):
    """`buttons[2,3]` -> `buttons`: repeat grids suffix their instances."""
    return name.split("[")[0]


def test_everything_on_the_panel_lands_flush(demo):
    z = demo.panels["main"]
    flush = {"screen.active_area", "pad_a.buttons", "pad_b.buttons",
             "encoders.bushings", "oled.active_area", "amy.panel_face"}
    seen = set()
    for s in demo.solids:
        ref = f"{s.placement}.{_base(s.name)}"
        if ref in flush:
            seen.add(ref)
            assert s.z[1] == pytest.approx(z, abs=1e-6), f"{s.ref} is not flush"
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
    scene = load_scene(SCENE)
    oled = next(p for p in scene.placements if p.id == "oled")
    mux = next(p for p in scene.placements if p.id == "mux")
    oled.on_panel = None
    mux.pos = (oled.pos[0], oled.pos[1], 0.0)
    oled.pos = (mux.pos[0], mux.pos[1], 40.0)     # well clear above
    issues = check(resolve(scene, lib), lib)
    assert not any(i.code == "collision" for i in issues)
    assert not any(i.code == "tight_overlap" for i in issues)


def test_passing_too_close_over_is_reported(lib):
    scene = load_scene(SCENE)
    oled = next(p for p in scene.placements if p.id == "oled")
    mux = next(p for p in scene.placements if p.id == "mux")
    oled.on_panel = None
    mux.pos = (oled.pos[0], oled.pos[1], 0.0)
    mux_top = max(s.z[1] for s in resolve(scene, lib).solids if s.placement == "mux")
    oled_bottom = min(v.z_min() for v in lib["adafruit-4741-oled-1v5"].volumes)
    oled.pos = (mux.pos[0], mux.pos[1], mux_top - oled_bottom + 0.4)   # 0.4 mm gap
    issues = check(resolve(scene, lib), lib)
    assert any(i.code == "tight_overlap" for i in issues), \
        "0.4 mm of clearance should be called out"
    assert not any(i.code == "collision" for i in issues), "but it is not a collision"
