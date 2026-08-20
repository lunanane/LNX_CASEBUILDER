# What we know, and what you need to measure

Every number in `backend/parts/*.yaml` carries a `confidence`:

| | meaning |
|---|---|
| `measured` | you put calipers on **your** unit |
| `datasheet` | official mechanical drawing, or a vendor STEP/STL we read with `tools/measure_cad.py` |
| `vendor` | quoted in a shop listing — usually just a bounding box |
| `community` | forum, GrabCAD, someone's printed case |
| `estimated` | our guess. **Do not cut against these.** |

`hwcase check` prints an `unverified` warning for any part whose weakest number
is `community` or worse, so the state of the library is always visible.

## Priority order for the calipers

Ranked by how much a wrong number costs.

### 1. HyperPixel 4.0 Square — position of the 40-way socket on the board  `estimated`

This is the single most load-bearing unknown in the project. The Pi's header is
at a known place (x = 32.5, y = 52.5 from the Pi's bottom-left corner — from the
official HAT mechanical spec). Where that socket sits on the 84 × 84 board
decides exactly how far the display overhangs the Pi, and therefore how much
room is left for the HDMI and micro-USB plugs that end up *underneath* it.

We currently assume the socket is centred in X (x = 42) and 4.0 mm in from the
header edge (y = 80). With those numbers the display lands at x −9.5…74.5,
y −27.5…56.5 relative to the Pi — overhanging the power/HDMI/audio edge by
27.5 mm, and leaving ~10 mm of the Pi's USB end sticking out the other side.

**Measure:** from the board edge to the centreline of the socket, both axes.

Related, and now modelled: the lit area is **not centred on the board**. The
vendor's bezels are 5.5 left/right but **4.5 top and 6.5 bottom**, so the window
sits 1 mm towards the header edge — centre y = 43.0 against a board centre of
42.0. Both axes' bezels sum to 83 rather than 84, so there is 1 mm unaccounted
for and the offset is good to about half a millimetre. **Measure** the four
borders from the board edge to the lit area.

### 2. The header stack height  `estimated`

`placement.mate_gap` in the scene, currently 17.0 mm for the bundled booster
(8.5 mm would be a plain socket). This has to clear the Pi's USB/Ethernet block,
which the official drawing gives as **16.0 mm** tall. 17.0 mm leaves 1 mm.

**Measure:** assembled height from the Pi PCB top face to the HyperPixel PCB
bottom face, with the booster header actually fitted.

### 3. Your rotary encoders' shaft length  `datasheet` for the vendor's, unknown for yours

The vendor mesh gives 21.4 mm from the board plane to the shaft tip (6.4 mm
body + 7.2 mm threaded bushing + 7.2 mm shaft). Your encoders are whatever you
soldered on. Shaft length sets the panel gap and the knob height.

**Measure:** board face → top of the threaded bushing, and board face → shaft tip.

### 4. AMYboard, everything except the panel  `estimated`

The 10 HP panel (50.5 × 128.5 mm, rail holes 123.5 mm apart) is a standard, so
it is safe. Everything else is scaled off the vendor's annotated photos
(`vendor/img/amyboard_*_annotated.jpg`) using the USB-C shell (8.94 mm) as the
ruler, which puts the PCB at roughly **50.5 × 105 mm** and the jack pitch at
about **12 mm**.

The *layout* is now right, which matters more than the millimetres:

- ten 3.5 mm jacks in **two columns of five**, hard against one edge, with the
  silkscreen labels filling the other half of the panel
- rows top to bottom: **SPDIF, LINE, MIDI, CV1, CV2**; left column **IN**,
  right column **OUT**
- the jack field only reaches ~68 mm up the panel. The top **~60 mm is empty** —
  the vendor calls it "front panel free space for display", and it is big enough
  for our own OLED behind the same panel
- the **I²C Grove port for accessories is on the FRONT face**, bottom-left,
  reached through the panel — not on a side, which is where we had it
- USB-C and microSD are both on the same side edge, above the jacks
- Eurorack power, the Tulip host I²C port and the debug header are on the back

**Measure:** panel-to-PCB-back depth, the jack grid origin and pitch, and how
far the jack columns sit from the panel edge.

### 5. Grove TCA9548A hub  `estimated`

Seeed publishes no mechanical drawing. We model it as a 40 × 40 mm double-width
Grove board. Since nine cables converge here, its *clearance* matters more than
its outline.

**Measure:** outline, height with Grove sockets, and hole positions.

### 6. ~~NeoTrellis mounting holes~~ — solved, from the STEP

The STL is a slab with the holes gone, but **STEP keeps them**: a hole is a
cylindrical face bounded by circles, stored as text. `tools/extract_holes.py`
reads them straight out. Our guess of one M2 per corner was wrong on all three
Adafruit boards:

| part | real pattern |
|---|---|
| 3954 NeoTrellis | **8 × M2.5** (2.8 mm), two rows at y = 15 and 45, x = 2 / 28 / 32 / 58 |
| 4741 OLED | 4 × M2 (2.5 mm) on 30.48 × 41.91 mm — 1.2" × 1.65" |
| 5752 quad encoder | 4 × M2 (2.5 mm) on 38.1 × 16.52 mm — 1.5" × 0.65" |

Run it on any vendor STEP:

```bash
python tools/extract_holes.py "vendor/cad/adafruit/3954/*.step"
```

### 7. OLED 4741 active-window offset  `estimated`

Board envelope is exact (35.56 × 46.99 × 5.63 mm). Where the 128 × 128 window
sits inside the glass is assumed centred, which it very likely is not.

**Measure:** from board edges to the lit area, all four sides.

### 8. Raspberry Pi connector Y positions  `community`

Outline, hole pattern, connector Z-heights and the −Y edge positions (power at
x = 10.6, HDMI at 32, A/V at 53.5) are straight off the official drawing. The
+X edge positions (Ethernet at y = 10.25, USB at 29 and 47) are read off that
drawing at low resolution, and the connector *overhangs* past the board edge are
estimated at 1.5–3 mm.

**Measure:** overhang of each connector past the PCB edge.

## Already exact — don't re-measure

| part | source |
|---|---|
| Pi 3B outline 85 × 56, R3, holes 3.5/3.5 on a 58 × 49 grid, M2.5 | official mechanical drawing |
| Pi 40-way header: body 50.8 × 5.08 centred at (32.5, 52.5) | official HAT mechanical spec |
| Pi connector heights: USB/Eth 16.0, USB 13.5, HDMI 6.5, A/V 6.0, power 2.75 | official mechanical drawing |
| NeoTrellis 60.000 × 60.000 × 7.570 | vendor mesh |
| Quad encoder 76.20 × 21.59, 19.05 mm pitch, 21.4 mm tall | vendor mesh |
| OLED 4741 35.56 × 46.99 × 5.63 | vendor mesh |
| Keypad 1611 60 × 60 × 11.5, 10 mm buttons on a 15 mm pitch | vendor |
| HyperPixel 84 × 84 × 9.5, 72 × 72 active | vendor |
| Eurorack 10 HP = 50.5 mm, 3U = 128.5 mm, rails 123.5 mm | Doepfer A-100 |

## Three vendor sources that were wrong or misleading

Worth recording, because both would have cost a cut sheet:

- **Adafruit 5752** is listed by several distributors as 25.6 × 25.3 × 4.6 mm.
  That is the *single*-encoder 4991. The real 5752 is **76.2 × 21.59 mm** with
  four encoders in a row on a 19.05 mm pitch — a factor of three in one axis.
- **Adafruit 4741** has no dimensions on the product page at all. It is
  35.56 × 46.99 mm, and it is not square.

- **AMYboard's own README** lists the ten jacks as a single "top to bottom"
  table, which reads like one column. The annotated photo shows two columns of
  five. We had modelled them from the table and had the in/out order swapped as
  well.

The first two were caught by reading `adafruit/Adafruit_CAD_Parts` instead of
shop pages; the third by looking at the vendor's own photograph instead of its
prose.

## How to record a measurement

Edit the part YAML, set the number, and raise its confidence:

```yaml
      - name: gpio_socket
        at: [41.2, 79.4]          # <- your number
        src:
          confidence: measured
          note: "calipers, 2026-08-19, unit #1"
```

Then re-run `hwcase check` — the `unverified` warning disappears once every
number in the part is at `datasheet` or better.
