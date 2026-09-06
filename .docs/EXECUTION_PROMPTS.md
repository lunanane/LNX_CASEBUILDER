# Execution Prompts — Eurorack U-Profile Case Designer

Regenerated 2026-09-06 against the settled assembly model in
`DEVELOPMENT_PLAN.md` §3. The previous milestone set is superseded and has been
removed rather than annotated.

## Global execution rules (apply to every goal)

- Use `stacked-prs`; each implementation PR is based on the preceding stack branch until that base merges.
- Use Conventional Commits, atomic commits, no attribution, and independently reviewable PRs.
- Run the pre-implementation design gate before creating product-code branches or changing product code.
- The committed plan/prompt files are authoritative. The local ignored history ledger is evidence; rebuild it from committed artifacts, merged PRs and current code when absent.
- A material plan change must update the current milestone and every affected future milestone before implementation, then rebuild the DAG and release trains.
- A docs-only reconciliation PR is required for a material revision, reviewed, green and externally merged before code begins.
- A shared mismatch in a parallel wave blocks product-code work in every affected lane.
- `GO` only makes the stack merge-eligible; release preparation stays deferred until every train member merges.
- Repo has no CI, no tags and no `CHANGELOG.md` (GAP-2). No milestone creates version, changelog, tag or publication work. "Green" means the local verification commands pass.
- **The assembly model in `DEVELOPMENT_PLAN.md` §3 is settled.** Do not re-derive it. If a milestone's evidence contradicts it, that is a material mismatch — stop and reconcile.

### The design gate (referenced by every goal below as GATE)

```text
1. Read this milestone, its source-map rows, current prompt, and `.docs/DEVELOPMENT_PLAN_HISTORY.md` when present.
2. Inspect the current codebase plus merged predecessor diffs, PR outcomes, check evidence and predecessor verification output.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train and every listed dependent milestone.
4. Append one ledger entry: timestamp, milestone, decision, trigger, evidence, sections changed, downstream impact, authorization.
5. No material mismatch -> report `DESIGN GO — PLAN REVISION: none`; this authorizes implementation.
6. Mismatch -> update both authoritative artifacts for this milestone and every affected future milestone, append the revision ID, report `DESIGN GO — PLAN REVISION: <entry IDs>`. Diagnosis complete, but product-code work is blocked until the reconciliation PR merges.
7. Validity not establishable -> report `DESIGN NO-GO — REASON: <evidence>` and stop. Repeat the gate after reconciliation merges and require `PLAN REVISION: none`.
```

### The review and verdict contract (referenced as CONTRACT)

```text
REVIEW — per PR: scope matches purpose; contracts match the reconciled plan; behavior meaningfully tested; failures loud; history atomic, conventional, attribution-free; PR-specific verification captured.
REVIEW — whole stack: bases form one valid stack; cumulative acceptance holds; green; no regression coverage removed without replacement; the docs-only root (if any) reviewed and green before dependent code PRs. Report PR URLs, bases, verification, risks, manual gates, review completion.
FINAL VERDICTS: report the design verdict first, then exactly one of `GO — RELEASE: unversioned — RELEASE PREP: <pending | not-required>` or `NO-GO — RELEASE: unversioned — REASON: <blocking gate>`. `GO` requires DESIGN GO, every PR based/reviewed/green, local verification and full acceptance.
NEXT STEPS: render the literal heading. 1. Current milestone. 2. Release. 3. Next milestone with dependency evidence. 4. On NO-GO, remediation and exact retry gate. On GO steps 1-3 are mandatory; on NO-GO all four are, and never advance a dependent milestone.
DONE: design verdict with evidence; when authorized, a reviewed stack with a release-aware merge verdict and the next-steps list.
```

---

### M1 — Eurorack format module, and the 122.5 mm correction

```text
/goal Deliver milestone M1 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M1 + docs/eurorack.md §§1-3,6,9. Preconditions: none. Repo: Python 3.10, pytest from backend/, pydantic v2, no CI.
OBJECTIVE: The format's arithmetic lives in one module and the rail-spacing defect is corrected everywhere it was copied. Acceptance: all 16 published HP panel widths asserted exactly; panel_width never exceeds a tabled value; frame_width(84) rounds to f&w's 427 mm; slot_span(3) == 122.5; 3U rail hole Ys are 3.0 / 125.5; no rail-spacing 123.5 remains.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE (see global rules). Dependents to revalidate: M3, M5, M6, M9.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M1 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M1 design` — docs only; merged before the stack.
1. PR-1 `feat(eurorack): add the format arithmetic module` — scope: `backend/hwcase/eurorack.py`, `backend/tests/test_eurorack.py`; verification: `cd backend && python -m pytest tests/test_eurorack.py -q`
2. PR-2 `fix(parts): correct 3U rail hole spacing to 122.5 mm` — scope: `backend/parts/misc.yaml`, `docs/research.md`, `docs/measurements.md`; verification: `grep -rn "123\.5" docs backend | grep -i rail` is empty
CONSTRAINTS: keep `panel_width` and `frame_width` distinct and documented — the panel table is per-module clearance and must never reach the frame; no new dependencies; match the prose-heavy comment style of `schema.py`.
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures; `python -m hwcase.cli check scenes/soundmachine-lnx1.yaml` → no new errors; `grep -rn "123\.5" docs backend | grep -i rail` → empty.
CONTRACT (see global rules). NEXT STEPS step 3 candidates: M2 or M4, both independent of M1.
```

### M2 — Pipeline discriminator

```text
/goal Deliver milestone M2 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M2 + `schema.py` (Scene), `cli.py`, `scenefile.py`, `api.py`. Preconditions: none. Repo: Python 3.10, pydantic v2, ruamel round-trip scene saving, FastAPI.
OBJECTIVE: A scene declares which pipeline builds it, and every existing scene behaves exactly as before. Acceptance: all five existing scenes build byte-identically; `new --kind rack` produces a scene check accepts; unknown kind fails with a named error; scenefile does not write `kind` into scenes that lacked it.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Dependents to revalidate: M3, M7.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M2 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M2 design` — docs only.
1. PR-1 `feat(schema): add Scene.kind pipeline discriminator` — scope: `schema.py`, `scenefile.py`, round-trip tests; verification: `cd backend && python -m pytest tests/ -q`
2. PR-2 `feat(cli): scaffold scenes per pipeline and dispatch build` — scope: `cli.py`, `api.py`; verification: `cd backend && python -m hwcase.cli new --kind rack -o /tmp/r.yaml && python -m hwcase.cli check /tmp/r.yaml`
CONSTRAINTS: no rack geometry; no edits to existing scene files; no new dependencies.
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures; build every scene in `backend/scenes/` before and after → identical file names and sizes.
CONTRACT. NEXT STEPS step 3 candidates: M3 once M1 is merged, else M4.
```

### M3 — Rack specification schema and checks

```text
/goal Deliver milestone M3 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M3 and §3 (assembly model) + `schema.py` (CaseSpec as the sibling to mirror), `scene.py` (check), `eurorack.py` (M1). Preconditions: M1, M2 merged.
OBJECTIVE: A frame of any size is described by validated data and impossible frames are rejected. Acceptance: colliding rails error; an insert wider than the side panel's depth errors; a mixed 1U+3U+1U stack validates and reports 5U; a 68 HP 6U spec reports 0 errors.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Dependents to revalidate: M5, M6, M8, M9.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M3 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M3 design` — docs only.
1. PR-1 `feat(schema): add RackSpec, RackRow and side-panel inserts` — scope: `schema.py`, tests; verification: `cd backend && python -m pytest tests/ -q`
2. PR-2 `feat(scene): validate rack specs in check` — scope: `scene.py`, one test per rule; verification: `cd backend && python -m pytest tests/ -q`
3. PR-3 `feat(cli): report rack issues in check output` — scope: `cli.py`, a rack fixture; verification: `cd backend && python -m hwcase.cli check <fixture> --strict`
CONSTRAINTS: rows are an ORDERED LIST of RowFormat, never a count — a count cannot express the built 1U+3U+1U case; inserts belong to the SIDE PANEL, not the rail; no geometry.
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures; `cd backend && python -m hwcase.cli check <rack fixture> --strict` → exit 0.
CONTRACT. NEXT STEPS step 3 candidates: M5 once M4 is merged, else M4.
```

### M4 — Frame stock as measured parts

```text
/goal Deliver milestone M4 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M4 and §3.1-3.2 + `docs/eurorack-frame-stock.md` (authoritative transcription) + `backend/parts/misc.yaml` as the style reference. Preconditions: none.
OBJECTIVE: The real stock is in the library with honest confidence tags. Acceptance: `parts` lists them; every dimension is measured/datasheet or listed as outstanding; no `estimated` in this file; the rail records its INSTALLED orientation (10.00 tall, 19.00 deep) per §3.1, not the drawing's.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Dependents to revalidate: M5, M6. Also resolve the acrylic's asymmetric 3 mm holes (x = 59 vs 180) against the physical part.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M4 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M4 design` — docs only.
1. PR-1 `feat(parts): add Eurorack frame stock` — scope: `backend/parts/eurorack-frame.yaml`, `docs/measurements.md`; verification: `cd backend && python -m hwcase.cli parts && python -m hwcase.cli audit --strict`
CONSTRAINTS: transcribe from `docs/eurorack-frame-stock.md` only; do not invent a length rule (that is M5); no `estimated` confidence anywhere in this file.
VERIFICATION (must pass): `cd backend && python -m hwcase.cli parts` lists rail, U-channel and acrylic; `python -m hwcase.cli audit --strict` → exit 0; `python -m pytest tests/ -q` → 0 failures.
CONTRACT. NEXT STEPS step 3 candidates: M5 once M3 is merged.
```

### M5 — Rail and body geometry

```text
/goal Deliver milestone M5 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M5 and §3 + `case.py` (the sibling builder), `eurorack.py` (M1), RackSpec (M3), `backend/parts/eurorack-frame.yaml` (M4). Preconditions: M3, M4 merged.
OBJECTIVE: A validated spec becomes parametric 3D — rails extruded to length, a folded body whose web follows the row stack. Acceptance: a 1U+3U+1U stack reproduces the built 5U web of 216.00 mm; rail count == 2 x rows; every rail Y equals a row slot centre from eurorack.slot_span; frame_width(68) == 345.44; two builds identical.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Confirm §3.5 holds: the side panels carry every rail (one M5 per end) and a case has exactly one body, however tall. Dependents to revalidate: M6, M7, M8, M9.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M5 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M5 design` — docs only.
1. PR-1 `feat(rack): add Member and RackModel` — scope: `backend/hwcase/rack.py`, tests; verification: `cd backend && python -m pytest tests/ -q`
2. PR-2 `feat(rack): rail cross-section and extrusion to length` — scope: `rack.py`, tests; verification: `cd backend && python -m pytest tests/ -q`
3. PR-3 `feat(rack): fold the U-body from the row stack` — scope: `rack.py`, the 216.00 regression test; verification: `cd backend && python -m pytest tests/ -q`
4. PR-4 `feat(rack): place rails at row slot centres` — scope: `rack.py`, determinism test; verification: `cd backend && python -m pytest tests/ -q`
CONSTRAINTS: no CAD kernel; deterministic; a member is a section polygon plus a length plus a pose — do not reuse the layer stack; hold to `case.build`'s speed contract; no side panels (M6) and no file formats (M8).
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures; `cd backend && python -m hwcase.cli build <rack fixture> -o ../out` reports a non-zero member count.
CONTRACT. NEXT STEPS step 3 candidates: M6.
```

### M6 — Parametric side panels with M5 fixings and 4 HP slots

```text
/goal Deliver milestone M6 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M6 and §3.4 (the decoded 5U panel) + `rack.py` (M5). Preconditions: M5 merged.
OBJECTIVE: The side panel generates itself from the row stack — M5 holes where the rails land, 4 HP slots where inserts go. Acceptance: for 1U+3U+1U the generated M5 pattern reproduces the built spans 35 / 123 / 35 with 10 mm gaps (or the rack-correct 33.6 / 122.5 / 10.85 — GAP-1's choice, asserted either way); panel outline is 237 x 52 for that stack; hole count == rail count; each insert slot is 4 HP across and breaks exactly one edge.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. **Settle GAP-1 before asserting hole spans.** Dependents to revalidate: M7, M8, M9.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M6 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M6 design` — docs only.
1. PR-1 `feat(rack): generate the side panel outline` — scope: `rack.py`, tests; verification: `cd backend && python -m pytest tests/ -q`
2. PR-2 `feat(rack): derive M5 rail-fixing holes from the row stack` — scope: `rack.py`, the 35/123/35 regression test; verification: `cd backend && python -m pytest tests/ -q`
3. PR-3 `feat(rack): cut 4 HP insert slots into the side panels` — scope: `rack.py`, edge-breaking assertion; verification: `cd backend && python -m pytest tests/ -q`
CONSTRAINTS: the M5 pattern is DERIVED from rail positions, never a literal table — the built panel is a regression fixture, not the source; a slot that fails to break its edge is a defect, not a variant; inserts add capacity outboard of the rails and must not change the rail span.
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures.
CONTRACT. NEXT STEPS step 3 candidates: M8, or M7 in parallel.
```

### M7 — 3D in the editor

```text
/goal Deliver milestone M7 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M7 + `web/app.js`, `web/store.js`, `web/tests/store.test.mjs` (store files currently uncommitted — read first), `api.py`. Preconditions: M2, M6 merged.
OBJECTIVE: The frame is visible and parametric in the editor. Acceptance: choosing "Eurorack frame" creates a scene check accepts; changing rows from 3U to 1U+3U+1U changes the rendered web height; parts scenes render unchanged; store tests cover the branch.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Dependents to revalidate: none.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M7 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M7 design` — docs only.
1. PR-1 `feat(web): choose a pipeline when starting a project` — scope: `web/app.js`, `web/store.js`, store tests; verification: `cd web && node --test tests/`
2. PR-2 `feat(web): edit rows, HP and side-panel inserts` — scope: `web/app.js`, `web/style.css`, store tests; verification: `cd web && node --test tests/`
3. PR-3 `feat(web): extrude frame members in the preview` — scope: `web/app.js`, `api.py`; verification: `cd web && node --test tests/`
CONSTRAINTS: the parts editor is untouched for `kind: parts`; the pipeline choice is a default, not a lock; a member extrudes a section along its own axis — do not assume Z as the layer viewer does; no new front-end dependencies.
VERIFICATION (must pass): `cd web && node --test tests/` → 0 failures; `cd backend && python -m pytest tests/ -q` → 0 failures.
CONTRACT. NEXT STEPS step 3 candidates: M9 if unmerged, else none — M7 is a leaf.
```

### M8 — Exports and bill of materials

```text
/goal Deliver milestone M8 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M8 + `export.py` (write_all, plate packing, margins), `rack.py` (M5, M6). Preconditions: M6 merged.
OBJECTIVE: The frame leaves the tool as something a person can cut, drill and buy from. Acceptance: a rack build writes a cutlist and at least one DXF; every hole offset matches the model within 0.01 mm; M5 count == rail ends == 2 x rails; exports match existing gitignore patterns.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Confirm `export.write_all`'s signature fits a non-layer model. Dependents to revalidate: M9.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M8 design`, docs-only, merged before any code PR.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M8 design` — docs only.
1. PR-1 `feat(export): write member cutlists for rack builds` — scope: `export.py`, tests; the cutlist header names the datum end explicitly; verification: `cd backend && python -m pytest tests/ -q`
2. PR-2 `feat(export): emit drill DXFs per member and side panel` — scope: `export.py`, tests comparing DXF entity positions to the model; verification: `cd backend && python -m pytest tests/ -q`
3. PR-3 `feat(export): bill of materials in supplier terms` — scope: `export.py`, count assertions; verification: `cd backend && python -m pytest tests/ -q`
CONSTRAINTS: reuse the existing plate-packing, margin and naming contracts; sheet parts go through the existing layer export, not a parallel path; the BOM is in supplier terms (profile mm, rail mm, M5/M3 counts, sliding nuts); no new dependencies.
VERIFICATION (must pass): `cd backend && python -m pytest tests/ -q` → 0 failures; `cd backend && python -m hwcase.cli build <rack fixture> -o ../out` writes a cutlist and DXFs.
CONTRACT. NEXT STEPS step 3 candidates: M9.
```

### M9 — 68 HP 6U reference frame

```text
/goal Deliver milestone M9 from DEVELOPMENT_PLAN.md as a reviewed stack of PRs.

CONTEXT: DEVELOPMENT_PLAN.md §6 M9 and §3.5 (capacity) + `docs/eurorack.md`, `docs/eurorack-frame-stock.md`, `backend/scenes/` as the pattern. Preconditions: M8 merged; GAP-1 closed.
OBJECTIVE: The frame being built next comes out of the tool, checked and ready to cut. Acceptance: `check --strict` exits 0; frame width resolves to 345.44 mm; web resolves to 260.45 and the side panels to 280 mm tall with M5 holes at 12 / 135 / 145 / 268; four 4 HP slots — one per side per row — giving 76 HP capacity; BOM lists 4 rails, 8 M5 screws, the body and two side panels.
RELEASE TRAIN: target=unversioned; members=M1..M9; trigger=all merged; artifacts=none; verification=`cd backend && python -m pytest tests/ -q` exits 0; publication=not requested.
PRE-IMPLEMENTATION DESIGN GATE: GATE. Dependents to revalidate: none.
RECONCILIATION RULE: a material revision opens `docs(plan): reconcile M9 design`, docs-only, merged before any code PR.
HUMAN REVIEW GATE: Do not treat the generated cutlist as cuttable until a human reviews the drill offsets, the named datum end and the BOM against `docs/eurorack-frame-stock.md`. Aluminium is bought to length and drilled once.
PLANNED STACK:
0. Conditional `docs(plan): reconcile M9 design` — docs only.
1. PR-1 `feat(scenes): add the 68 HP 6U reference frame` — scope: `backend/scenes/rack-68hp-6u.yaml` with GAP-1's resolution in its comments, plus a test asserting resolved width and web; verification: `cd backend && python -m hwcase.cli check scenes/rack-68hp-6u.yaml --strict`
2. PR-2 `docs: document the Eurorack frame pipeline` — scope: `README.md`; verification: the commands in the new section execute as written
CONSTRAINTS: 68 HP is the RAIL SPAN; the side panels add 4 HP each on top of it, so capacity is 76 HP per row — do not widen the rails to 76. This milestone composes M1-M8 and adds no engine capability; if a capability is missing, stop and report a mismatch rather than widening scope.
VERIFICATION (must pass): `cd backend && python -m hwcase.cli check scenes/rack-68hp-6u.yaml --strict` → exit 0; `python -m hwcase.cli build scenes/rack-68hp-6u.yaml -o ../out` writes cutlist, DXFs and BOM; `python -m pytest tests/ -q` → 0 failures.
CONTRACT. NEXT STEPS step 3: none — M9 is last in the train.
```
