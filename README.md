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

Left: the part library, click to drop one into the scene. Middle: the 3D view —
drag a part to move it in XY, shift+drag for Z, arrows nudge (shift = 0.1 mm),
`R` rotates 90°, `Del` removes. Right: placements, the selected part's numbers,
and the live issue list — click an issue to select the part it blames. Mated
parts (the screen on the Pi) can't be dragged; they follow their parent, and you
change `gap` instead. Toggles for the generated case layers and the plug
corridors are in the header.

Every move re-posts the scene to the backend, so the issue list is always the
engine's opinion, never the browser's guess. `save` writes back to
`backend/scenes/<name>.yaml` and keeps the previous file as `.yaml.bak` — a
round trip through the editor cannot preserve YAML comments.

## Or from the command line

```bash
cd backend
pip install -r requirements.txt

python -m hwcase.cli parts                                  # what's in the library
python -m hwcase.cli check scenes/soundmachine-v0.yaml      # will this work?
python -m hwcase.cli build scenes/soundmachine-v0.yaml -o ../out
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
sitting in as its own Eurorack patchbay panel. 336 × 130 × 32 mm, 13 layers of
3 mm ply.

The Pi is rotated 180° in that scene, and that is not cosmetic: with the USB
stack facing the button block, the checker reported that four USB plugs needed
45 mm of straight run straight through the keypads. Turning the anchor was the
fix. That exchange is the whole point of the tool.

## Roadmap

Done: part schema with provenance, YAML library, mate solver for board stacking,
collision / connector-access / exposure / cable-clearance checks, slab
generation with kerf compensation, SVG + DXF export, vendor CAD ingest.

Also done: FastAPI over the same engine, a vendored three.js editor
(select / drag / nudge / rotate / add / delete / save / export), and a
one-click `start.bat`.

Next:
1. **Snapping** — the editor moves parts freely today. Wants: snap to mates
   (drop the pad on the Trellis and have it click on), snap to part edges and
   to a grid, and alignment guides.
2. **Panel plane awareness** — everything the user touches should land on one
   surface; the editor should offer "put this on the panel" instead of making
   you work out z by hand.
3. **Real nesting** — layers currently tile naively and overflow one sheet;
   needs bin packing and multi-sheet output.
4. **Joinery** — finger joints between slabs, or the simpler standoff-and-rod
   stack. Kerf is modelled, joints are not.
5. **Solid export** — optional build123d backend to emit STEP for milling, from
   the same slab stack.
6. **Cable routing** — currently a keepout distance; wants actual routes with
   fixed STEMMA QT cable lengths (50/100/200 mm) as a constraint.
