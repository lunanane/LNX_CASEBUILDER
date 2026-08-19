# Landscape research — parametric enclosure tooling (2026-08)

Question: what already exists that we can stand on, and what do we have to build ourselves?

## 1. Geometry kernels we could build on

| Option | What it is | Verdict for us |
|---|---|---|
| [build123d](https://github.com/gumyr/build123d) | Python B-Rep CAD over OpenCascade (OCP). Successor-in-spirit to CadQuery, context-manager API instead of fluent chaining. Exports STEP/STL/DXF/SVG. | **Yes, as the optional "solid" backend** for milling output and STEP export. Heavy dep (~500 MB OCP wheel), so keep it behind an extra. |
| [CadQuery](https://github.com/CadQuery/cadquery) | Same kernel, fluent API, larger ecosystem. | Equivalent; build123d is the more active line. |
| [replicad](https://replicad.xyz) / [OpenCascade.js](https://ocjs.org/) | OCCT compiled to WASM, runs the kernel *in the browser* in a Web Worker, returns meshes to three.js. | Interesting later for live in-browser booleans. Not needed for v1 — our geometry is 2.5D and shapely handles it in ms. |
| [PartMode](https://github.com/BOMWiki/partmode) | Browser parametric CAD = OCCT-WASM + replicad + three.js, editable parts, assemblies, STEP export. | Good reference architecture for the frontend; not a library we can embed cleanly. |
| shapely + ezdxf + trimesh | 2D polygon booleans, DXF writing, mesh I/O. Tiny, pure-ish Python. | **Core of v1.** A layered case *is* a stack of 2D polygons. |
| [manifold3d](https://github.com/elalish/manifold) | Fast, robust mesh booleans, has Python + WASM bindings. | Good escape hatch if we ever want true voxel/mesh CSG. |

## 2. Laser-cut / enclosure generators (prior art, not dependencies)

- **[Boxes.py](https://github.com/florianfesti/boxes)** (GPLv3) — the reference implementation of parametric laser-cut boxes. Finger joints, flex cuts, hinges, `thickness` and `burn` (kerf) compensation, SVG/DXF/PS output, web UI + Inkscape plugin. We should **steal its concepts** (kerf compensation model, finger-joint edge descriptors) but not link it — GPL and its model is "generator per box type", not "case around an arbitrary part scene".
- **[openscad-lasercut](https://github.com/playfultechnology/openscad-lasercut)** and [laser_slicer](https://github.com/frezik/laser_slicer) — 3D-model-to-stacked-layers slicing in OpenSCAD. Confirms the slicing approach; OpenSCAD's 2D projection route is slow and lossy compared to doing it in shapely.
- **MakerCase / Festi / atomm layer slicer** — web box generators, closed or trivial. Nothing supports *"lay out these dev boards, then wrap a case around them"*, which is exactly our gap.
- **LaserStacker (UIST 2015)** — academic; cut+weld layered acrylic. Good reading for joint strategies.

Conclusion: **nobody does component-aware enclosure layout.** EasyEDA/LibrePCB/KiCad do PCB-internal layout; box generators do empty boxes. The interesting middle — "snap real modules into a 3D volume, respect their connectors and cables, then emit a case" — is ours to build.

## 3. Where the measurements come from

Ranked by trust:

1. **Official mechanical drawings (PDF/DXF)** — Raspberry Pi publishes them at `datasheets.raspberrypi.com` / `pip.raspberrypi.com`.
2. **Vendor STEP/f3d files** — jackpot for us: [`adafruit/Adafruit_CAD_Parts`](https://github.com/adafruit/Adafruit_CAD_Parts) has 473 folders keyed by product ID, each with `.step` + `.stl` + `.f3d`. It contains **`3954 Adafruit NeoTrellis`, `4741 OLED 1.5in`, `5752 Quad Rotary Breakout`** — i.e. three of our eight parts, exact, with connectors modelled. `tools/fetch_cad.py` pulls these into `vendor/cad/`.
3. **Vendor product-page "Technical Details"** — bounding box only (`60.0 x 60.0 x 7.5 mm`), no hole positions. Useful as a cross-check on our own numbers.
4. **Community CAD** (GrabCAD, Thingiverse, Cults3D) — usable as a sanity check, never as truth. HyperPixel links there have rotted.
5. **Our own calipers** — the final authority. Every part in the library carries a `confidence` field; anything below `datasheet` prints a warning and lands on the *measure-me* checklist in `docs/measurements.md`.

## 4. Standards worth encoding once

- **Eurorack** (AMYboard is a 10 HP module): panel width = `n * 5.08 - 0.3 mm` → 10 HP = **50.5 mm**; 3U panel height **128.5 mm**; rail hole spacing `U * 44.45 - 10.85` = **123.5 mm** for 3U. Source: Doepfer A-100 construction details.
- **Raspberry Pi HAT/pHAT**: 40-pin 2.54 mm header, mount holes 58 x 49 mm, 3.5 mm in from the board edges, M2.5.
- **STEMMA QT / Qwiic**: JST SH 4-pin, 1 mm pitch, cables in fixed lengths (50/100/200 mm) — cable length is a *layout constraint*, so the library stores connector positions and the checker measures run length.
- **Grove**: 4-pin 2.0 mm pitch, 20 x 20 mm module grid on Seeed boards.
- **Laser kerf**: material- and machine-specific; modelled as `kerf` on the material, applied as an outward offset of every cut contour by `kerf/2`.

## 5. Architecture consequences

- Geometry that must be exact and reusable lives in **Python**; the browser is a viewer/editor, not the source of truth.
- A **layer stack of 2D polygons** is the internal case representation. It is natively a laser job, and it is also a valid 2.5D milling job (each layer = one pocket depth). A voxel grid would throw away XY precision for nothing; polygons-per-slab keeps it.
- Parts are **data, not code** (YAML), so adding a board never means touching the engine.
