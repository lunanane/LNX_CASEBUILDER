# Review and roadmap — catalogue, part models, Eurorack case styles

2026-10-16. Three questions:

1. What would it take to make the Adafruit catalogue integration reliable?
2. What would it take to show each part at the resolution of its real CAD model?
3. How do we get from today's U-channel frame to fully laser-cut Eurorack
   cases, and then to hybrid cases that carry Adafruit parts beside the rails?

Numbers about the Adafruit CAD repository were measured on 2026-10-16 against
`adafruit/Adafruit_CAD_Parts@main` through the GitHub tree API.

## Summary

- **A third of the "importable" products cannot be imported.** 158 of the 478
  vendor CAD folders publish only STEP, and the importer needs an STL.
  The search ranks them as importable anyway.
- **The catalogue does not refresh in a running server.** It is checked once,
  on the first search after start-up. The docstring says it refreshes "on a
  timer"; there is no timer. The local cache is from 26 August, and 13 CAD
  folders have been added since.
- **Parts are drawn as boxes, everywhere.** The vendor mesh is read once to
  measure the part and then discarded. The editor and the photographic
  renderer both extrude one box per volume. Real models are a display layer we
  can add without touching the geometry engine, because the importer already
  computes the transform it would need and then throws it away.
- **The laser-cut and hybrid Eurorack cases share one missing foundation:
  joints between flat plates.** The frame pipeline already models plates in 3D
  (`rack.Member`); what it lacks is tabs, slots and captive nuts, and packing
  those plates onto sheets. Build that once and three case styles follow.
- **The cheapest hybrid comes first:** a Eurorack *module panel* designer,
  with Adafruit parts mounted behind a standard panel. It reuses the layered
  engine almost as it is.

---

## Part 1 — A reliable Adafruit integration

### How it works today

`catalog.py` downloads Adafruit's product feed (5,577 products) and a listing
of the CAD repository's top-level folders, and caches both under
`vendor/catalog/`. Search scores product names locally. Importing a product
(`ingest.py`) lists that product's CAD folder through the GitHub contents API,
downloads its STL and STEP files, splits the STL into connected bodies
(`measure.bodies`), picks the board, guesses which face is up, and reads
mounting holes out of the STEP text. The result is a draft part with no
connectors.

### Findings

| # | Finding | Evidence | Effect |
|---|---|---|---|
| F1 | 158 of 478 CAD folders contain STEP but no STL. The importer needs an STL, so these fail with "no STL on disk to measure". Search still ranks them as importable. | `ingest.draft_part`; `CatalogEntry.importable` is `cad is not None` | A third of advertised imports fail after the download |
| F2 | The catalogue is loaded once per server process. `shared()` re-reads only when it has no products, so the 24 h TTL is checked on the first search and never again. | `catalog.shared`, no timer anywhere in `api.py` | A long-running hosted instance serves an ever older catalogue. The local cache is from 2026-08-26 |
| F3 | The CAD index uses the GitHub *contents* API: unauthenticated, 60 requests per hour, one call per refresh and one more per import. Directory listings from that API stop at 1,000 entries. | `CAD_INDEX_URL`, `ingest.cad_files` | Fine for one person, fragile for a hosted instance. The 1,000 cap is far off (478 today) |
| F4 | Six product IDs have more than one CAD folder (1586, 1833, 1982, 2590, 3010, 4222). The index is a dict keyed by product ID, so all but one are lost. | `Catalog._refresh` | Some variants are unreachable |
| F5 | 20 folders hold more than one STL (revisions, with and without headers, assemblies). The importer takes the first one alphabetically. | `draft_part`: `meshes[0]` | It may measure the wrong variant, silently |
| F6 | Holes come from the STEP, bodies from the STL, and they are combined assuming both files share an origin. The STEP reader is a text parse that ignores assembly transforms. | `_holes_from`, `measure.step_holes` | Holes can land in the wrong place with no warning |
| F7 | The board outline is always the bounding rectangle. | `draft_part`: `outline.type: rect` | Round and odd-shaped boards (rings, Circuit Playground) get the wrong outline |
| F8 | No connectors are ever proposed, and orientation is a heuristic with no way to correct it at import time. | By design, documented | Every import needs hand editing before it is safe to cut |
| F9 | The feed carries `discontinue_status`, `product_stock` and `parent_pid`; none is used. It has no dimensions. | Cached feed keys | Discontinued boards look as good as current ones |
| F10 | Import is one synchronous request with no file size cap. The largest model in the repository is 55 MB; the average is 1.7 MB. | `api.catalog_import` | One import can tie up a hosted worker |
| F11 | There is no measure of how often import succeeds across the whole repository. | — | Regressions in `measure.py` are invisible until someone imports that board |

### Plan

Ordered by value for effort. Sizes: **S** is a pull request or two, **M** a few,
**L** a milestone of its own.

**C1 — Index the repository properly (S).** One call to the git tree API
(`/git/trees/main?recursive=1`) returns every file with its size, so the index
becomes product → folders → files. That fixes F4, gives each search result an
honest `importable` (has an STL or a readable STEP), removes the per-import API
call, and lets downloads go straight to the raw file host. Record the commit
SHA the index was built from, so an import is reproducible. Accept an optional
`GITHUB_TOKEN`. Check the TTL on every request instead of only the first (F2),
and fix the docstring.

**C2 — Import from STEP (M).** Fixes F1, the largest single gap. STEP needs a
CAD kernel to turn into triangles. trimesh can load STEP through the optional
`cascadio` package, which wraps OpenCASCADE; `cadquery-ocp` is the heavier
alternative. **Decision needed:** check the wheel size and platform coverage
before adding it to the Docker image, or make it an optional extra like
Mitsuba. Bonus: with a kernel, holes come from real geometry, which also
fixes F6.

**C3 — An import review step (M).** Before a draft is saved, show it: the file
picker when a folder has several models (F5), a preview of the mesh with the
board's top face marked, a "turn over" button that overrides the heuristic
(F8), and the warnings. Today the review happens afterwards, in a YAML file.

**C4 — Check the geometry before trusting it (S).** Hole centres must fall
inside the board outline and outside every component body, or the hole set is
rejected (F6). A board thickness far from 0.8–2.0 mm suggests inches or a
wrong body choice. Take the outline from the board body's projected footprint
rather than its bounding box (F7).

**C5 — Propose connectors (M).** This is what makes an import safe to cut.
Match body sizes against a small library of connector signatures (STEMMA QT /
JST SH 4-pin, JST PH, USB-C, micro-B, 3.5 mm jack, pin headers on a 2.54 mm
pitch), and take the face from the nearest board edge. Use product-name
keywords ("STEMMA QT", "USB C") as a prior. Proposed connectors are tagged
`estimated`, so the issues pane keeps asking for confirmation.

**C6 — Measure the success rate (S).** A script that imports every folder in
the repository offline from a pinned snapshot and writes a table: imported,
failed and why, flipped, holes found, connectors proposed. Run it before
releases. It turns "reliable" into a number we can watch. Without C1 and C2
the ceiling is 318 of 478.

**C7 — Small feed improvements (S).** Show discontinued and out-of-stock
products as such, cap model downloads by size (F10), and run imports in the
background on a hosted instance.

Suggested order: C1 → C6 (to get a baseline) → C2 → C4 → C3 → C5 → C7.

---

## Part 2 — Parts at the resolution of their CAD model

### How it works today

The engine represents a part as boxes and cylinders (`volumes`), which is what
every check, cutout and pocket is computed from. That is correct and should
stay. But the same boxes are all anyone ever sees:

- **editor:** `export.scene_to_json` sends each volume's footprint, and
  `app.js` `buildSolids` extrudes it;
- **photograph:** `raytrace.build_scene_dict` extrudes the same boxes,
  commented "the hardware, as blocks. Not pretty".

The vendor mesh is read once at import and dropped. The transform that maps it
into the part's frame (shift to the board corner, optional turn-over) is
computed in `ingest.draft_part` and also dropped.

### Plan

Keep the boxes as the engineering model and add a **display model** next to
them. The two never mix: checks and cuts read only the boxes.

**M1 — Keep the transform (S).** At import, write a `model:` block into the
part: source file, units, offset, turn-over, rotation. The importer has every
number already.

**M2 — One optimised model per part (M).** Convert the vendor STL or STEP to a
GLB file, merged and reduced to a triangle budget (around 20–50 k per board),
cached beside the vendor CAD. Serve it from `GET /api/parts/{id}/model.glb`.
Adafruit's models are MIT licensed, so caching and serving them is fine.

**M3 — Show it in the editor (M).** Load models with three.js's GLTF loader
(vendored from the same r169 release as the rest of three). Add a
**view → real models** toggle. Selection, dragging and the red error ghosting
stay on the boxes, which are drawn as outlines when real models are on.
Repeated parts share one loaded model. Models load lazily, so a schematic
session never downloads them.

**M4 — Use it in the photograph (S).** The Mitsuba renderer writes the same
meshes in place of the blocks.

**M5 — Models for hand-written parts (S each).** The Raspberry Pi, the
HyperPixel and the AMYboard have no `model:` today. Add vendor models where
the vendor publishes one; the rest keep their boxes.

**M6 — Colour (S).** STL has no colour. Start with a per-part PCB colour (many
current Adafruit boards are black) and a neutral metal for components; read colours
from STEP or 3MF once C2 brings in a kernel.

**M7 — Audit the alignment (S).** Extend `hwcase.cli audit` to check that each
display model's bounding box agrees with the part's boxes. A model that does
not line up is worse than none.

Risks: a hosted instance serves a few megabytes per scene the first time; the
triangle budget and caching keep that bounded. Older laptops may struggle with
a dozen detailed boards; a "detail" setting can step the budget down.

Suggested order: M1 → M2 → M3 → M7, then M4–M6 as wanted. M2 depends on C2 for
STEP-only products.

---

## Part 3 — Roadmap for Eurorack case styles

### Where the frame pipeline stands

`rack.py` builds one style: aluminium rails, one folded aluminium U-channel
as the body, and two 5 mm side panels that carry the rails with an M5 per rail
end. Every piece is a `Member`: a 2D section, a length and a pose in 3D. Only
the side panels are cut; they export as one SVG without sheet packing, DXF or
a cut list. The command line does not build frames.

Three new styles are wanted:

- **B — fully laser-cut frame:** the same rails, but every other piece is
  laser-cut plate. No U-channel.
- **C — module panels with Adafruit parts:** a standard Eurorack panel, any
  HP, carrying boards from the library or the catalogue behind it.
- **D — hybrid case:** a laser-cut case that extends beyond the rails and
  holds Adafruit parts alongside the modules.

### Foundation (needed by B and D)

**G1 — Plates as first-class cut parts (M).** Pack frame plates onto sheets
with the existing plate-stock packer, write SVG and DXF with holes before
outlines, and produce a cut list naming every plate, its material, and its
rail positions from a named datum edge. Today `export.pack_sheets` takes only
a layered `CaseModel`, so this means extracting a "flat part" interface both
pipelines produce.

**G2 — Joints between plates (L).** A new module that, given two plates
meeting at an edge, cuts matching geometry into both:

- **tab and slot with a captive M3 nut** (the "T-slot"): the usual answer for
  laser-cut boxes. It is strong, needs no glue and can be taken apart;
- **finger joints** for glued boxes.

Both must be kerf-aware and depend on material thickness, which has to be
measured per sheet (nominal 3 mm plywood is often 2.7–3.2). Exhaustively
testable without a laser: every tab has a slot, every slot fits its tab plus
the declared clearance, and no joint lands on a rail hole.

**G3 — Material per plate (S).** Thickness, kerf and finish per member,
because the joints are computed from them.

### Style B — fully laser-cut frame

| | U-channel (today) | Laser-cut (B) |
|---|---|---|
| Rails | f&w 16500, M5 into side panels | unchanged |
| Body | one folded aluminium U | base plate + back plate, optional top and front lip |
| Side panels | 5 mm acrylic, rails only | cheeks: rails plus joints to every other plate |
| Joints | sliding nuts in the rail slot | T-slots with captive M3 nuts (G2) |
| Bought parts | rails, U-channel, screws | rails, screws, nuts |

The cheek outline comes from the same arithmetic as today's side panel: row
stack, module depth, margins. Base and back plates span the rail length. The
rails' sheet slot goes unused, since a 3 mm plate does not fit a 1.5 mm slot.

Scene: `rack.style: u_channel | laser`, defaulting to `u_channel` so every
existing frame builds unchanged. New settings: `material`, `joint`,
`top_plate`, `front_lip`.

Acceptance, in the spirit of the existing plan: a 68 HP, 6U, 51 mm laser frame
produces two cheeks whose M5 holes coincide with today's side panels to 0.01 mm;
every tab has a slot; every plate fits the configured stock; the cut list names
each plate.

Later: **tilted rows** (a wedge or "boat" case). The cheek outline becomes a
polygon and each row gets an angle. That needs `layout_rows` to work in a
rotated frame per row, so it is a milestone of its own.

### Style C — module panels with Adafruit parts

The smallest hybrid, and useful on its own: design a DIY module around
Adafruit boards.

- The outline is locked to a Doepfer panel: width from `eurorack.panel_width`
  (the table, never the formula), height 128.5 mm, oval rail holes at 3.0 mm
  from each edge.
- The panel plane is the panel; boards hang behind it on standoffs or
  `from_lid` supports, which the layered engine already does.
- A depth limit (the frame's module depth, or a skiff's 25 mm) becomes a check:
  anything deeper is an error.
- The power header is a known connector: the AMYboard panel already carries
  an `eurorack_power_10` mate, and `docs/eurorack.md` defines the 10- and
  16-way pinouts.
- Output: the panel as SVG or DXF, with engraving for labels.

This is mostly a preset of the existing layered pipeline (a fixed outline, one
faceplate, standoffs, a depth check), not new geometry. **Size: M.**

### Style D — hybrid case beyond the rails

**Recommendation: build hybrid cases as plate boxes, not layer stacks.**
Modules need 25–50 mm behind the panel and the rails another 19 mm. As a
layered stack that is 20 or more sheets of 3 mm plywood, which is heavy, slow
to cut and wasteful. A box of plates (style B's construction) has the depth for
free.

That gives two steps:

**D1 — A plate-box case style for parts scenes (L).** `case.style: box`, next
to `layered`. A top deck (the faceplate), walls, floor and optional back, all
joined with G2. Parts mount under the deck on standoffs. Every existing check
(collisions, plug access, panel plane, connector openings in the walls) still
applies, because it is computed from the parts, not from the layers. This is
useful well beyond Eurorack: any deep case for a Pi and a screen.

**D2 — Rack bays inside a box case (L).** A scene gains `racks:`, each a bay
with a position on the deck, HP, row stack and module depth. A bay contributes:

- an aperture in the deck the width of the rail span;
- two internal cheeks (from style B) that carry the rails, held by slots in the
  deck and the floor;
- a keepout the size of the module space, so no board collides with a module;
- room for a bus board and power supply, as parts from the library.

The rest of the deck carries Adafruit parts: screens, encoders, a Pi. The case
outline grows to enclose both, and the existing checks keep boards and plugs out
of the module space.

### Order

| Step | Gives you | Depends on | Size |
|---|---|---|---|
| C — module panel designer | DIY modules with Adafruit parts | nothing new | M |
| G1 — plates as cut parts | frame plates on sheets, DXF, cut list | — | M |
| G2, G3 — joints and materials | the ability to make boxes | G1 | L |
| B — laser-cut frame | a Eurorack case with no folded metal | G2 | M |
| D1 — plate-box case style | deep cases for any parts | G2 | L |
| D2 — rack bays | true hybrid cases | B, D1 | L |
| tilted rows | wedge and angled cases | B | M |

C can start immediately and in parallel with G1.

### Decisions for you

1. **Joint type for B and D:** captive-nut T-slots (strong, can be taken
   apart), finger joints (glued, simpler to cut), or both?
2. **Materials:** which sheet thicknesses do you actually cut? Joints are
   computed from measured thickness, so this sets the defaults and the tests.
3. **Hybrid layout:** should module panels sit flush with the deck's top face,
   or recessed by the deck's thickness, which is how most commercial hybrids do
   it?
4. **Desktop shape:** is a flat case enough for now, or are tilted rows needed
   early? That decides whether tilt comes before or after D.
5. **STEP support (C2):** acceptable as a required dependency of the Docker
   image, or optional like the renderer?
