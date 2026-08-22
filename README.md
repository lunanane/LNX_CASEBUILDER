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
    engrave.py    front-panel decoration, as a pass the cutter keeps separate
    hershey.py    single-stroke lettering for those decorations
    measure.py    read geometry out of vendor CAD -- the one place that does
    catalog.py    search Adafruit's live product feed
    ingest.py     turn a catalogue hit into a measured draft part
    audit.py      check the library against the models it claims to come from
    raytrace.py   the final photograph (optional: needs Mitsuba)
    export.py     SVG / DXF for the cutter, JSON for the browser
    cli.py        hwcase parts | check | build | audit
    api.py        FastAPI: /api/parts, /api/resolve, /api/build, /api/export/*,
                  /api/catalog/*, /api/render
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
web/vendor/hdri/  three CC0 environments from Poly Haven (CREDITS.json)
vendor/fonts/     the Hershey stroke fonts (public domain, 1967)
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

Across the top: **file**, **edit**, **view** and **help** menus, then the scene
picker, and the controls you reach for constantly — the case toggle, the
interior mode and the render switch. Anything used once a session is in a menu;
anything toggled while working stays on the bar. (It used to be fourteen buttons
in a row, and the last four were off the edge of the screen, which is the same
as not existing.)

Left: the part library, click to drop one into the scene, with the catalogue
search underneath. Middle: the 3D view. Right: four panes —

- **layout** — placements and the selected part's numbers
- **case** — size, the bolts that hold the stack together, minimum web,
  and the materials with their colours
- **look** — lighting, contact shading, engraving, and the final render
- **issues** — the live list, badged so a problem is visible from any pane

The case settings used to live below everything about the selected board, so
you only met them by scrolling past a screenful of something else.

**help → how this works** is the manual; **keyboard & mouse** is the shortcut
list that used to be a permanent strip along the bottom; **about** says which
version computed the geometry. <kbd>F1</kbd> opens the manual.

Click an issue to select the part it blames.

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

### Variations

`new` starts an empty scene, `duplicate` copies the current one under a new name
and switches to it, `save as` does the same with your unsaved changes included,
and `rename` moves it. Duplicating and renaming copy the **file**, not a
re-serialised model, so the reasoning written into a scene's comments comes
along with its geometry.

Scene names become filenames, so they are validated rather than trusted: letters,
digits, spaces, dot, dash and underscore, and nothing that could climb out of
`backend/scenes/`. Creating or renaming onto an existing name is refused rather
than silently overwriting.

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

## Where a part comes from

The palette lists parts somebody has measured and vouched for. The **find a
part** box under it searches Adafruit's live catalogue — about 5,500 products —
and imports one on demand. Around 470 of them have a vendor CAD model, and the
search says which: a product with a model can be measured, one without would
have to be entered by hand.

Both feeds are cached on disk and refreshed daily, so a self-hosted instance
picks up new products the week they ship, and a search still works with the
network unplugged. If Adafruit is down you get yesterday's catalogue and a note
saying so, which is more useful than an empty box.

### What an import actually gives you

Real: the outline, the board thickness, the volumes that stand proud of it, and
the mounting holes (out of the STEP — an STL of a board is a slab with the
drilling gone).

Guessed, and labelled as such in the generated file:

- **which way up.** Nothing in a mesh says "this face is the front". Two rules
  decide it: one large flat body alone on a face is a display module and that
  face goes up; otherwise the face the model stands furthest proud of goes up,
  because knobs and sockets are tall and a solder side is flat.
- **what the volumes are.** We know a 4.95 × 6.00 × 2.96 box sits at the left
  edge. We do not know it is a STEMMA QT socket, and the case treats a socket
  differently from a capacitor.
- **connectors — there are none.** A mesh cannot say where a cable plugs in, so
  a freshly imported board asks the case for *no cable room whatsoever* until
  you add them. This is the one to remember.

Imports land in `backend/parts/imported.yaml`, on their own, so a machine never
rewrites a hand-written file and a bad import is one file to delete.

### Bodies, not bands

`measure.py` splits a mesh into connected shells rather than slicing it into
horizontal bands. Band slicing was wrong twice over: an STL of a flat plate has
vertices only on its two faces, so a 1.6 mm PCB reads as two paper-thin slivers
with a void between them; and a band's bounding box merges everything at that
height, so the 1.5" OLED's two STEMMA QT connectors — on opposite edges —
became one imaginary 34 mm "strip across the display". A display was then fitted
to that fiction, which is why its window was two planes crossing each other.

Splitting on connectivity asks the model what objects it contains instead of
inferring them.

### Checking the library against itself

```
python -m hwcase.cli audit
python -m hwcase.cli audit --tolerance 0.5 --strict
```

Re-measures every part that names a `cad:` file and reports where the file and
the model disagree — outline, board thickness, and how far anything stands
proud. It understands that a part may be modelled upside down relative to how
we use it, and that a `cad:` pointing at a PDF is provenance rather than a
failed measurement.

Worth running periodically: vendors revise boards and quietly replace models,
and our own extraction changes. The OLED was wrong for weeks, and this would
have shown it on the day it was written.

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
| `open_to_edge` | the same openings run out to the edge as slots |
| `channel` | a groove of `channel_width` from **each** port out through the wall, open all the way **down to the underside** |
| `open_side` | one opening spanning **all** the selected ports of that side, likewise open to the underside |

**`margin`** decides how close the outside of the case comes to that side's
ports. Left unset, the wall is offset from the bounding box of the *whole*
scene — so a port on a board that is not the outermost one ends up buried,
however thin you make the wall. Set `margin: 2.5` and the case wall on that side
is brought to exactly 2.5 mm from the socket mouth. It only moves its own edge,
two boards pulling the same edge take the outermost, and it will never come
inside the hardware.

Connectors also carry a `cutout_shape`: a 3.5 mm jack asks for `circle` and gets
a round hole rather than a 7 mm square. An explicit `cutout` is treated as a
measurement and is never widened.

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

## What the inside looks like

A case full of I²C leads needs somewhere to put them. Left solid, the middle
layers are a block of plywood with a pocket per board and nowhere for a wire to
run; hollowed out completely, the faceplate has nothing holding it up. So the
interior is a choice, on the case spec or from the header dropdown:

| `interior` | the middle layers become |
|---|---|
| `pocketed` | solid, one pocket per part, corridors for the external ports (the default — unchanged) |
| `hollow` | a wall of `wall` thickness all the way round, empty inside |
| `ribs` | hollow plus stiffeners on a `rib_spacing` grid, `rib_width` wide, routed around the hardware and the cables |
| `grown` | pockets grown to clear **every** cable port plus `cable_clearance` around each board, so the wall is as thick as it can be without crushing a wire |

On the current machine that runs 75% of solid for `pocketed`, 66% for `grown`,
27% for `ribs` and 12% for `hollow`.

Ribs are pruned: a stiffener chopped free by a cable run would be an offcut on
the cutting bed and no help to the faceplate, so only fragments still joined to
the surrounding wall survive. There's a test asserting every piece of every
ribbed layer touches the wall, and another asserting no rib sits on a board.

The floor and the lid are structural faces and keep their own rules whatever the
interior is set to.

## Rendering it

The `render` toggle in the header swaps the schematic view for a physically
based one: standard materials, ACES tone mapping, a shadow-casting sun you steer
with the three sliders (azimuth, elevation, strength). The case stops being
wireframe outlines and becomes real extruded slabs with their holes punched
through, sitting on a shadow-catching ground plane.

**Light** comes from the **look** pane. `room` is the synthetic box generated at
startup — instant, offline, and lit from nowhere in particular. The other three
are real captured environments (Poly Haven, CC0, credits in
`web/vendor/hdri/CREDITS.json`), vendored at 1k because a PMREM cares far more
about the light in an environment than its resolution. They load on demand and
are cached, so nobody pays five megabytes for a schematic view they never leave.

**Contact** darkens the walls of each slab where it meets the ones above and
below. A stack of flat sheets has almost nothing concave in it, so a
screen-space AO pass finds very little to darken and costs a whole
postprocessing chain to run; what actually reads as *separate pieces of
plywood* is the shadow line in the seam, and that is a function of position
within the slab, so it is simply baked in.

Material appearance comes from the material's **name**: `plywood-3mm` is matte
and pale, `acrylic-clear-3mm` is glossy and see-through, `aluminium-2mm` is
metallic. Presets live in [web/finishes.js](web/finishes.js) and are matched by
precedence rather than by length, so `acrylic-smoked-3mm` resolves to *smoked* —
the qualifier — and not to *acrylic*, even though it is the shorter word. A
`color` declared in the scene always overrides the preset's colour while keeping
its feel, and the **case materials** panel in the inspector lets you set colour,
name and thickness live.

Schematic mode keeps the grid, gizmo, guides and diagnostic overlays; render
mode drops them, because they are for laying out rather than looking at.

### The final photograph

**render a photograph** in the look pane traces the scene properly on the
server, using the same environment the preview is lit by — a final image that
looks nothing like what you designed under is not much use. Minutes, not
milliseconds, and synchronous on purpose: a request that takes two minutes is a
clearer signal than a job id to poll.

This needs **Mitsuba**, which is an optional install:

```
.venv\Scripts\python -m pip install mitsuba
```

About 50 MB, and it brings drjit with it. On startup it prints
`jitc_llvm_init(): LLVM API initialization failed` — that is the GPU/JIT
variant announcing it is unavailable, and it is harmless: the renderer asks
for `scalar_rgb`, one ray at a time on the CPU. A 1100 x 825 frame at 96
samples takes about 35 seconds on a laptop.

Everything else works without it, and the button says so rather than failing
when pressed. Mitsuba was picked over the alternatives because it is the only
one that drops into this stack unaided: `bpy` publishes no wheel for the Python
here (so Blender means installing Blender), LuxCore would need its own
translation layer for no gain, and `three-gpu-pathtracer` — which would have
been tidiest, since the editor already has a three.js scene — needs three
≥ 0.180 against the r169 we vendor, plus a build step.

Acrylic renders as a real dielectric rather than as alpha blending: the reason
to reach for a raytracer on an acrylic-lidded box is the refraction and the
edge glow, and alpha gives neither.

## Engraving the front panel

A laser does two jobs. A **cut** goes through the sheet and changes the shape of
the part; an **engrave** marks the surface and changes nothing structural.
Mixing them up is how a panel ends up with a speaker grill sawn clean out of it,
so engraving lives in its own group in the SVG (blue, titled *do not cut*) and
its own `_ENGRAVE` layer in the DXF, never merged with the outline.

Seven patterns, from the **look** pane: `text` for labelling, `fins` (the
amplifier front-panel look), `slots` (a speaker grill, offset row to row — a
square grid looks like a spreadsheet), `rings`, `hex`, `rule` for separating
groups of controls, and `frame`. Each carries a preset that looks right without
fiddling, and switching pattern brings its preset with it, so a hex mesh does
not inherit fin spacing.

### Labels

`text` uses a **stroke** font, not an outline one. A normal font describes the
edge of a letter, which a laser then has to fill — slow, and at label size the
fill closes up into a blob. A stroke font describes the path down the middle of
each letter, which is exactly what an engraving head wants: one fast pass, and a
3 mm character that is still legible.

The Hershey fonts have been the answer to this since 1967. They are public
domain, they were drawn for plotters, and two weights are vendored in
`vendor/fonts/`: `futural` (light) for small labels and `futuram` (medium) for
anything read across a room.

Two things about the sizing are deliberate:

- **`height` is cap height in millimetres**, not em size. It is the dimension
  you actually measure on a finished panel; em size would make a "6 mm" label
  come out about 4 mm tall.
- **a label is never scaled to fit its box.** The box is where it sits, not how
  big it is. Squashing a 6 mm label because someone dragged a small box would
  make the one number anyone checks untrue — so it overhangs instead, and says
  by how much.

Newlines start a new line, alignment is on the margins, and the block is centred
on its *ink* rather than on its advance width, so `at` means the middle of what
you can see. A character the font does not have is skipped rather than replaced
with a box: a tofu glyph engraved into a faceplate is permanent.

Marks are clipped to the lid: a grill that runs off the edge of the plate is not
a grill, it is a row of nicks in the outline, and one that misses the plate
entirely says so instead of silently drawing nothing.

**cut right through** is a separate switch with a warning on it. It moves the
pattern out of the engraving pass and into the geometry — the one case where the
two are allowed to meet — and you are on your own for whether the panel still
holds together.

## Case size: auto or pinned

By default the case outline is derived from the bounding box of the hardware
plus the wall. That is right while you are laying out, and wrong the moment you
want a connector flush with the outside — push a board outward and the wall
moves out with it, so the gap never closes.

The **case size** control in the inspector switches it to `fixed`: the current
derived outline is frozen in place, and from then on moving a board moves it
*relative to a stationary wall*. Combine it with `open_to_edge` or `open_side`
on that side and the ports come right out through the surface.

## A note on anchors

`anchor` re-centres the whole scene on one placement every solve. That makes
world coordinates readable, but it also means **the anchored placement cannot be
moved** — dragging it just slides everything else the other way. The checker now
warns when a scene anchors something that is not locked. The demo scene no
longer uses one.

## Carrying a board by its screw holes

Per placement, `support` decides whether the case holds the board on its own
mounting holes — and which way round:

| `support` | what the case grows |
|---|---|
| `none` | nothing (the default) |
| `from_floor` | a column of material from the bottom plate up to the board's underside, with a clearance hole through it |
| `from_lid` | a clearance hole down through the faceplate so the board can be screwed up against its underside, plus posts across any gap |

### Flush heads or proud heads

`screw_inset` decides whether the head sinks into the outer plate. Left on
**auto** it follows the mount direction, because the right answer is opposite at
each end of the case:

- **`from_floor` → flush.** A screw head standing proud of the bottom plate
  makes the whole case rock on the bench.
- **`from_lid` → proud.** This one is not cosmetic. A counterbore removes the
  *full thickness* of the plate it is cut in. A front-mounted board sits
  directly under the faceplate, so that plate is the only thing between the
  screw head and the board — counterbore it and the head drops straight through
  and lands on the board, clamping nothing. A plain clearance hole is what
  works: the head bears on the outside face and pulls the board up against the
  inside one, which is the entire point of mounting a board to a faceplate.

Set it explicitly when there *are* layers between the plate and the board and
you want the heads flush with the faceplate. Ask for an inset that has nothing
to bear on and the build refuses it, says why, and falls back to the hole that
works — a counterbore there would look right on the drawing and hold nothing.

"Nothing to bear on" means less than half a millimetre of material, not zero:
the layers in between are whole sheets, so the gap is either essentially nothing
or at least one sheet thick, and a 0.07 mm shoulder is the former in the
latter's clothing.

The hole positions are the real ones, read out of the vendor STEP files by
`tools/extract_holes.py` — eight M2.5 on a NeoTrellis, not the four corners we
originally guessed.

Two details that matter and are tested:

- Bosses are added **after** the interior is carved out and after the board's
  own pocket is cut, or the void would eat the very post holding the board up.
  They survive `hollow`, `ribs` and `grown` alike.
- `from_lid` uses a *reach* test rather than an overlap test, because a board
  flush with the faceplate has its top exactly at the lid's top — an overlap
  measures zero and the lid never gets drilled, which is precisely the board you
  most want to screw down from above.

`support_boss`, `screw_clearance` and `screw_head` on the case spec set the post
diameter, the slop on the through hole and the counterbore.

### Case bolts carry their own material

A bolt through the stack is only a bolt if material touches it the whole way
down, and a hollowed or pocketed layer will happily have nothing at all at that
point. Cutting a circle out of nothing removes nothing, so the hole is simply
*absent* from that sheet — which looks fine on the drawing and leaves the stack
unclamped there.

So each bolt carries a **collar** (`case_screw_boss`, 9 mm) on every layer,
added before the hole is drilled through it, exactly as a board support carries
a boss. The collar is clipped to the outline so it can never grow the case, and
cut back from any hardware at that height — material pressed against a board is
the same mistake as a bolt through one. Set it to 0 to drill only where
material already happens to be.

On the demo scene that is the difference between 6 unsupported bolt/layer pairs
and none — 24 with the `grown` interior, which routes cable space through
exactly the corners the bolts want.

## Two connectivity guarantees

Whatever the interior strategy carves, two things have to hold, and both are
settled after the fact on the finished layer rather than being special-cased
into each strategy.

**The material is one piece.** Nothing may come off the cutting bed as a
separate part. An island holding a board up — a boss — is tied back with a rib;
anything else is a severed stiffener or boolean debris and is dropped, because
bridging it would run a strip of plywood straight across the hardware.

**The empty space is one piece.** Every internal lead must be able to reach
every other one. The strategies disagree about this by nature: `hollow`
connects everything for free, while `pocketed` gives each board its own recess
and would happily wall it in with an I²C cable and nowhere to run it. So the
void around the internal connectors is checked, and a `cable_channel`-wide
groove is cut wherever it is broken — including cutting open a socket that
ended up buried in solid material. `link_cables: false` turns it off.

Measured on the demo scene, layers where the leads could not all reach each
other:

| interior | without linking | with |
|---|---|---|
| `pocketed` | 8 | **0** |
| `hollow` | 1 | **0** |
| `ribs` | 2 | **0** |
| `grown` | 2 | **0** |

## Material that would snap

`min_segment` (4 mm by default, 0 to disable) opens out anything narrower than
itself — two cutouts passing close together leave a thread of plywood that will
not survive being handled, and merging them into one opening is both stronger
and easier to cut.

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
`start.bat`, derived panel planes, and a test suite (`pytest` from `backend/`,
`node --test tests/` from `web/`).

And since: body-based CAD measurement, a live Adafruit catalogue with on-demand
import, a library audit against the vendor models, menus and panes instead of an
overflowing toolbar, image-based lighting from CC0 environments, baked contact
shading, front-panel engraving as its own laser pass, and an optional Mitsuba
renderer for the final photograph.

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
6. **More vendors in the catalogue** — Adafruit publish a product feed and a
   CAD repository, which is why they went first. Pimoroni, SparkFun and Seeed
   would each need their own adapter behind the same search box.
7. **Identifying what an imported volume is.** An import knows a box is there
   and not that it is a STEMMA QT socket. Matching against a library of known
   connector footprints would turn most drafts into finished parts.
