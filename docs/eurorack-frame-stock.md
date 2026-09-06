# Frame stock for the U-profile case — as ordered

The hand-built reference case, transcribed from the vendor pages and the order
confirmation (screenshots, 2026-09-06). These are **the numbers as the supplier
states them**, which is the strongest source this project has short of calipers:
they are what was actually bought and cut, not a photo estimate.

Confidence is `datasheet` for the drawing dimensions and `measured` for the
cut-to-order dimensions, which are the values the supplier cut to.

§§1–3 are the supplier's numbers verbatim, in the supplier's own orientation.
**§4 is how they go together**, and it rotates the rail relative to its drawing —
read it before using anything here as a case dimension.

## 1. Rack rail — "Eurorack-Rail mit Kante", silver

| | |
|---|---|
| supplier | f&w, Art.Nr. **16500** |
| profile | aluminium extrusion, sold by the metre / cut to length |
| finish | silver |

Cross-section, from the vendor's dimensioned drawing:

| dimension | mm |
|---|---:|
| overall height | **19.00** |
| overall width (top flange) | **10.00** |
| inner step | 5.00 |
| lip / edge thickness ("Kante") | 2.80 |
| slot mouth | 1.50 |
| slot inner | 2.50 |
| web | 2.00 |
| upper section height | 10.50 |
| lower offset | 3.30 |
| lower section width | 9.00 |
| **sliding-nut channel bore** | **Ø 4.40** |

The Ø4.40 channel runs the length of the extrusion and takes an **M5** screwed in
through the *end*, which is how the side panels carry the rails — f&w describe it
as "seitliche Montage mit M5-Schrauben". It is not the module screw channel and
not an M3 feature. See §4.1.

"mit Kante" = with the integral edge: the rail presents its own finished lip
rather than needing a separate trim strip. That lip is the case's visible front
edge — §4.1.

**Orientation warning:** the drawing's "height" of 19.00 is the installed
*depth*. See §4.1 before using these as case dimensions.

## 2. Side profile — aluminium U-channel, cut to size

| | |
|---|---|
| supplier article | **al5905100upko** |
| description | Aluminium U-Winkel, schwarz matt (RAL 9005), 1.0 mm, Zuschnitt |
| configuration ID | 2125050 |
| finish | decorative face outward ("Sichtseite: Dekor aussen") |
| price | € 15.87 for the piece |

Folded from **1.0 mm** sheet. Cross-section is a U of three flats:

| | mm |
|---|---:|
| **length** (the cut dimension) | **376** |
| flange A | **40** |
| web B | **216** |
| flange C | **40** |

So the developed cross-section is 40 + 216 + 40, bent to a channel, and the piece
is 376 mm long. Quantity on this order is 1 — a case needs two, so the mirror was
either ordered separately or cut from the same run.

**1.0 mm wall is thin.** It takes a sliding nut and a screw perfectly well in
shear, which is the load here, but it will not hold a thread of its own and it
will dimple if a screw is over-torqued against it. That is consistent with the
build: the rail carries the load, the profile just locates it.

## 3. Tinted acrylic panel

| | |
|---|---|
| material | Acrylglas Platte getönt Orange, **5 mm** |
| blank | **237.0 × 52.0 mm** rectangle |
| edge finish | lasered ("Gelasert") |
| price | € 5.95 |

### Holes

Positions are edge → hole **centre**, origin at the lower-left corner (the
supplier's convention: "Abstand von der linken Seite" / "Abstand vom Boden").

| # | x (mm) | y (mm) | Ø (mm) |
|---:|---:|---:|---:|
| 1 | 12.0 | 37.0 | 5.0 |
| 2 | 47.0 | 37.0 | 5.0 |
| 3 | 57.0 | 37.0 | 5.0 |
| 4 | 180.0 | 37.0 | 5.0 |
| 5 | 190.0 | 37.0 | 5.0 |
| 6 | 225.0 | 37.0 | 5.0 |
| 7 | 59.0 | 11.0 | 3.0 |
| 8 | 180.0 | 11.0 | 3.0 |

### Cutout

One rectangular `Aussparung`, position given edge → **centre** of the cutout:

| | mm |
|---|---:|
| width | 112.0 |
| height | 21.0 |
| centre X | 119.0 |
| centre Y | 10.0 |
| rotation | 0° |

Centre Y = 10.0 with a height of 21.0 puts the lower edge at −0.5 mm, i.e. the
cutout **breaks the bottom edge** by half a millimetre: it is an open-bottomed
slot, not an enclosed window. Worth knowing before this is re-cut, because a
generator that assumes an enclosed rectangle will produce a different part.

### What this panel is

It is the **5U side panel**, and every feature on it is derivable from the row
stack rather than drawn by hand. Decoded in §4.4 and §4.5: the six Ø5 holes are
the M5 rail fixings for a `1U + 3U + 1U` stack, and the 112 × 21 cutout is the
4 HP insert slot.

The two Ø3 holes at y = 11 remain unexplained and are **not** symmetric: x = 59
and 180, where symmetry about the blank's centre of 118.5 would want 59 and 178.
Check against the built part before re-cutting.

## 4. The assembly — settled 2026-09-06

Confirmed by the builder. Superseded readings have been deleted rather than
annotated; the canonical version of this model is `.docs/DEVELOPMENT_PLAN.md` §3.

### 4.1 The rail is the case edge

The rail is not mounted inside a case — it *is* the visible front edge, which is
what "mit Kante" means. Its installed orientation is **rotated from the drawing**:

| | mm | axis when installed |
|---|---:|---|
| height | **10.00** | Y |
| depth | **19.00** | Z, into the case |
| module slot centre | 5.00 from each edge | centred in the 10.00 |
| sheet slot | 1.50 | takes the 1.0 mm body flange |
| end channel | Ø4.40 | takes an **M5** into the rail's end |

The drawing's 19.00 "height" is the installed depth. The proof is stacking:
adjacent rows' slot centres are 10.85 mm apart, so a 10.00 mm tall rail drops
into that gap with 0.85 mm spare. At 19 mm it could not, and multi-row cases
would be impossible.

### 4.2 The body hangs under the rail

The U-channel's flange edge slides into the rail's 1.50 mm slot, and a sliding
nut in the channel below bolts the two together. The body is therefore *smaller*
than the case's outer envelope — the rail overhangs it by `5.00 - 2.30 = 2.70 mm`
per edge. The 40 mm flange is the case depth, fixed by the stock.

### 4.3 The parameterisation

```
length(HP) = HP x 5.08              68 HP -> 345.44 mm  (cut 345)
web(U)     = U x 44.45 - 6.25       5U -> 216.00 (this case, exactly)
flange     = 40                     the case depth
```

The `6.25` is `10.85 - 2 x 2.30`: the rack hole deduction less twice the inset
from the outermost slot centre to the body's edge. Only `c = 2.30` is fitted, and
only from this one case — `estimated` until a caliper says otherwise.

Rail length uses the **nominal** grid, never the panel-width table. f&w's own
cut lengths confirm it: 84 TE = 427 mm, which is `84 x 5.08 = 426.72` rounded
up, not reduced.

### 4.4 The acrylic is the side panel, and its holes are derivable

The 237 x 52 x 5 panel is the **5U side panel**. Its six 5.0 mm holes are M5
clearance for the rail end-fixings, in three pairs — one pair per row:

| holes (mm) | span | row |
|---|---:|---|
| 12 / 47 | 35 | 1U |
| 57 / 180 | **123** | **3U** (rack model: 122.50) |
| 190 / 225 | 35 | 1U |

Row gaps 10 mm, margins 12 mm each end, outer span 213 mm. **So "5U" here means
1U + 3U + 1U** — not a 5U panel format, and not five 1U rows.

237 mm is the height axis, 52 mm the depth axis, and the holes sit 37 mm along
the depth.

The rack-correct model gives 33.60 / 122.50 / 33.60 with 10.85 gaps and a 211.40
outer span. The built panel is 1.6 mm longer across 5U — whole-millimetre
rounding, absorbed by the sliding nuts.

### 4.5 The 4 HP insert slot

The 112 x 21 mm cutout that breaks one edge is the insert slot:

- **21 mm** across is 4 HP (20.32, rounded up)
- **112 mm** long clears a 3U module's PCB (panel height less ~16.5 mm)
- centred on the 3U row
- **breaks the edge** so the module slides in from the side

Each side panel therefore contributes 4 HP of capacity outboard of the rails —
**8 HP per 3U row** across both sides. A 68 HP frame holds 76 HP of modules.

### 4.6 Still open

1. **A 6U case needs four rails; one U-channel has two flange edges.** Either
   the middle pair mounts to the side panels, or a 6U case is two stacked 3U
   shells. Monolithic 6U web = 260.45; two stacked 3U shells = 2 x 127.10 =
   254.20. Blocks geometry generation for 6U.
2. **The two Ø3 holes** at (59, 11) and (180, 11) are not symmetric about the
   panel's centre of 118.5 — 59 would pair with 178, not 180. Check the built
   part before re-cutting.
3. **The fifth screenshot** never arrived.
