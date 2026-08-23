# Overnight loop prompt — the instrument

You are building the software for the sound machine, working from
`instrument/PLAN.md`. That file is the authority: architecture, phases,
per-task **Done when** criteria, guardrails, and the list of things
deliberately not built tonight. Read it fully before the first action of
every iteration — it changes as you check things off.

Each iteration:

1. Read `instrument/PLAN.md` and `instrument/BLOCKERS.md` (if present).
2. Pick the **first unchecked item in phase order** (A → I). Do not skip
   ahead; later phases lean on earlier ones.
3. Implement it completely, with the tests its **Done when** names. Tests
   live in `instrument/tests/`, run with
   `.venv/Scripts/python.exe -m pytest instrument/tests -q`.
4. When green: mark the item `[x]` in PLAN.md with one line of what proved
   it, and commit — code, tests and the PLAN.md tick in one commit, message
   in this repo's style (what and why, honest about anything guessed).
5. If stuck on the same item for a second iteration: write what you tried
   into `instrument/BLOCKERS.md`, mark the item `[!]` in PLAN.md, move to
   the next item. Never rabbit-hole.

Hard rules (also in PLAN.md, repeated because they matter):
- Never modify `backend/`, `web/`, `tools/`, `vendor/`, or
  `instrument/scripts/gridcomposer21.lua`.
- No hardware or audio access; no network beyond localhost; no pip
  installs beyond lupa / python-osc; nothing needing a compiler.
- The virtual grid and mock link are shipping simulators, not test props.
- If every item in a phase is `[x]` or `[!]`, continue to the next phase.
  If all phases are done, run the full case-tool suite once
  (`python -m pytest backend/tests -q` — it must still be green), write a
  short NIGHT-REPORT.md in `instrument/` summarising what exists, what is
  blocked and what to verify on hardware, then output only the word DONE.
