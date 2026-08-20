# Hardware Design — parametric cases for sound machines

Lay real hardware modules out in 3D, snap them together the way they actually
bolt together, check that the plugs and cables can physically get where they
need to go, and emit a case: a stack of laser-cut layers today, a milled or
printed solid later.

Nothing off the shelf does this. Box generators ([Boxes.py](https://github.com/florianfesti/boxes)
and friends) make empty boxes; EDA tools lay out parts *inside* a PCB. The
middle — "here are eight modules, wrap a case around them without blocking the
HDMI port" — is what this is.

## The idea in one paragraph

A case is a **stack of slabs**. Each slab is one sheet of material and one 2D
polygon: the outer shape minus the pockets the hardware needs at that height,
minus the corridors the connectors need through the walls. Cut the stack on a
laser and it *is* the case. Mill it and each slab is one pocket depth on a 2.5D
toolpath. Extrude each slab and you have the solid for a preview or a STEP. So
the same model serves layered plywood, milled aluminium and 3D printing, and it
is all 2D polygon booleans — no CAD kernel in the hot path, fast enough to
re-run on every drag in the editor.

## Layout

```
backend/
  hwcase/
    schema.py     part / scene / case data model (pydantic), coordinate conventions
    geom.py       transforms, footprints, connector corridors
    library.py    YAML part loader
    scene.py      mate solver + the checks (collision, access, exposure, clearance)
    case.py       scene -> stack of slabs
    export.py     SVG / DXF for the cutter, JSON for the browser
    cli.py        hwcase parts | check | build
    api.py        FastAPI: /api/parts, /api/resolve, /api/build, /api/export/*
  parts/          the part library -- data, not code
  scenes/         layouts
  tests/          pytest suite over the engine
docs/
  research.md     what already exists and what we build ourselves
  measurements.md what is exact, what is guessed, what to measure next
web/            the editor: three.js (vendored, no build step, no CDN)
tools/
  fetch_cad.py    mirror vendor STEP/STL into vendor/cad/
  measure_cad.py  read exact dimensions + z profiles out of those meshes
vendor/           downloaded datasheets and CAD (not authored here)
```

## Run the editor

```
start.bat
```

That is the whole thing. It builds a `.venv` beside itself on first run
(~2 minutes while shapely/trimesh/numpy download), installs the requirements,
checks the engine imports, finds a free port from 8765 up, starts the server and
opens the browser. Later runs skip straight to the server in a few seconds —
dependencies are only reinstalled when `backend/requirements.txt` actually
changes. `start.bat 9000` picks the port; `start.bat 9000 bare` skips auto-reload
and the browser. Ctrl+C stops it.

### In the editor

Left: the part library, click to drop one into the scene. Middle: the 3D view.
Right: placements, the selected part's numbers, and the live issue list — click
an issue to select the part it blames.

Selecting a part puts a gizmo on it: an amber **move pivot** on its centre with
the two axes it slides along, and a blue **rotate ring** further out with one
handle. Drag the pivot or the part body to slide it in its plane; drag the ring
handle to turn it (hold shift for 15° steps); drag empty space to orbit the
camera. The ring is a fixed size rather than scaled to the part, so it stays the
same target whether you grabbed a 25 mm breakout or the 128 mm Eurorack panel.
`90°` buttons in the header and in the inspector turn the selection a quarter
turn, as does `R`. Arrows nudge 1 mm, shift+arrows 0.1 mm, `Del` removes.

**Snapping** is on by default: while you drag, the moving assembly's edges and
centreline are matched against every other board's, and the nearest match within
2 mm wins — independently in x and y, with a green guide line showing what it
caught. That is how two 60 mm Trellis boards end up *exactly* 60 mm apart with
the 15 mm button pitch continuous across the seam; there is a test for precisely
that. Hold **alt** to place something off-grid, or turn `snap` off in the header.
A snapped coordinate is kept exact rather than rounded to 0.1 mm on drop.

Rotation pivots on the part's own centre, not its local origin — otherwise every
`origin: min` part would swing around a corner and fly off the bench.

Parts named by an error render **ghosted red**; everything clean renders solid,
so you can shove things around and read the result without looking away from the
viewport. The case outline and the issue list update live during the drag.

Mated parts (the screen on the Pi) can't be dragged; they follow their parent,
and you change `gap` instead. Toggles for the case layers, the plug corridors and
the panel plane are in the header.

Clicking any board in a mate stack manipulates the whole stack — a silicone pad
glued to a Trellis has no position of its own, so grabbing the pad moves the
Trellis and everything on it, and the gizmo is sized to the assembly rather than
buried inside it.

The `90°` buttons snap to an **absolute** multiple of 90 rather than adding a
quarter turn to whatever the free-rotate ring left behind: free-rotate to 37°,
press it and you land on 90° or 0°, never 127°.

Every move re-posts the scene to the backend, so the issue list is always the
engine's opinion, never the browser's guess. `save` writes back to
`backend/scenes/<name>.yaml` and **keeps the file's comments** — it merges the
new values into the existing YAML document rather than dumping over it, so the
reasoning recorded in those files survives an edit. Saving is idempotent.

## Or from the command line

```bash
cd backend
pip install -r requirements.txt

python -m hwcase.cli parts                                  # what's in the library
python -m hwcase.cli check scenes/soundmachine-v0.yaml      # will this work?
python -m hwcase.cli build scenes/soundmachine-v0.yaml -o ../out
python -m pytest tests/                    # engine suite
```

The editor's own maths is tested headlessly too, with no browser and no
dependencies:

```bash
cd web && node --test tests/
```

`build` writes `*-layers.svg`, `*-layers.dxf`, plus `*-scene.json` and
`*-case.json` for the browser.

To add a board: drop a YAML file in `backend/parts/`. To get its dimensions
right, look for it in [`adafruit/Adafruit_CAD_Parts`](https://github.com/adafruit/Adafruit_CAD_Parts)
first:

```bash
python tools/fetch_cad.py 3954 4741 5752
python tools/measure_cad.py --band 0.8 "vendor/cad/adafruit/5752/*.stl"
```

That is how we found out the 5752 quad encoder is a 76.2 × 21.6 mm strip and
not the 25.6 mm square every shop page claims. See [docs/measurements.md](docs/measurements.md).

## Panels: the surface you touch

Nothing in this machine wants a hand-computed z. A **panel** is the plane your
eyes and fingers meet, and it is *derived*, not typed:

```yaml
panels:
  - name: main
    from_ref: screen.active_area     # the screen fixes the height of the face
```

Then every other part just says which of its own features has to sit flush:

```yaml
  - id: encoders
    part: adafruit-5752-quad-encoder
    on_panel: main
    panel_ref: bushings      # the threaded collar clamps the panel;
                             # the shaft is meant to stand proud for the knob
```

`panel_ref` names any volume in the part **or in anything mated on top of it**,
so `trellis_a` can reference `buttons`, which belongs to the elastomer pad
sitting on it. `auto` picks the highest actuator/display, `top` the highest
point of the whole stack, and `panel_offset` sets it proud or recessed.

The payoff: change `mate_gap` under the screen and the panel moves, and every
keypad, knob and window moves with it. There is a test for exactly that. The
checker also warns when an actuator ends up *below* its panel, because you could
not press it.

## Getting the cables out: per-side cutout policy

The ports that end up *under* a faceplate are the awkward ones, and how you let
them out changes the whole look of the case. So it is a decision **per side of a
board**, not a global rule:

```yaml
  - id: pi
    part: rpi-3b
    sides:
      - side: -y
        cutout: open_side       # everything beyond this edge goes, from the
        headroom: 2.0           # floor up to 2 mm above the cable
        span: full              # or `board`, to keep the neighbours' floor
      - side: +x
        cutout: per_connector   # one opening each, plug + bend only
        include: [usb1, hdmi]   # and only for the ports you actually use
```

| `cutout` | what the case does |
|---|---|
| `none` | leave the wall solid — nothing gets out on this side |
| `per_connector` | one opening per port, plug and cable-bend sized (the default) |
| `open_to_edge` | the same openings run out to the edge as slots, so an already-fitted plug can be threaded in from outside |
| `open_side` | remove everything beyond that edge from the floor up to just above the cable — a **seamless faceplate over an open, variable underside** |

`include` names which of that side's ports count. Left out, the case makes room
for the external ones and ignores internal wiring; naming a list overrides that
in both directions — drop a port you will never plug into, or add an internal
one you want to reach.

`under_panel: true` on a placement means the faceplate passes over that board
unbroken: no window, no actuator hole. The checker errors if the board is too
tall to actually fit under the plate.

## Features that repeat

A 4x4 keypad is sixteen 10 mm buttons on a 15 mm pitch, not one 55 mm square —
and the difference is the difference between a faceplate and a hole. Volumes
take a `repeat` grid and a `shape`:

```yaml
      - name: buttons
        at: [30.0, 30.0]
        size: [10.0, 10.0]
        corner_radius: 1.5
        repeat: {count: [4, 4], pitch: [15.0, 15.0]}   # centred on `at`
      - name: bushings
        size: [6.8, 6.8]
        shape: circle                                   # round hole, round part
        repeat: {count: [4, 1], pitch: [19.05, 0.0]}
```

The grid is centred on `at`, so the pad's buttons land at 7.5 / 22.5 / 37.5 /
52.5 mm from each edge and stay continuous across tiled pads. Instances are
named `buttons[2,3]` in the issue list.

## Coordinate conventions

Part-local: X and Y in the board plane, +Z out of the component side, **z = 0 at
the PCB bottom face**. So the board is `[0, pcb_thickness]`, every datasheet
"Z-Height" (quoted from the top surface) is `pcb_thickness + Z`, and stacking a
HAT is a z offset. Rectangular outlines use `origin: min` so local coordinates
match the drawing you read them off.

## The current machine

`scenes/soundmachine-v0.yaml`: Pi 3B as the anchor with the HyperPixel 4.0
Square *mated* to its 40-way header (so the screen follows the Pi, not the other
way round), 2 × NeoTrellis + elastomer pads as a 4 × 8 button grid, the quad
encoder strip, the 1.5" OLED, the Grove I²C hub buried inside, and the AMYboard
sitting in as its own Eurorack patchbay panel. 336 × 130 × 40 mm, 15 layers of
3 mm ply — the panel plane lands at z = 25.4, derived from the screen glass, and
the encoder shafts stand 7.8 mm proud of it for the knobs.

The Pi is rotated 180° in that scene, and that is not cosmetic: with the USB
stack facing the button block, the checker reported that four USB plugs needed
45 mm of straight run straight through the keypads. Turning the anchor was the
fix. That exchange is the whole point of the tool.

## Roadmap

Done: part schema with provenance, YAML library, mate solver for board stacking,
collision / connector-access / exposure / cable-clearance checks, slab
generation with kerf compensation, SVG + DXF export, vendor CAD ingest.

Also done: FastAPI over the same engine, a vendored three.js editor
(select / drag / nudge / rotate / add / delete / save / export), a one-click
`start.bat`, derived panel planes, and a 27-case test suite (`pytest` from
`backend/`).

Next:
1. **Snapping** — the editor moves parts freely in XY. Wants: snap to mates
   (drop the pad on a Trellis and have it click on as a child), snap to part
   edges and to a grid, and alignment guides. The 60 mm Trellis tiling is the
   case that needs it most.
2. **Real nesting** — layers currently tile naively and overflow one sheet;
   needs bin packing and multi-sheet output.
3. **Joinery** — finger joints between slabs, or the simpler standoff-and-rod
   stack. Kerf is modelled, joints are not.
4. **Solid export** — optional build123d backend to emit STEP for milling, from
   the same slab stack.
5. **Cable routing** — currently a keepout distance; wants actual routes with
   fixed STEMMA QT cable lengths (50/100/200 mm) as a constraint.
