# Execution Prompts — Catalogue, Part Models and Eurorack Case Styles

Generated 2026-10-16 from `DEVELOPMENT_PLAN.md`. Supersedes the 2026-09-06
U-profile prompts.

## Global execution rules (apply to every goal)

- **One run (A-1).** All milestones run in one team-lead session. Create
  `feat/catalog-models-eurorack` from `master` once. Each milestone is a stack
  of branches (`m<N>/<n>-<slug>`), each based on the previous one, the first on
  the integration branch. After the milestone's `GO`, merge the stack into the
  integration branch; that merge is "externally merged" for every dependency
  rule below. Never merge into `master`; the builder does that.
- Use `stacked-prs` conventions locally: atomic commits in the repository's
  existing message style (an outcome sentence as the title, the reasoning in the
  body), independently reviewable branches, no unrelated formatting churn.
- Run the design gate (GATE) before creating product-code branches or changing
  product code.
- Collect bulky evidence (predecessor diffs, verification output, per-branch
  review) in dedicated read-only subagents, and re-check load-bearing citations
  before acting on them. Verdicts, ledger appends, plan/prompt edits and git
  topology stay with the session that owns the milestone.
- The committed plan/prompt files are authoritative. The local ignored history
  ledger is evidence; rebuild it from committed artifacts, merged branches and
  current code when absent.
- A material plan change updates the current milestone and every affected
  future milestone in both files before implementation, then rebuilds the DAG
  and release train. It lands as a docs-only commit on the integration branch,
  `docs(plan): reconcile M<N> design`, verified before any code.
- A shared mismatch in a parallel wave blocks product-code work in every
  affected lane until reconciled.
- `GO` makes the stack mergeable into the integration branch. Release
  preparation stays deferred until every train member has merged.
- No CI, no tags, no `CHANGELOG.md`, no `gh` (GAP-1). "Green" means the local
  verification commands pass. No milestone creates version, changelog, tag or
  publication work.
- `python` is the project venv (`.venv\Scripts\python`). The full backend suite
  takes about 18 minutes: run each milestone's narrow tests per branch and the
  full suite once per milestone, before its verdict.
- **Settled decisions (D-1…D-5, plan §1) are not reopened.** Both joint types;
  dynamic sheet thickness; modules sit on the rails and the module face is the
  upper surface; no tilted rows; STEP reading is required.

### The design gate (referenced as GATE)

```text
1. Read this milestone, its source-map rows, current prompt, and `.docs/DEVELOPMENT_PLAN_HISTORY.md` when present.
2. Inspect the current codebase plus merged predecessor diffs, their verdicts and their verification output.
3. Revalidate objective, interfaces, dependencies, acceptance, verification, risks, release train and every listed dependent milestone.
4. Append one ledger entry: timestamp, milestone, decision, trigger, evidence, sections changed, downstream impact, authorization.
5. No material mismatch -> report `DESIGN GO — PLAN REVISION: none`; this authorizes implementation.
6. Mismatch -> update both authoritative files for this milestone and every affected future milestone, append the revision ID, report `DESIGN GO — PLAN REVISION: <entry IDs>`. Product code is blocked until the reconciliation commit lands on the integration branch and verifies.
7. Validity not establishable -> report `DESIGN NO-GO — REASON: <evidence>` and stop. After reconciliation, repeat the gate and require `PLAN REVISION: none`.
```

### Review and verdict contract (referenced as CONTRACT)

```text
RECONCILIATION RULE: a material revision lands first as the docs-only commit `docs(plan): reconcile M<N> design` on the integration branch; no product code in it; never folded into an implementation branch.
REVIEW — per branch: scope matches its purpose; contracts match the reconciled plan; behaviour meaningfully tested; failures loud; history atomic and in repo style; branch-specific verification output captured.
REVIEW — whole stack: bases form one valid stack; cumulative acceptance and integration hold; full milestone verification green; no regression coverage removed without replacement; any reconciliation commit verified before dependent code. Report branches, bases, verification, risks, manual gates, review completion.
FINAL VERDICTS: report the design verdict first, then exactly one of `GO — RELEASE: unversioned — RELEASE PREP: <pending | not-required>` or `NO-GO — RELEASE: unversioned — REASON: <blocking gate>`. `GO` requires DESIGN GO, every branch based, reviewed and green, local verification, and full acceptance. NO-GO for pending or failed checks, incomplete review, scope drift, ambiguous readiness or open manual gates.
NEXT STEPS: render the literal heading. 1. Current milestone: <merge the stack into feat/catalog-models-eurorack | already merged | stop on NO-GO>. 2. Release: <deferred until M1–M10 merge | begin declared preparation | not-required>. 3. Next milestone: <M# with dependency evidence | SKIP <current> FOR NOW; RUN M# — independent of <closure> | none — reason>. 4. On NO-GO: <specific remediation and exact retry gate>; otherwise `not applicable`. On GO steps 1–3 are mandatory; on NO-GO all four, and never advance a dependent milestone.
DONE: design verdict with evidence; when authorized, a reviewed stack merged into the integration branch with a release-aware verdict and the next-steps list.
```

Release train for every goal: target=`unversioned`; included milestones=M1–M10;
preparation trigger=all included milestones merged into
`feat/catalog-models-eurorack`; required artifacts=`none` (GAP-1); release
verification=`cd backend; python -m pytest tests/ -q` and
`cd web; node --test tests/` exit 0; publication=`not requested`.

Run order (plan §8): M1, M5, M6, M2, M3, M4, M7, M8, M9, M10. M10 is always
last; its NEXT STEPS starts the release-train verification.

---

### M1 — Catalogue index, freshness and an import baseline

```text
/goal Deliver milestone M1 (Catalogue index, freshness and an import baseline) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M1; .docs/REVIEW_CATALOG_MODELS_EURORACK.md Part 1 (F2–F5, F9–F11; C1, C6, C7). Preconditions: none. Repo: Python 3.10 venv, pytest, FastAPI; backend/hwcase/catalog.py, ingest.py; no CI.
OBJECTIVE: index every Adafruit CAD folder and file from one pinned git-tree request; keep the catalogue fresh in a long-running server; measure import success. Acceptance = plan M1 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Also re-check the tree API's `truncated` flag and the unauthenticated rate limit.
PLANNED STACK:
1. Index from the git tree — catalog.py builds product → folders → files (with sizes and the commit SHA) from `/git/trees/main?recursive=1`; optional GITHUB_TOKEN; TTL re-checked on every request; `importable` from available formats (STL only until M2); `discontinued` / `in_stock` from the feed; correct the "refreshed on a timer" docstrings. Tests: tests/test_catalog.py with fixture tree including duplicate product IDs and a stale-cache refresh using a stubbed fetch.
2. Downloads and survey — ingest.py downloads from the indexed file list; HWCASE_MAX_CAD_MB (default 64) with a named error; tools/import_survey.py --sample N --seed S writing CSV + summary; commit the sample-40 result as tools/import_survey_baseline.csv.
CONSTRAINTS: no scope leakage (no STEP reading, no background import jobs); no new dependencies; repo style.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_catalog.py -q` → 0 failures; `python ../tools/import_survey.py --sample 40 --seed 1` prints a summary; full backend suite → 0 failures.
CONTRACT: apply.
```

### M2 — STEP import and geometry checks

```text
/goal Deliver milestone M2 (STEP import and geometry checks) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M2, A-4; review Part 1 (F1, F6, F7; C2, C4). Preconditions: M1 merged. Repo: backend/hwcase/measure.py, ingest.py, audit.py; vendor/cad/adafruit/{3954,4741,5752}.
OBJECTIVE: import STEP-only products with real geometry, and check every import before trusting it. Acceptance = plan M2 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Confirm `cascadio` installs in the venv and (when Docker is available) in `docker build .`; otherwise record it as a manual gate.
PLANNED STACK:
1. Read STEP — add `cascadio` to backend/requirements.txt; measure.bodies accepts STEP via trimesh; ingest prefers STEP when present; holes from the same file; `importable` true for STEP-only folders. Tests: tests/test_step.py comparing STEP and STL imports of the three vendored models (outline within 0.3 mm) and a STEP-only fixture.
2. Check before trusting — reject and warn on holes outside the board or inside a component; unit-sanity warning; `outline: poly` from the board body's projected footprint when it fills under 95 % of its bounding box. Tests: round-board and shifted-hole fixtures; re-run the sample-40 survey and update tools/import_survey_baseline.csv.
CONSTRAINTS: `cascadio` is the only new dependency; existing ingest and audit tests unchanged.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_catalog.py tests/test_step.py -q` → 0 failures; survey success ≥ tools/import_survey_baseline.csv + STEP-only folders in the sample; full backend suite → 0 failures.
CONTRACT: apply.
```

### M3 — Import review and connector proposals

```text
/goal Deliver milestone M3 (Import review and connector proposals) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M3; review Part 1 (F5, F8; C3, C5). Preconditions: M2 merged. Repo: backend/hwcase/ingest.py, api.py; web/app.js, web/store.js, web/tests.
OBJECTIVE: review an import before it is saved, and propose connectors instead of none. Acceptance = plan M3 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Use the measured connectors in backend/parts/*.yaml as signature fixtures.
PLANNED STACK:
1. Connector signatures — hwcase/connectors.py matching body sizes to STEMMA QT, JST PH 2/3/4, USB-C, micro-B, 3.5 mm jack and 2.54 mm headers; face from the nearest board edge; every proposal `estimated`; product-name keywords as a prior. Tests: tests/test_connectors.py (4741 gives two STEMMA QT on opposite edges; interior bodies never match).
2. Preview endpoint — POST /api/catalog/preview/{pid}?file=&flip= returning draft, warnings and candidate files; flip override recorded in notes. Tests in test_catalog.py.
3. Editor import step — file choice, turn-over and warnings shown before saving; store tests in web/tests.
CONSTRAINTS: no connector editing UI; no mesh preview (M4).
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_connectors.py tests/test_catalog.py -q` → 0 failures; `cd web; node --test tests/` → 0 failures; full backend suite → 0 failures.
CONTRACT: apply.
```

### M4 — Display models

```text
/goal Deliver milestone M4 (Display models) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M4, A-4; review Part 2 (M1–M4, M6, M7). Preconditions: M2 merged. Repo: schema.Part, ingest.py, export.scene_to_json, raytrace.py, audit.py; web/app.js buildSolids; web/vendor (three r169).
OBJECTIVE: show parts with vendor CAD as their real model in the editor and the photograph; checks and cuts still use the boxes. Acceptance = plan M4 rows (1)–(5).
RELEASE TRAIN: see header.
GATE: run it. Confirm r169 GLTFLoader.js's import graph before vendoring.
PLANNED STACK:
1. Model data — `Part.model` (file, units, offset, flip, rotation, colour); importer writes it; add it to the three vendored Adafruit parts; GLB generation with `fast_simplification` to a 40 k-triangle default budget, cached; GET /api/parts/{id}/model.glb. Tests: tests/test_models.py (budget, bounds within 0.5 mm, /api/build byte-identical).
2. Editor — vendor GLTFLoader.js and BufferGeometryUtils.js from r169, add them to the import map; view → real models, fetching models only when switched on; PCB colour plus a neutral metal for components; boxes as outlines while models are on; selection and ghosting stay on boxes; shared loads for repeated parts. Web tests for the toggle state.
3. Renderer and audit — raytrace writes model meshes (testable through build_scene_dict without Mitsuba); audit checks model bounds against boxes.
CONSTRAINTS: `fast_simplification` is the only new dependency; no colours from STEP/3MF; no models for the hand-written parts.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_models.py -q` → 0 failures; `python -m hwcase.cli audit --strict` → exit 0; `cd web; node --test tests/` → 0 failures; full backend suite → 0 failures. Manual gate: toggle real models on soundmachine-v0 and confirm alignment by eye.
CONTRACT: apply.
```

### M5 — Flat plates as cut parts, and frames from the command line

```text
/goal Deliver milestone M5 (Flat plates as cut parts, and frames from the command line) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M5; review Part 3 G1; superseded plan's M8/M9 scope. Preconditions: none. Repo: backend/hwcase/export.py (pack_sheets, rack_panels_svg), rack.py, cli.py, api.py.
OBJECTIVE: every cut piece from either pipeline is a flat part packed onto stock sheets; frames build and export from the CLI. Acceptance = plan M5 rows (1)–(4).
RELEASE TRAIN: see header.
GATE: run it. Before any change, capture byte-for-byte sheet exports of every layered scene in backend/scenes as the regression fixture.
PLANNED STACK:
1. FlatPart — hwcase/flat.py; layers and rack plates both produce flat parts; pack_sheets takes them. Tests: tests/test_flat.py including the byte-identical regression.
2. Rack exports — sheets SVG/DXF and a cut list for rack scenes (plates; rails with length and count; M5 count = 2 × rails; U-channel order); /api/export/sheets/{fmt} accepts kind: rack. Tests: hole centres within 0.01 mm.
3. CLI — `check` prints a frame summary and `build` writes the frame's sheets and cut list for kind: rack; never a layered case. Tests in test_rack.py.
CONSTRAINTS: no joints, no new frame style.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_flat.py tests/test_rack.py -q` → 0 failures; `python -m hwcase.cli build scenes/frame-6.yaml -o ../out` writes sheets and a cut list; full backend suite → 0 failures.
CONTRACT: apply.
```

### M6 — Materials and joints

```text
/goal Deliver milestone M6 (Materials and joints) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M6, A-3, A-6, D-1, D-2; review Part 3 G2, G3. Preconditions: none. Repo: schema.Material, shapely geometry conventions in case.py and rack.py.
OBJECTIVE: join two plates at an edge with finger joints or captive-nut T-slots, computed from each material's own thickness. Acceptance = plan M6 rows (1)–(5), parametrised over 2.0–8.0 mm and both joint types.
RELEASE TRAIN: see header.
GATE: run it. Agree the edge representation with M5's FlatPart datum (if M5 has not merged, use a plain 2D edge spec and record the adapter as an M7/M9 task).
PLANNED STACK:
1. Materials — per-material thickness, kerf, joint clearance and finish, all user-set; fastener table (M3 per A-6).
2. Joinery — hwcase/joinery.py: finger_joint, tslot_joint, validate_joint; keep-clear zones. Tests: tests/test_joinery.py property tests over thickness and both types.
CONSTRAINTS: pure geometry; no case pipeline uses joints yet.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_joinery.py -q` → 0 failures; full backend suite → 0 failures.
CONTRACT: apply.
```

### M7 — Fully laser-cut Eurorack frame

```text
/goal Deliver milestone M7 (Fully laser-cut Eurorack frame) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M7, D-4; review Part 3 Style B. Preconditions: M5 and M6 merged. Repo: rack.py, schema.RackSpec, web frame pane.
OBJECTIVE: build a frame from aluminium rails and laser-cut plates only. Acceptance = plan M7 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Confirm the rail and M5 arithmetic is reused from rack.py, not copied.
PLANNED STACK:
1. Laser members — `rack.style: u_channel | laser` (default u_channel); cheeks from the side-panel outline plus joints; base, back and optional top plates; material and joint type per frame; BOM with M3 bolts and nuts, no U-channel. Tests in test_rack.py (identical u_channel members; cheek holes within 0.01 mm; validate_joint; 3 → 6 mm thickness; nut count = T-slots).
2. Editor and export — frame pane gains style, material thickness and joint type; exports through M5. Web tests.
CONSTRAINTS: no tilted rows (D-4); no front lip (D-3); no deck or hybrid features.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_rack.py tests/test_joinery.py -q` → 0 failures; `cd web; node --test tests/` → 0 failures; full backend suite → 0 failures.
CONTRACT: apply.
```

### M8 — Eurorack module panel designer

```text
/goal Deliver milestone M8 (Eurorack module panel designer) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M8, A-5; review Part 3 Style C; docs/eurorack.md §§2–3, 8. Preconditions: none. Repo: eurorack.py (panel_width, ROW_FORMATS), case.py, schema.CaseSpec, backend/parts/misc.yaml, web project chooser.
OBJECTIVE: design a Eurorack module panel of any HP with library and catalogue parts behind it, and export it. Acceptance = plan M8 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Confirm A-5 (horizontal hole position and the 12 HP four-hole rule) against the Doepfer source in docs/eurorack.md; a contradiction is a material mismatch.
PLANNED STACK:
1. Panel case — `case.eurorack_panel: {hp, format, thickness, module_depth}`: single panel sheet, outline from panel_width and the row format, oval rail holes, from_lid standoffs with lengths in the cut list, depth error; 10- and 16-way power header parts; engraving works on the panel; correct the AMYboard panel holes to y = 3.0 / 125.5 with a src.note, and correct every document repeating 123.5 mm. Tests: tests/test_panel.py (layered scenes byte-identical).
2. Editor — "new Eurorack module panel" in the project chooser; web tests.
CONSTRAINTS: no frames or joints; layered scenes unchanged.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_panel.py -q` → 0 failures; `git grep -n "123\.5" -- docs backend` → empty output; `cd web; node --test tests/` → 0 failures; full backend suite → 0 failures.
CONTRACT: apply.
```

### M9 — Plate-box case style

```text
/goal Deliver milestone M9 (Plate-box case style) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M9; review Part 3 Style D (D1). Preconditions: M5 and M6 merged. Repo: case.py (outer_shape, openings, supports), scene.py, export.py, flat.py, joinery.py.
OBJECTIVE: build a parts scene as a box of jointed plates, with every existing check still applying. Acceptance = plan M9 rows (1)–(5).
RELEASE TRAIN: see header.
GATE: run it. List which case.py functions are reusable without layers; anything that must be duplicated is a design finding.
PLANNED STACK:
1. Box geometry — `case.style: box`; deck = panel plane with the faceplate cutouts; walls, floor; joints; box height from the hardware. Tests: tests/test_box.py (deck openings match the layered lid within 0.1 mm; joints validate; plates fit stock).
2. Walls and supports — per_connector / open_to_edge openings on the facing wall; from_lid / from_floor become holes plus standoff lengths in the cut list. Tests: every external per_connector port opens its wall.
3. Editor and export — case-style selector; exports through M5; layered scenes byte-identical.
CONSTRAINTS: no rack bays.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_box.py -q` → 0 failures (builds soundmachine-v0 with style: box in a temporary copy); `cd web; node --test tests/` → 0 failures; full backend suite → 0 failures.
CONTRACT: apply.
```

### M10 — Rack bays: hybrid cases

```text
/goal Deliver milestone M10 (Rack bays: hybrid cases) from DEVELOPMENT_PLAN.md as a reviewed stack merged into feat/catalog-models-eurorack.

CONTEXT: DEVELOPMENT_PLAN.md §6 M10, A-2, D-3, D-4; review Part 3 Style D (D2). Preconditions: M7 and M9 merged. Repo: rack.py (laser cheeks), the box builder, scene.py collision checks.
OBJECTIVE: a box case carries Eurorack bays beside Adafruit parts, with the module faces as the upper surface. Acceptance = plan M10 rows (1)–(6).
RELEASE TRAIN: see header.
GATE: run it. Re-read D-3 and A-2 against M7's rail datum before choosing coordinates.
PLANNED STACK:
1. Bay model — `racks: [{id, at, hp, rows, module_depth, module_panel_t}]`; deck top = rail front face + module_panel_t; deck aperture; module-space keepout in the collision checks; box depth grown to the deepest bay. Tests: tests/test_hybrid.py (deck height within 0.01 mm; a board inside the module space is an error).
2. Cheeks and fixings — M7 cheeks inside the box, jointed to deck and floor, carrying the rails with M5s; merged BOM and cut list. Tests: cheek M5s align with rail ends within 0.01 mm; joints validate; plates fit stock.
3. Editor — place and size a bay on the deck; web tests.
CONSTRAINTS: no tilted rows (D-4); no layered-stack hybrids; no bus-board or PSU parts.
VERIFICATION (must pass): `cd backend; python -m pytest tests/test_hybrid.py -q` → 0 failures; full backend suite → 0 failures. M10 runs last: after its GO and merge, NEXT STEPS step 2 runs the release-train verification (`cd backend; python -m pytest tests/ -q` and `cd web; node --test tests/` → 0 failures) on the integration branch.
CONTRACT: apply.
```
