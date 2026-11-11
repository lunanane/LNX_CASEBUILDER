# Eurorack mechanical norms

Everything here is the *format*, not a part. Parts get measured (see
[measurements.md](measurements.md)); these numbers you can cut against without
owning the thing — provided you cut against the **table**, not the folklore
formula. Sources at the bottom; every value is `datasheet` unless marked
otherwise.

There is no Eurorack standards body. Doepfer's A-100 construction page is the de
facto specification because everyone copied it, so "the norm" below means "what
Doepfer publishes", and where the field has drifted away from that (1U, module
depth) it is called out as drift rather than smoothed over.

## 1. Panel height — 3U

**128.5 mm**, all panels, no formula and no tolerance band worth having.

3U in rack terms is 3 × 44.45 = 133.35 mm. Eurorack panels are 4.85 mm shorter
than that; the difference is the rails and the gap between rows. Do not derive
the panel height from the U count — it is a flat constant.

## 2. Panel width — HP

1 HP = 5.08 mm = 0.2". A panel is *narrower* than its nominal HP so it can be
assembled without binding against its neighbour.

> **Use this table, not the formula.** The widely repeated rule
> `width = n × 5.08 − 0.3` is a bad approximation of the table, and it errs on
> the *wrong side*: it cuts too wide on 11 of the 16 published sizes, worst at
> **6 HP, where it is 0.18 mm over**. Stack a few of those in a row and an 84 HP
> rack stops closing.

| HP | nominal (HP × 5.08) | **actual panel width** | formula error |
|---:|---:|---:|---:|
| 1 | 5.08 | **5.00** | −0.22 |
| 1.5 | 7.62 | **7.50** | −0.18 |
| 2 | 10.16 | **9.80** | **+0.06** |
| 4 | 20.32 | **20.00** | **+0.02** |
| 6 | 30.48 | **30.00** | **+0.18** |
| 8 | 40.64 | **40.30** | **+0.04** |
| 10 | 50.80 | **50.50** | 0.00 |
| 12 | 60.96 | **60.60** | **+0.06** |
| 14 | 71.12 | **70.80** | **+0.02** |
| 16 | 81.28 | **80.90** | **+0.08** |
| 18 | 91.44 | **91.30** | −0.16 |
| 20 | 101.60 | **101.30** | 0.00 |
| 21 | 106.68 | **106.30** | **+0.08** |
| 22 | 111.76 | **111.40** | **+0.06** |
| 28 | 142.24 | **141.90** | **+0.04** |
| 42 | 213.36 | **213.00** | **+0.06** |

("formula error" = `n × 5.08 − 0.3` minus the actual width. **Positive = the
formula makes the panel too wide**, which is the failure that costs you a sheet.)

There is no clean closed form — Doepfer's deductions wander between 0.08 and
0.48 mm with no pattern. For an HP not in the table use `nominal − 0.5`: that is
the only round rule that never over-cuts any published row (the binding case is
6 HP, where the deduction is 0.48). Mark anything derived that way `estimated`.

## 3. Mounting holes — the load-bearing section

| | |
|---|---|
| vertical centre distance, 3U | **122.5 mm** |
| hole centre from top edge | **3.0 mm** |
| hole centre from bottom edge | **3.0 mm** |
| hole diameter, round | **3.2 mm** |
| oval slot | 3.2 mm high × ~6.4 mm wide (≈2:1) |
| horizontal position | free, but hole-to-hole must be a multiple of 5.08 mm |
| screw | **M3 × 6 oval-head, cross recess (DIN 7985)** |
| count | 2 up to 10 HP (one top, one bottom); 4 from 10–12 HP up |

122.5 = 128.5 − 2 × 3.0, and it is also what `U × 44.45 − 10.85` gives for 3U.

The holes are **oval on real panels** for a reason: rails are threaded strips or
sliding nuts, and a round 3.2 mm hole gives no lateral adjustment at all. Cut
round only if the panel is the only thing in its row.

> **Corrected 2026-10.** This repo used to give the spacing as a millimetre
> too much in four places -- the formula `U × 44.45 − 10.85` written out with
> the wrong answer, and the AMYboard panel's top rail holes at y = 126.5,
> 2.0 mm from the top edge against 3.0 at the bottom. All four now read
> 122.5, and the panel's holes are at **y = 3.0 and y = 125.5**.

## 4. Panel material

- **2 mm anodised aluminium** is the Doepfer panel.
- 1.6 mm FR4 (PCB-as-panel) and 2–3 mm acrylic are both common in the wild.
- Thickness matters at the jacks: a panel-mount 3.5 mm jack's threaded bushing is
  only a few mm long, so past ~3 mm the nut runs out of thread. Counterbore or
  step the hole rather than going thicker.

## 5. Depth, and what is behind the panel

Doepfer standardises **nothing** here — this is the axis where modules actually
collide. Treat this whole section as `community`.

| | |
|---|---|
| PCB height behind a 3U panel | ≈ **110 mm** (panel height − ~18.5) |
| "skiff friendly" | ≤ **25 mm** total, *including the power cable* |
| shallow case | ~40 mm usable |
| standard / deep case | 50–60 mm usable |

**How this repo measures it.** `module_depth` -- on a frame (`rack.module_depth`)
and on a hybrid case's rack alike -- is the clear
room *behind the module panel*: from the rails' front face, which is the back of
the panel, to the inside of the frame's body web or the top of the hybrid's rack
floor. The panel's own 2 mm is not counted. A module that fits a 40 mm frame
fits a 40 mm hybrid. (The module panel designer's `eurorack_panel.module_depth` is still
measured from the panel's face.)

The power ribbon is the usual offender: a 16-way IDC plugged into a header
standing ~9 mm off the PCB needs 15–20 mm behind the board before the cable will
turn. Budget for the *cable*, not the connector.

## 6. Rows and cases

- A 19" row is **84 HP**. 104 HP and 126 HP cases are common; they are wider than
  19" and are a vendor convention, not a norm.
- **3U row pitch = 133.35 mm.** Panels are 128.5, so consecutive rows leave
  4.85 mm for rails and clearance. `derived`
- Rail types: Doepfer/TipTop lipped rails, Vector-style unlipped rails, threaded
  strips, and sliding-nut channels. They differ in whether they eat panel height
  at the edges — which is the entire reason 1U is a mess.

## 7. 1U — two incompatible standards

Do not write "1U" in a scene without saying whose.

| | panel height | PCB height |
|---|---:|---:|
| **Intellijel 1U** | **39.65 mm** | ≈ 22.15 mm |
| **Pulp Logic 1U** | **43.18 mm** | ≈ 25.68 mm |

3.53 mm apart, and not interchangeable. Intellijel's rails have a decorative lip
that eats panel height; Pulp Logic assumed unlipped Vector rails, so its screw
channels sit further apart. Put a Pulp Logic tile in a lipped rail and the row
exceeds 1U (44.45 mm) and the case stops closing. Width is plain HP in both.

## 8. Power connector

Mechanically: **2.54 mm pitch shrouded IDC box header**, 2 × 8 (16-way) or 2 × 5
(10-way), keyed by the shroud notch. The body stands ~9 mm off the PCB — that is
the number that matters for case depth, and it is `community`: measure the header
on the board you actually have.

Electrically (16-way, pin pairs from the red-stripe end):

| pins | |
|---|---|
| 1, 2 | **−12 V** ← red stripe |
| 3, 4 | GND |
| 5, 6 | GND |
| 7, 8 | GND |
| 9, 10 | **+12 V** |
| 11, 12 | +5 V |
| 13, 14 | bus CV |
| 15, 16 | bus Gate |

The 10-way connector is exactly pins 1–10 of that: ±12 V and ground, no +5 V and
no bus CV/Gate. **Red stripe = −12 V = "stripe down"** by convention on the
module end. Reversing it is the classic way to kill a module.

This project does not cut against pinouts — it is recorded so the `mates`
standard names (`eurorack_power_10`, `eurorack_power_16`) mean something.

## 9. Panel component drill sizes

`community`, but well settled. Check your own component's datasheet before the
sheet goes on the laser.

| component | panel hole |
|---|---:|
| 3.5 mm jack, PJ301M / PJ398SM ("Thonkiconn") | **6.0 mm** |
| 9 mm pot, Alpha, threaded bushing | 7.0–7.5 mm |
| rotary encoder, 7 mm bushing | 7.0–7.5 mm |
| mini toggle switch | 6.0–6.35 mm |
| 3 mm LED | 3.0 mm (3.2 with a bezel) |
| 5 mm LED | 5.0 mm (5.2 with a bezel) |
| generic pre-drilled panel | 7.0 mm, accepts most of the above |

The Thonkiconn body is ~10 mm tall with 4.5 mm of thread, so it clamps 2 mm of
panel comfortably and 3 mm marginally.

## 10. What this repo already encodes

[misc.yaml](../backend/parts/misc.yaml) carries `shorepine-amyboard-panel`:
50.5 × 128.5 × 2.0, four M3 rail holes, an `eurorack_power_10` mate. Correct
apart from the rail hole Y positions flagged in §3.

Not encoded anywhere yet: the HP table (§2), the row pitch (§6) and the component
drill sizes (§9). If the engine should *enforce* these rather than have them sit
in prose, the natural home is a `standards` block the checker reads — say the
word and this lifts into YAML.

**The hybrid case** (`kind: hybrid`, `backend/hwcase/hybrid.py`). A laser-cut
frame lying flat, with your own hardware beside it: `rack:` is the frame's spec
(rows, `hp`, `module_depth`, `panel` overhangs and radii, `material`, `joint`,
`fastener`, plus `module_panel_t`), and the HP rail span is the width of the
whole case -- there are no left and right walls, the case's ends across the
width are the two side panels, with an M5 through each into each rail end. The
deck top is the module face (D-3), so the rails sit `module_panel_t` under it.
`sections:` are stretches of deck fixed above or below the rack, each
`{id, side: above|below, length, depth}` and as wide as the rails; several on
one side stack outwards in list order. Each section gets a deck cut as a
layered lid is (so boards placed there get their windows, shaft holes and
standoffs), every run of zones at one depth a floor, a full-height step wall
where neighbouring depths differ (a step under a sheet and a millimetre is one
floor at the deeper depth, the divider standing on it), and an end wall at each
end. Each divider has rounded cable openings just above the floor --
`rack.cable_openings: {count, width, height}`, one per 90 mm of width, 30 x 15
mm unless set -- kept clear of every joint feature. Every plate is jointed to
the plates it meets, with the case's joint (dividers, which stand inside the
side panels, are T-slotted there), and into both side panels, whose outline
follows the stepped profile grown
by the overhangs (bottom/top past the end walls, front past the deck, back past
the floors). The rack keeps no modules: its space is kept clear, and a part
outside its section, in the rack's space or deeper than its section's floor is
an error. The cut list gives the plates, the rails by length, an M5 per rail
end (the side panel plus 10 mm of thread, rounded up to stock) with the M5 tap
for the rail's 4.4 mm end bore (tap drill 4.2), the joint bolts and nuts, and
every standoff. An older box scene's `racks:` (rack bays set into a box) still
loads, is ignored, and the check says the bay moved to the hybrid case.

## 11. A panel made as a PCB

Many DIY panels are not cut at all: they are 1.6 mm FR4 boards from a PCB
service, black (or any colour) soldermask, white silkscreen lettering, and gold
where the mask is opened over copper. The editor's panel group has a **made as**
choice for this; in the scene it is

```yaml
case:
  eurorack_panel:
    hp: 10
    fab: pcb            # laser (the default) | pcb
    pcb: {thickness: 1.6, mask: black, silk: white, finish: enig, ring: 0.6}
```

**What can be ordered** lives in one table, [`web/pcb-fab.json`](../web/pcb-fab.json),
read by the backend (validation, checks, fab notes; also served at
`/api/pcb/palette`) and by the editor (swatches, the image preview, the 3D
colours). Mask: green, matte green, black, matte black, white, blue, red,
yellow, purple. Silk: white or black (black on white or yellow mask; white on
the rest). Finish: ENIG (gold), HASL and lead-free HASL (silver). Thickness:
0.8–2.0 mm. The preview colours are sRGB approximations, not the services'
renderings. With `fab: pcb` the board thickness *is* the panel thickness, so
the standoffs are measured from the back of a 1.6 mm board.

**Layers.** Every decoration element gets a `layer`: `silk` (printed),
`copper` (exposed metal: copper under a mask opening, gold or silver by the
finish) or `substrate` (mask opened, no copper: bare FR4). `ring` puts a
metal rim of that width round every round panel hole.

**Image artwork** (`scene.artwork`). Any PNG/JPG/SVG is sampled in the browser
to at most 512 × 512 greyscale and stored in the scene as `z64:` base64 of the
zlib-compressed bytes — a few kB to a few hundred kB, inside the 1 MB scene
limit — together with the thresholds and the layer of each band. The original
file and the traced outlines are *not* stored; the build traces the bands from
the greyscale (marching squares with interpolated edge crossings, numpy and
shapely only) every time, so thresholds stay editable. One threshold makes two
bands, two make three; each band maps to `mask` (leave it), `silk`, `copper` or
`substrate`. `blur` (mm) despeckles; `min_feature` (mm) drops anything thinner,
and the issues list says how much was dropped. Artwork is clipped to the
board, kept 0.5 mm off the holes and 2.5 mm off the rail slots (the screw head
sits there).

**What the build does for the fab, and says:** silk on a mask opening or over
a hole is clipped off (a fab would do it anyway); copper is pulled 0.3 mm in
from every edge and hole; silk lines under 0.15 mm, lettering under 1 mm, a
ring under 0.15 mm and metal lines under 0.15 mm are warnings.

**The package** (*export for PCB fab*, `POST /api/export/pcb`) is a zip with
Protel-named Gerbers (RS-274X, 4.6 mm, X2 file attributes): `.GTL` top copper,
`.GTS` top mask (drawn = opening), `.GTO` top silk, `.GBL`/`.GBS` an empty
bottom, `.GKO` outline, and `-NPTH.DRL` (Excellon, metric). Polygons with holes
are drawn dark and their holes cleared (`%LPD*%`/`%LPC*%`); unclipped lettering
is stroked with a round aperture the width of its line. All panel holes are
non-plated. Round ones are drilled; the oval rail slots and any other cut-out
(display windows) are **routed — drawn as closed contours on the `.GKO`
outline layer**, which JLCPCB, PCBWay, Aisler and OSH Park all route as
internal cut-outs, rather than as Excellon G85 slots, which not every
service's CAM reads. The `-fab-notes.txt` says what to tick: 2 layers, FR4,
thickness, mask, silk, finish, no edge plating, and *remove order number* (or
*specify a location*) — otherwise the order number is printed on the front.

Before paying, upload the zip to the service's Gerber viewer and check: the
outline and the rail slots routed, the holes drilled, gold where you meant
gold, and no order-number placement on the face.

## Sources

- [Doepfer A-100 construction details](https://doepfer.de/a100_man/a100m_e.htm) —
  panel height, the HP width table, screws, hole rules, panel material. Primary
  source for §§1–4.
- [Synth DIY Wiki: Eurorack](https://sdiy.info/wiki/Eurorack) /
  [Eurorack panel components](https://sdiy.info/wiki/Eurorack_panel_components)
- [Exploding Shed — Eurorack dimensions](https://www.exploding-shed.com/synth-diy-guides/standards-of-eurorack/eurorack-dimensions/) —
  hole diameter, 1U heights, PCB height behind the panel.
- [Intellijel 1U technical specifications](https://intellijel.com/support/1u-technical-specifications/)
- [Pulp Logic — about 1U tiles](https://pulplogic.com/1u_tiles/)
- [North Coast Synthesis — the varieties of ribbon cable experience](https://northcoastsynthesis.com/news/ribbon-cable-experience/) —
  10-way vs 16-way, stripe orientation.
- [Thonkiconn PJ301M-12 datasheet](https://www.thonk.co.uk/wp-content/uploads/2014/02/Thonkiconn_Jack_Datasheet.pdf)
