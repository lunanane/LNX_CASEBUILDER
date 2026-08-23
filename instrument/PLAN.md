# The instrument — software plan

The case is designed; this is what runs inside it. One Raspberry Pi 3B with a
HyperPixel 4.0 Square touch screen is the brain: it hosts monome **iii** Lua
scripts against emulated grids, sequences everything, owns the clock, the
presets and the session view. One **AMYboard** is the sound engine: two poly
synths, two monos, a drum channel and the output bus, with the quad encoder
wired to it directly. They talk over I2C.

This file is the work list for the overnight Ralph loop. Every task has a
**Done when** — an executable criterion, because the loop has no taste, only
tests. Guardrails are at the bottom and they are not optional.

**Hardware is allowed and wanted, but never required.** The loop should test
against real devices wherever they are reachable from this Windows machine:
the AMYboard over **USB serial** (MicroPython REPL / mpremote), a monome grid
via **serialosc**, the Pi over the **network** if it answers. Real I2C is
physically impossible from this laptop — that leg is verified on the Pi
later; serial exercises the identical framed protocol. Every hardware test
must **skip cleanly when the device is absent** (pytest skip, not failure):
the loop runs unattended and must never stall on an unplugged cable.
Simulators and mock transports remain first-class so everything is also
testable with nothing attached. Feasibility verified: `lupa` 2.8 runs Lua
5.5 in-process (tested, both call directions), and the whole host API that
`gridcomposer21.lua` needs is six functions plus `metro`.

---

## Scoreboard (the loop ticks these; sections below hold the detail)

- [ ] A1 Lua sandbox + API shim
- [ ] A2 metro
- [ ] A3 clock
- [ ] A4 grid backends + virtual grid
- [ ] B1 gridcomposer21 headless integration suite (fidelity milestone)
- [ ] B2 script manager (two isolated runtimes, reload)
- [ ] C1 minipad01.lua — chord/arp on 8x4, mode row, state_save/load
- [ ] D1 parameter registry
- [ ] D2 script params (iii_params + gridcomposer21 companion map)
- [ ] D3 touch UI shell (tabs, bookmarks, sliders, BPM, virtual grids)
- [ ] E0 hardware preflight: enumerate COM ports / serialosc / Pi on LAN,
      probe for a MicroPython REPL, write findings to instrument/HARDWARE.md;
      device tests from here on are real when present, skipped when not.
      Known at planning time: COM13 exists (generic USB-CDC, plausibly the
      AMYboard's USB side, but held by another program — retry politely,
      never fight over a port); the AMYboard is physically on the Pi's I2C
      bus behind the TCA9548A mux. If the Pi answers on the LAN (ssh),
      real I2C tests are possible TONIGHT by running the probe on the Pi:
      scan mux channels, find the AMYboard's address, record both
- [ ] E1 link transport research finding (stock-firmware I2C slave? serial?)
- [ ] E2 framed protocol + codec shared with MicroPython, fuzz-tested
- [ ] E3 mock round-trip (param set / encoder echo / preset chunking)
- [ ] F1 amyboard engine module (voices, bp builders, CPython-tested)
- [ ] F2 amyboard pages module (encoder paging, LED/display model)
- [ ] F3 amyboard bus (sidechain depths, fx macros, comp/limiter finding)
- [ ] F4 amyboard main.py shell + MicroPython-subset lint
- [ ] F5 (hardware-gated) load engine onto the real AMYboard via mpremote,
      run the protocol handshake and a param round-trip over USB serial;
      back up every pre-existing device file to amyboard/device-backup/
      before writing anything
- [ ] G1 preset schema, save/recall, bar-quantized apply
- [ ] H1 clip model, quantized launch, atomic multi-lane
- [ ] H2 param glide
- [ ] H3 session tab UI
- [ ] I1 run.py + golden-file demo scenario
- [ ] I2 README-instrument.md + full-repo suite green

## 0. Ground truth from the source repos

Read before implementing; do not guess what these do.

**`scripts/gridcomposer21.lua`** (copied from `D:\OneDrive\CodeLibraries\iii`,
832 lines) — the "delicate" script, to run **unmodified**. Three pages behind
a menu row: (1) chromatic interval pad surface, (2) 7-track per-row step
sequencer with stutter/reset performance gestures, (3) the **harmonic master
arranger**: 8 stages, each with a root (circle-of-fifths layout, maj/min
columns), one of 15 chord types, per-stage duration, play mode (2=sustain,
3..6=retrigger at 24/12/6/3 pulses), and one of 8 groove patterns; loop
start/end; live audition with independent gate registers; retrigger via
held column 16.

Host API it consumes — this is the entire emulation surface:

| script calls | host calls into script |
|---|---|
| `grid_led(x, y, level)` 0..15 | `event_grid(x, y, z)` z=1 press, 0 release |
| `grid_led_all(level)` | `event_midi(b1, b2, b3)` raw bytes; 248 = clock |
| `grid_refresh()` | (script drives its own `draw()` via metro) |
| `midi_note_on(note, vel, ch)` / `midi_note_off` | |
| `metro.init(fn, seconds)` → object with `:start()` (assume `:stop()`) | |
| `print(...)` | |

Timing model: the script expects **24 ppqn MIDI clock** as `event_midi(248,…)`
— 96 pulses = 1 bar. It keeps its own counters; the host only ticks.

**`scripts/reference-c21-min.lua`** — minified variant with an **`SC` state
controller**: `SC.pend`/`SC.loadq` implement *bar-quantized state recall*
(pending state applied at the next bar boundary, off the clock ISR). Reuse
this pattern for preset recall and clip launching; do not reinvent it.

**`amyboard/reference-juno-moog-4enc.py`** (MicroPython, runs on AMYboard) —
the paging idiom to extend: press encoder N → page N (SYNTHESIS / EFFECTS /
AMP ENV / FILT ENV), four params per page, per-page encoder LED colors, OLED
`draw()`. Also demonstrates: `amy.send(synth=…, patch=…, num_voices=6)` Juno
patches, `bp0`/`bp1` envelope strings, global reverb/chorus/echo/eq macros,
**audio-in sidechain ducking** (AUDIO_IN drone voice, amp modulated by an
inverted envelope triggered from the kick channel), CV/gate out.

---

## 1. Architecture (decided; flagged items are defaults, not questions)

```
instrument/
  brain/                  Python package -- runs on the Pi AND on the dev machine
    host/                 the iii emulator: lua runtime, metro, clock, script mgmt
    grids/                grid backends: virtual (websocket), serialosc, neotrellis
    link/                 Pi <-> AMYboard protocol + transports (mock, i2c, midi)
    params.py             the parameter registry (single source of truth for UI)
    presets.py            preset = sequencer state + sound state
    clips.py              session view: 5 lanes x 8 scenes, quantized launch
    api.py                FastAPI app: REST + websockets, serves ui/
    ui/                   touch web UI -- same stack as the case editor:
                          no build step, vanilla ES modules, versioned assets
  scripts/                Lua that runs in the host
    gridcomposer21.lua    UNMODIFIED -- fidelity to the real iii is the point
    gridcomposer21.params.lua   companion param map (see 5.2) -- separate file
    minipad01.lua         the 8x4 chord/arp script (new, written tonight)
  amyboard/               MicroPython for the AMYboard (flashed later, tested
                          tonight only via its pure-python logic + protocol)
  presets/                saved presets (json), gitignored except examples/
  tests/                  pytest; runs headless on this machine
```

Decisions, with reasons:

- **UI is a web app** served by the brain, kiosk-browser fullscreen on the
  HyperPixel, ordinary browser during development. Same philosophy as the
  case editor (no build step, vendored deps, versioned asset URLs — reuse
  that cache-busting lesson). Touch-first: fat targets, horizontal sliders,
  no hover-dependent behaviour anywhere (we have learned this).
- **Lua host is `lupa`** (installed, verified). One `LuaRuntime` **per script
  instance** — gridcomposer21 and minipad01 are separate universes with
  separate grids; globals must not bleed.
- **Clock master is the brain.** One asyncio task generates 24 ppqn from BPM;
  every tick fans out to (a) each running script as `event_midi(248,0,0)`,
  (b) the clip scheduler, (c) the link (so AMYboard's own `sequencer.tempo`
  stays slaved). BPM is set from the touch UI (tap field + drag), range
  20..300, default 120.
- **Encoders belong to the AMYboard** (user decision, reversing earlier
  plan). The Pi never reads them. Sound params changed by encoders are
  *echoed* to the Pi over the link so the touch sliders mirror them.
- **Stock AMYboard firmware, no custom builds** (user decision). Everything
  we put on the AMYboard is plain MicroPython files loaded onto the firmware
  it ships with — that is the surface most likely to stay maintained, and it
  means updating the instrument never involves a toolchain. Consequence:
  the link can only use what stock MicroPython exposes, so transport
  research is the *first* task of Phase E, not an afterthought.
- **Pi ↔ AMYboard traffic is I2C** (user decision: "all the data that
  otherwise would come via midi comes via i2c") — notes, clock, params,
  preset ops, encoder echoes. Build the protocol transport-agnostic
  (`link/transport.py`): `MockTransport` for tests; `I2CTransport` (Pi
  master via smbus2) — give the stock-firmware I2C-slave path a **solid
  try**, it is the wiring that now physically exists: **the AMYboard hangs
  behind the TCA9548A multiplexer**, sharing it with the NeoTrellis tiles,
  so the transport selects the mux channel (channel mask to the mux, usually
  at 0x70) before every transaction, and one lock serialises the whole mux —
  the pad driver and the link must never interleave mid-transaction. Which
  channel and what slave address the AMYboard answers on are E0 findings,
  not assumptions; `SerialTransport`
  over the USB/UART REPL channel as the proven-reliable fallback (talking
  to stock MicroPython over serial is bread and butter); `MidiTransport`
  last. The framed protocol is identical on all four, so swapping costs
  nothing but the adapter.
- **8x4 pad orientation**: the two NeoTrellis tiles side by side = 8 wide x
  4 tall. "Lays it out vertically" = the arranger's 8 stages run along the
  long axis (x 1..8), stage data in the 3 rows above the mode row.
  **Row 4 is the mode row**: x1 live-play, x2 tracker, x3 groove, x4
  velocity, x5..7 reserved (dark), x8 menu — mirroring gridcomposer's
  menu-in-corner idiom.

---

## 2. Phase A — the iii host (the foundation; everything else leans on it)

### A1. Lua sandbox + API shim
`brain/host/runtime.py`. Class `IIIScript(path, grid, midi_out)`:
loads the file into a fresh `LuaRuntime`, injects `grid_led/grid_led_all/
grid_refresh` (writing into a 16x8 framebuffer of 0..15 ints owned by the
grid backend), `midi_note_on/off` (queued to `midi_out`), `print` (to the
script's own ring-buffer log, surfaced in the UI later), and `metro` (A2).
`script.event_grid(x,y,z)`, `.event_midi(...)` guarded: a Lua error is
caught, logged, and **does not kill the host** — a live instrument survives
a script bug.
**Done when**: pytest loads gridcomposer21.lua, the framebuffer is non-empty
after boot (`grid_refresh` ran), pressing the menu key `event_grid(16,8,1)`
changes the framebuffer, and a deliberate `error()` in a callback is
swallowed and logged.

### A2. metro
Faithful semantics: `metro.init(fn, seconds)` returns an object;
`:start()` schedules `fn` every `seconds` (0.03 in the wild) until `:stop()`.
Implementation: the host owns a monotonic scheduler pumped by
`IIIScript.tick(now)` — **no threads**; tests advance fake time.
**Done when**: a metro at 0.03 fires ~33x for one simulated second, stop
works, and gridcomposer21's `framework_tick` actually runs (its blink
counters advance).

### A3. Clock
`brain/host/clock.py`: BPM → 24 ppqn tick stream, asyncio task + a
`step(n_ticks)` method for tests (same object, two pumps). Subscribers:
scripts, clips, link. Beat/bar counters exposed (96 pulses = bar).
**Done when**: at 120 BPM one simulated bar takes 2.000 s of fake time;
feeding 96 ticks into gridcomposer21 with a root assigned on page 3
produces `midi_note_on` calls (the arranger steps) — this test is the
proof the emulation actually plays music.

### A4. Grid backend interface + virtual grid
`brain/grids/base.py`: `led(x,y,v)`, `led_all(v)`, `refresh()`,
`on_key(cb)`, `size` (16x8 or 8x4). `brain/grids/virtual.py`: framebuffer +
websocket push (dirty-flag on refresh, coalesced), key events from the UI.
`brain/grids/serialosc.py`: python-osc client — discovery on 12002,
`/grid/key`, `/grid/led/level/set|all|map`; **import-guarded**, not
testable tonight beyond message formatting (unit-test the OSC payloads).
`brain/grids/neotrellis.py`: stub with the mux addressing worked out in
comments (2x NeoTrellis behind the TCA9548A), level→RGB map for varibright;
import-guarded; real driver is device-day work.
**Done when**: virtual grid round-trips (led→refresh→snapshot; injected key
→ script event), serialosc payloads match the monome protocol docs
byte-for-byte in tests.

## 3. Phase B — gridcomposer21 running end to end (the fidelity milestone)

### B1. Headless integration test-suite for the unmodified script
Boot, page-switch via menu row, page 3: assign a root to a stage
(circle-of-fifths coords), 96 ticks → chord fires with the right intervals
(assert actual note numbers: e.g. C maj stage → 60,64,67); groove pattern
gates; retrigger via col-16 hold; loop start/end respected; all note-ons
eventually matched by note-offs (no stuck notes over 8 bars).
**Done when**: those assertions pass without having modified the script.
**Guardrail**: if a test seems to demand a script change, the host is wrong,
not the script — fix the host.

### B2. Script manager
`brain/host/manager.py`: named script slots (`grid` → gridcomposer21 on the
16x8 backend, `minipad` → minipad01 on the 8x4), start/stop/reload,
crash-restart with the log preserved. REST: list, state, reload.
**Done when**: two scripts run concurrently in one process without shared
globals (test: poke a global in one runtime, absent in the other), and
reload survives a syntax error by keeping the old instance running.

## 4. Phase C — minipad01.lua (the 8x4 chord/arp)

Extract **page 3 only** from gridcomposer21 and re-lay it for 8x4.
New file; copy freely from the original (same chord_types, circle roots,
groove_library, SC recall pattern from reference-c21-min.lua), but this one
is ours to edit.

Layout (8 wide, 4 tall):
- **Rows 1–3, columns 1–8**: the working surface, meaning depends on mode.
- **Row 4**: mode row — x1 **live** (interval pads: roots on row 1–3 as a
  mini circle-of-fifths, chords fire on press), x2 **tracker** (columns are
  the 8 stages; rows 1–3 show/edit root presence, cursor blinks on the
  playing stage; press row1–3 to enter stage-edit overlay: row1 root pick,
  row2 chord-type pick page, row3 duration 1..8 bars), x3 **groove** (rows
  1–3 select groove pattern + retrigger speed per stage), x4 **velocity**
  (rows 1–3 = 3-level velocity per stage, column = stage), x5–x7 dark,
  x8 **menu/shift** (hold: row 1 = transport run/stop, arm sync-reset —
  the gridcomposer gestures that must not be lost).
- All state lives in one `state` table; implement `state_save()` /
  `state_load(t)` returning/accepting a plain table — this is the preset
  hook, and the SC bar-quantized pattern applies pending loads at bar
  boundaries.
**Done when**: same class of headless tests as B1 pass on an 8x4 virtual
grid (chords fire, modes switch on row 4, save→mutate→load round-trips
exactly), and `iii_params` (below) exposes tempo-relative params.

## 5. Phase D — the parameter registry and the touch UI

### 5.1 Registry
`brain/params.py`. A parameter: `id` (dotted: `minipad.groove.speed`,
`amy.polyA.cutoff`), label, min/max/step/unit, value, `source`
(script | amyboard | bus | clock), bank + page + slot (layout position).
Everything the UI shows and everything presets snapshot goes through this
one object. Websocket topic per bank; sets are echoed to all clients and
routed to the owner (lua global write / link message).

### 5.2 Script params
Host injects `iii_params(defs)` — a script (minipad01 natively) registers
its tunables. For **gridcomposer21, which must stay unmodified**, a
companion file `gridcomposer21.params.lua` runs *in the same runtime after
it* and registers accessors for the interesting globals (retrigger speed,
groove selection, loop points, playmode…). The script never knows.
**Done when**: setting a param from Python moves the actual Lua global and
the change is observable in behaviour (retrigger speed test), both for
minipad and for the unmodified gridcomposer.

### 5.3 Touch UI shell
`brain/ui/`. Layout: **top tab strip** — AMYBOARD / PAD 8x4 / GRID /
SESSION / BUS. **Right sidebar** — bookmark buttons for the pages of the
active bank (fat, labeled, current page lit). Main area — **horizontal
slider banks** (name left, value right, full-width drag target ≥56 px tall,
immediate websocket set, remote changes animate). **Header** — BPM
(drag ±, tap to type), run/stop, bar:beat readout, link status dot.
Also: a **virtual grid page** under GRID and PAD tabs (the dev stand-in for
the hardware — tap = key, LEDs live), which is what makes everything
demoable in a browser tonight.
**Done when**: node structural tests (the shell.test.mjs approach: every
id wired, no dead controls) pass; slider set round-trips through a
websocket test client to a Lua global and back to a second client.

## 6. Phase E — the link protocol (Pi ↔ AMYboard)

`brain/link/protocol.py` — framed binary, transport-agnostic:
`[0xA5][len][type][payload…][crc8]`. Message types (one byte):
NOTE_ON/OFF{ch,note,vel}, CLOCK_TICK, TRANSPORT{run}, PARAM_SET{id16,f32},
PARAM_ECHO (AMYboard→Pi, encoder moves), PARAM_DUMP_REQ/RESP,
PRESET_SAVE{slot}/PRESET_RECALL{slot}/PRESET_DATA{chunked}, PING/PONG,
DRUM_TRIG{pad,vel}. Param ids are a **shared registry table generated into
both languages** (`link/paramids.py` → emits `amyboard/paramids.py`) so the
two sides cannot drift.
Transports: `MockTransport` (paired in-proc queues — the test rig),
`I2CTransport` (smbus2, import-guarded), `MidiTransport` (SysEx-wrapped,
fallback). The AMYboard side of the codec is **the same file** — written in
the MicroPython-compatible subset of Python (no f-strings beyond basics, no
dataclasses), imported by both brain and amyboard code, unit-tested once.
**Done when**: fuzz test — 1000 random valid frames encode→decode
identically; truncated/corrupted frames are rejected by CRC without
desyncing the stream (resync on 0xA5); a mock round-trip drives a fake
AMYboard state dict from brain param sets and echoes encoder moves back
into the registry.

## 7. Phase F — the AMYboard program

`amyboard/main.py` + modules, MicroPython, modeled on the reference
sketches. Cannot run tonight; **all pure logic must live in
importable, CPython-testable modules** (`amyboard/engine.py`,
`amyboard/pages.py`, `amyboard/paramids.py`), with `main.py` a thin
hardware shell (amy/amyboard imports live only there).

- **Voices**: ch0 polyA (Juno *or* DX7 patch, patch number is a param;
  `num_voices=4`), ch1 polyB (same, 4), ch2 monoA (the reference Moog
  patch), ch3 monoB, ch4 drums (AMY PCM sample set; params per drum bus:
  attack, release, pitch, distortion-drive, comp-ish makeup gain —
  **research note for the loop**: check AMY's PCM patch + any existing
  drum-machine conventions in the AMY docs/examples before inventing;
  record findings in BLOCKERS.md).
- **Bus**: global reverb/chorus/echo/eq as in the reference; **per-channel
  sidechain depth** — generalize the reference's ducking trick: DRUM_TRIG
  (kick) fires an inverted envelope scaled per-channel by
  `bus.sidechain.{ch}` into each synth's amp; **global comp/limiter**:
  research AMY capability; if absent, implement gain-staged soft-knee in
  the engine (documented approximation, param-compatible).
- **Encoders**: the quad NeoRotary on AMYboard's I2C, paging exactly like
  the reference (press = page). Pages: SYNTH A / SYNTH B / MONO A / MONO B /
  DRUMS / BUS-FX / BUS-DYN. Every encoder change → PARAM_ECHO.
- **Link**: the Phase E protocol serviced from a cooperative task in
  `main.py`'s loop — stock firmware, so plain `machine.I2CTarget`/`I2CSlave`
  if the port has it, else the serial channel; preset data chunks
  stored/recalled to AMYboard flash **and** echoed to the Pi so the Pi's
  preset files are the master copy.
- **Runs on stock firmware by construction**: no frozen modules, no custom
  C, nothing that will not survive a firmware update. If a feature seems to
  need firmware work, it goes to BLOCKERS.md with the stock-compatible
  approximation implemented instead.
**Done when**: engine/pages/protocol modules import and pass tests under
CPython (page math, param clamp/curve tables, bp-string builders — port the
reference's `_amp_bp_str`-style builders with exact-output tests), and
`main.py` passes a "MicroPython-subset" lint (no imports outside allowlist,
no f-string nesting, no dataclasses/typing at runtime).

## 8. Phase G — presets

`brain/presets.py`. A preset is one JSON document:
`{version: 1, name, created, sequencers: {grid: <state_save() of each
script>, minipad: …}, sound: {params: {amy.*: value…}}, clock: {bpm}}`.
Save = snapshot registry + script `state_save()`s; recall = **bar-quantized
by default** (SC pattern: stage the load, apply on bar tick; immediate mode
available). Sound params recall via PARAM_SET burst + PRESET_RECALL to the
AMYboard. Files in `instrument/presets/`, atomic write, schema-versioned.
**Done when**: save→perturb everything→recall restores registry, minipad
state and (mock) AMYboard state byte-for-byte; recall mid-bar applies only
at the next bar tick in a clocked test.

## 9. Phase H — the clip launcher (session view)

`brain/clips.py` + SESSION tab. Grid: **5 lanes** (polyA, polyB, monoA,
monoB, drums) × **8 scenes**. A clip = a *lane-scoped slice* of a preset
(its sequencer fragment + its sound params). Launch semantics: tap = arm;
armed clips apply at the next quantize boundary (default 1 bar; per-launch
choices 1/2/4 beats, 1/2/4 bars); playing/armed/empty states visibly
distinct (Ableton idiom); scene-launch column applies a whole row.
**Param transition option per clip**: numeric sound params glide linearly
over N beats (default off, N=4) — sequencer state always switches hard on
the boundary.
Also controllable from the 8x4 pad later (mode row reserve x5–x7 is where
that will live — do **not** build pad session control tonight, just leave
the seam).
**Done when**: clocked tests — arm mid-bar applies exactly on the boundary;
two lanes armed together apply atomically on the same tick; glide
interpolates monotonically over exactly N beats in the registry; UI
structural tests for the session tab.

## 10. Phase I — integration & polish (only after A–H are green)

- One `instrument/run.py` entry: clock + host (both scripts) + api + ui.
- A demo scenario test: boot, load example preset, launch scene 1, run 8
  bars headless, assert the full note stream to the mock link is
  deterministic (golden file).
- README-instrument.md: architecture, dev quickstart (`pytest`, `run.py`,
  open browser), Pi provisioning checklist, AMYboard flash checklist,
  open hardware risks (I2C slave, comp/limiter, serialosc discovery).
- The case-tool suite still green, untouched.

---

## Guardrails for the loop (read every iteration)

1. **Never modify** `backend/`, `web/`, `tools/`, `vendor/` or
   `scripts/gridcomposer21.lua`. The case tool and the reference script are
   frozen tonight.
2. Work in priority order A→I; within a phase, top to bottom. One checklist
   item per iteration is fine; finished and tested beats broad and broken.
3. Every completed item: mark it `[x]` here with one line of what proved it,
   run `python -m pytest instrument/tests -q` (must be green), commit with a
   real message. Full case-tool suite only at session end (it is 8 minutes).
4. Stuck twice on the same item → write it into `instrument/BLOCKERS.md`
   with what was tried, mark `[!]` here, move on. No rabbit holes.
5. Install whatever is needed (into `.venv`, never the system Python) —
   mpremote, pyserial, python-osc, python-rtmidi, whatever earns its place.
   Prefer wheels; if something demands a compiler and fights back, it goes
   to BLOCKERS.md rather than eating the night.
6. Hardware, audio and network access are allowed and encouraged. Rules of
   engagement: enumerate before assuming (COM ports change); every device
   test skips cleanly when the device is absent; **back up any file read
   from a device before overwriting it** (amyboard/device-backup/, dated);
   never flash or erase firmware — stock firmware is a standing decision;
   never kill processes by image name (kill by PID only — other Python
   apps run on this machine).
7. Simulators are first-class deliverables, not scaffolding — the virtual
   grid and mock link ship in the UI and stay.
8. Style: this repo's voice — comments explain *why*, tests assert
   behaviour someone once got wrong, honest notes over silent guesses.

## Deferred by decision (do not build tonight)

- Real NeoTrellis and Pi-side I2C code beyond import-guarded stubs — those
  need the Pi's bus, which this laptop does not have. (Serialosc and USB
  serial to the AMYboard are NOT deferred: test them for real if present.)
- Pad-controlled session view (seam reserved).
- DX7/Juno patch *editing* UI (patch number select only).
- Multi-grid-size auto-adaptation of gridcomposer21 (16x8 only, faithful).
- Audio rendering of AMY on the dev machine.

## Open questions parked with defaults

- **All-I2C vs MIDI-for-notes**: default all-I2C per user instruction;
  transport abstraction keeps the exit.
- **AMYboard as I2C slave under stock MicroPython**: first task of Phase E
  is a written finding (which `machine` classes the AMYboard port exposes,
  from docs — no hardware tonight); SerialTransport is the proven fallback
  and MidiTransport the last resort. The answer changes an adapter, not
  the protocol.
- **AMY compressor/limiter**: research in Phase F; approximation documented
  if absent.
- **Scenes count 8** and **glide default 4 beats**: chosen, easily changed.
