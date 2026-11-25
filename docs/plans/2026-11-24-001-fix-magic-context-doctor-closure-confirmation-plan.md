---
title: "fix: Magic Context operational doctor finding c7d63424 — zero-delta closure confirmation + terminalization (structural contingency preserved)"
type: fix
status: active
date: 2026-11-24
origin: conductor-delegate-spool-maestro/delegate/b945e7de65e74f61bda17eccbe976f72-brainstorm-magic-context-doctor-flap.md  # spool-only artifact, untracked in this repository (run worktree was swept before plan phase)
---

# fix: Magic Context operational doctor finding c7d63424 — zero-delta closure confirmation + terminalization (structural contingency preserved)

**Target repo:** magic-hermes (canonical `master` tip `bfeb007`, v0.3.63 — unchanged since 2026-11-19; run base `bfeb007`).

## Overview

The run exists to remediate finding `c7d63424daa3e45b9b1e15be` (contract `phase2.magic-context.operational`, CRITICAL): the installed `magic-hermes doctor` probe failing under host memory/swap pressure. The brainstorm phase (2026-11-20) produced a verified mechanism and a recommended structural fix. **On 2026-11-24T01:45:17Z the finding was CLOSED by the independent verifier** (same closure sweep as the phase-1 sibling `d5975859`, closed 01:48:10Z) after a long PASS streak that continues through 05:06Z today. The run is therefore a **zombie-successor**: its driver closed while it was in flight.

Per fleet doctrine for exactly this state (precedent: hermes-roadmap run `6b9bebab2c78`, finding `8ff30a72638c` closed 01:45:08Z in the same sweep → zero-delta closure-confirmation plan), this plan makes the **primary path zero-delta**: confirm the closure with independently re-derivable evidence, route the residuals, declare `changed_surfaces=[]`, and terminalize early. The brainstorm's recommended structural approach (bound the doctor end-to-end + liveness-corroborated boot-transient severity) is **preserved in full as a gated contingency unit (U4)** with complete design, so a future lane can execute it without re-research if the finding re-opens or review refuses the zero-delta shape.

Nothing about the closure refutes the brainstorm's mechanism: `master` still carries zero bytes for either failure class (11× rc=1 rendered boot-crash FAILs, 3× rc=124 no-stdout hang FAILs observed 2026-11-19/20). The closure reflects the pressure episode subsiding, not a fix. The structural work is deferred, not dismissed — it activates on re-open.

## Problem Frame

- **Driver (scope artifact `2cde0915062da43e85f28e81.json`):** fix root cause in magic-hermes only (no Maestro workaround), preserve swim lanes, add regression tests, validate, inspect lineage and adopt predecessor candidates, land + push, prefer structural prevention.
- **What the brainstorm verified (2026-11-20):** maestro's evaluator (`maestro/src/maestro/context.py`) runs the installed doctor at 90 s, retries once at 240 s only on rc=124, and passes iff rc==0 and `FAIL 0` in stdout. Observations split 26 PASS / 11 rc=1 (rendered `status -6` sidecar boot crash + 60 s hello-timeout retry, sessions healthy throughout) / 3 rc=124 (empty stdout, Python `BlockingIOError: [Errno 11]` stderr — the doctor outlived both evaluator budgets, so its own 75 s wall budget does not bound every path). Lineage: all branches 0-ahead of `bfeb007` except `conductor/run-22fff19a7c24` (7-commit watcher-healer batch for the wiring-clobber sibling class; failed three review cycles with 3 must-fix P2s open; salvage disclaims validation; host timer disabled).
- **What changed since (2026-11-21 → 11-24):** the flap stopped — every observation since 11-21 is PASS (rc=0); finding state `CLOSED` at 01:45:17Z; 12/12 sampled evaluations PASS through 05:06Z. The run worktree was swept (empty dir, no `.git`, reaped 03:48Z) — same pattern as runs `54eb1f710c0d`, `fec6831bc3e4`, `55e573cae52b`.
- **Lane mortality context:** 11 terminal runs in this finding's lineage with zero landed bytes; attempt 1 of this run reaped at ~59 min. The plan is shaped to land its (zero) delta and terminalize with minimal execution surface.

## Requirements Trace

- R1. Closure confirmation is the only gate: `findings.state == 'CLOSED'` for `c7d63424daa3e45b9b1e15be` (contract `phase2.magic-context.operational`), re-derived from maestro's own stores at execution time; all other arms (PASS streak, observation signatures, sibling-closure, lineage sweep) are recorded as **context rows, not gates**.
- R2. Zero repository delta on the primary path: no changes to `src/`, `tests/`, `scripts/`, or release metadata; fold declares cumulative `changed_surfaces=[]`; the plan document itself lands in `docs/plans/` only (documentation, not a changed surface in the engine's sense unless the engine counts it — declare it if so, never silently).
- R3. Terminalize early: evidence pack + routing records + zero-delta fold; no implementation units beyond U0–U3 execute unless a contingency trigger fires.
- R4. Preserve the structural design: the brainstorm's recommended approach is carried in this plan as gated Unit 4 with files, approach, patterns, and enumerated test scenarios, plus the planning-time pin duties it still owes.
- R5. Swim lanes preserved: no Maestro edits, no evaluator changes, no historian_guard changes, no upstream package pin moves, no arming of the disabled `magic-hermes-converge.timer`.
- R6. Worktree identity restored before any execution: re-materialize at `bfeb007`, verify `.git` link + `HEAD` + clean tree, confirm no lineage branch has gained bytes since the brainstorm's sweep.
- R7. Residuals routed with evidence pointers: structural design → re-open trigger; watcher-healer → dedicated lane with its cycle-3 fix-list; host capacity → ops-plane note.

## Scope Boundaries

- No change to the doctor, runtime, wiring, release scripts, or tests on the primary path.
- No adoption of `conductor/run-22fff19a7c24` (watcher healer) or `conductor/run-c2ff2a4a2303` (11-21 salvage: README + `scripts/release.py` + docs/release tests — a different concern, not this finding's lineage; preserved on its branch).
- No Maestro-side tolerance, no host/ops tuning, no evaluator contract change.
- No claim of fix: the closure is confirmed as observed, not attributed to this run.

### Deferred to Separate Tasks

- Structural bounded-doctor + severity fix (Unit 4): activates on finding re-open, review refusal of the zero-delta shape, or owner direction; otherwise a future finding-triggered lane executes it from this document.
- Watcher-healer landing: dedicated lane using `conductor/run-22fff19a7c24` + cycle-3 fix-list (`conductor-delegate-spool/c77fe7a9ae114c389ef989b815612d33-review/review-report.md`), then arm/verify the host timer.
- Ops-plane host memory/swap capacity policy (root enabler for every probe-based gate).

## Context & Research

### Relevant Code and Patterns

- Closure-confirmation pattern: zero-delta plan shape from run `6b9bebab2c78` (spool `5934c6e7…-plan-8ff30a72638c.md`) — evidence pack with context-rows-not-gates, routing records, `changed_surfaces=[]` fold, early envelope, contingency pivot only on re-open/refusal.
- Swept-worktree pattern: runs `54eb1f710c0d`, `fec6831bc3e4`, `55e573cae52b` — implement unit 0 re-materializes at the run base, verifies identity, places the spool-only plan doc under `docs/plans/`.
- Evidence stores (read-only at execution): maestro findings/evaluations/observations (`~/.local/state/maestro/maestro.db`), engineering-context (`~/.local/state/maestro/engineering-context/2cde0915062da43e85f28e81.json`), conductor gateway (`conductor-gateway/maestro-engineering.db`: runs, phase_results, review_findings).
- For Unit 4 (gated): `src/magic_hermes/cli.py` (`run_doctor`, wall budget `MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S` 75 s with env override, sidecar fast-fail constants, three WARN-only lanes: guard block, wall-budget, bind-contention with the "a WARN keeps both rc==0 and FAIL 0" doctrine), `src/magic_hermes/runtime.py` (`RuntimeClient.call` — `select` deadline guards reads only, stdin writes un-deadlined), `tests/test_cli.py` (budget scaling + wall-budget-edge WARN tests), `tests/test_doctor_contract.py` (`FAIL 0` contract tests).

### Institutional Learnings

- Zombie-successor doctrine (closure mid-run → zero-delta confirm + terminalize; structural work pivots in only on re-open or refusal).
- Lane-mortality doctrine: optimize for landing and timely delivery; focused test sets only (`hermetic_watcher` full-battery hazard).
- Install-convergence hazard: the evaluator runs the installed venv CLI; any repo-side fix is invisible until release + `magic-hermes install`/wiring refresh the observed binary (binding on Unit 4 when it activates).

## Prior-Art Survey

```json
{
  "schema_version": 2,
  "verdict": "reuse",
  "scope": "conductor spool plans for zombie-successor runs (hermes-roadmap run 6b9bebab2c78) and magic-hermes lineage branches conductor/run-22fff19a7c24, conductor/run-c2ff2a4a2303 vs base bfeb007",
  "freshness": {
    "vcs_reference": "bfeb007c871601ba1898bd569ae79519b7487d26",
    "scope_baseline": "spool plan 5934c6e7…-plan-8ff30a72638c.md (2026-11-24); brainstorm b945e7de… (2026-11-20); maestro findings state as of 2026-11-24T05:06Z"
  },
  "budget": {
    "max_search_passes": 2,
    "max_candidate_inspections": 4,
    "exhausted": false
  },
  "candidates": [
    {
      "path_or_symbol": "conductor-delegate-spool-maestro/5934c6e7…-plan-8ff30a72638c.md (run 6b9bebab2c78 zero-delta closure-confirmation plan)",
      "description": "Fleet-validated plan shape for a run whose finding closed mid-flight: evidence pack, routing records, empty changed_surfaces fold, contingency pivot",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "conductor/run-22fff19a7c24 @ d62fca9 (7 commits: converge verb, watcher_units, install/deploy arming)",
      "description": "Watcher-healer for the out-of-band wiring-clobber sibling class; three NEEDS_CHANGES review cycles, 3 must-fix P2s open, salvage disclaims validation",
      "disposition": "insufficient",
      "insufficiency_reason": "wrong failure class for this finding (wiring clobber, not doctor boot/hang), review-failed with must-fix findings open, and the run is zero-delta on its primary path"
    },
    {
      "path_or_symbol": "conductor/run-c2ff2a4a2303 @ f516210 (salvage: README.md, scripts/release.py, tests/test_docs_consistency.py, tests/test_release.py)",
      "description": "11-21 failed-implement salvage for a release/docs concern; not in this finding's generation_lineage",
      "disposition": "insufficient",
      "insufficiency_reason": "different concern (release/docs tests), no run row in this gateway, no doctor-related content"
    },
    {
      "path_or_symbol": "brainstorm b945e7de… Unit 1 design (bound-the-doctor + liveness-corroborated boot-transient severity) targeting src/magic_hermes/cli.py and src/magic_hermes/runtime.py",
      "description": "Recommended structural approach for both observed failure classes; unlanded, fully specified in the brainstorm and carried here as gated Unit 4",
      "disposition": "extend",
      "insufficiency_reason": "preserved, not executed: driver finding is CLOSED; activates on re-open or review refusal"
    }
  ]
}
```

## Key Technical Decisions

- **Zero-delta primary path over landing the structural fix now:** the finding is CLOSED with a verified PASS streak; closure needs no bytes, post-closure master mutation has no open mandate, review can legitimately reject it, and every extra unit multiplies lane-mortality risk in a lineage with zero landed bytes. The structural defect is real but currently unobservable (no pressure episode since 11-21) — it is preserved as Unit 4 with triggers, not abandoned.
- **Only gate is finding state:** PASS streaks, signature history, sibling closure, and lineage sweeps are evidence rows; gating on any of them would make a confirmation plan able to fail on healthy variation.
- **Contingency triggers are explicit and narrow:** Unit 4 executes only on (a) finding `c7d63424…` (or a successor instance on the same contract) returning to OPEN/FAIL during this run's life, (b) independent review refusing the zero-delta shape, or (c) owner direction. Otherwise Unit 4 is a hand-off document.
- **Worktree re-materialization precedes everything:** the run dir is empty; identity verification (`.git` link, `HEAD == bfeb007`, clean tree) is the first execution step; git run inside the empty dir resolves to a foreign repository and must not be trusted.
- **Spool is authoritative; repo gets the document copy:** the plan lives in the conductor delegate spool; the re-materialized worktree receives a copy under `docs/plans/` per repository convention.

## Open Questions

### Resolved During Planning

- Finding state at plan time: `CLOSED` at 2026-11-24T01:45:17Z; phase-1 sibling `d5975859…` closed 01:48:10Z; 12/12 sampled evaluations PASS through 05:06Z; observations all rc=0 since 11-21.
- Lineage bytes gained since the brainstorm: none for this finding — `conductor/run-c2ff2a4a2303` (+1 salvage, release/docs concern) is the only new branch and is out of lineage; `master` unchanged at `bfeb007`.
- Run base unchanged: `workspace_base_sha = bfeb007…` in the conductor's runs table.
- Plan-doc sequence: no `docs/plans/2026-11-24-*` exists in the canonical repo → `2026-11-24-001`.

### Deferred to Implementation

- Exact closure evaluation id and guard-accrual details for the evidence pack (re-derived from maestro stores at execution time; the brainstorm-era context file predates closure).
- Whether the engine counts `docs/plans/*.md` as a changed surface for fold attestation — declare whichever the engine's convention is, never omit silently (precedent: #18410 cumulative fold attestation).
- Unit 4 pin duties, if it activates: the rc=124 blocking point (candidates: pre-render import/module work under swap thrash; un-deadlined stdin write in `RuntimeClient.call`; `EAGAIN` escaping a worker thread; process-group reap) and the liveness recency window (store cadence data: last 50 `historian_runs` rows span 2026-11-19T16:04Z → 2026-11-22T09:44Z; zero rows 11-23/24 while closure evals passed — a traffic-based WARN predicate must stay silent on idle).

## Implementation Units

- [ ] **Unit 0: Re-materialize the run worktree and place this plan**

**Goal:** Restore a trustworthy working tree at the run base and land the plan document per repository convention.

**Requirements:** R6, R2 (documentation-only delta)

**Dependencies:** Conductor-managed worktree dir `conductor-worktrees/magic-hermes-31abeceaad/run-7e67635cad89-7e67635c` (currently empty; reaped 2026-11-24 03:48Z).

**Files:**
- Create: `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md` (copy of this document, frontmatter intact)
- Inspect (no modify): `.git` (worktree link), `src/magic_hermes/cli.py`, `pyproject.toml`

**Approach:**
- Re-materialize from the canonical repo at `bfeb007` on the run branch (`conductor/run-7e67635cad89`); verify the `.git` link points at the magic-hermes repository, `HEAD` equals the run base, `git describe` reports v0.3.63, and the tree is clean. Any git command that reports `native-rails-migration`, `item-47x`, or hermes-agent commits is resolving to the foreign agent-home repository — stop and fix the link.
- Re-run the lineage sweep (`rev-list --count bfeb007..<branch>` for every `conductor/run-*` touching this finding) and record results in the evidence pack; expected: all 0 except `22fff19a7c24` (7) and `c2ff2a4a2303` (1, out of lineage).
- Place the plan document; commit only if the conductor's work order asks for commits (default: do not commit or push unless asked).

**Patterns to follow:** Swept-worktree U0 in runs `54eb1f710c0d`, `fec6831bc3e4`, `55e573cae52b`.

**Test expectation:** none — worktree restoration and documentation placement carry no behavioral change.

**Verification:**
- `git -C <worktree> rev-parse HEAD` equals `bfeb007c871601ba1898bd569ae79519b7487d26`; `git status --porcelain` shows only the new plan document (if untracked) or nothing (if committed per work order).
- The plan document exists at the repo-relative path above and its frontmatter parses.

- [ ] **Unit 1: Closure-confirmation evidence pack**

**Goal:** Produce an independently re-derivable record that finding `c7d63424daa3e45b9b1e15be` is closed and why this run carries zero delta.

**Requirements:** R1, R7

**Dependencies:** Unit 0

**Files:**
- Create: `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-evidence.md` (or a spool-only evidence file if the conductor's work order forbids repo writes beyond the plan — follow the work order)

**Approach:**
- Re-derive at execution time from maestro stores (read-only): finding row (`state`, `last_seen_at`, `latest_evaluation_id`), the closure evaluation, the post-closure PASS streak (count and span), and the observation signature history (26 PASS / 11 rc=1 / 3 rc=124 over 2026-11-19→20, all PASS since 11-21). Record the phase-1 sibling `d5975859…` closure (01:48:10Z) as a context row.
- Record the brainstorm's verified mechanism and lineage dispositions as context (they explain why zero delta is honest: nothing landed, nothing to adopt, driver closed).
- Gate: exactly one — `findings.state == 'CLOSED'` for `c7d63424…`. If it is not CLOSED at execution time, stop and escalate to Unit 4's trigger evaluation instead of proceeding.

**Patterns to follow:** Evidence pack shape from run `6b9bebab2c78`'s plan (arms as context rows, not gates).

**Test expectation:** none — evidence collection only.

**Verification:**
- Evidence document exists and every row carries a store path + query or JSON pointer a reviewer can re-run; the single gate row reads CLOSED at execution time.

- [ ] **Unit 2: Routing records for residuals**

**Goal:** Ensure the preserved structural design, the watcher-healer candidate, and the ops-plane enabler each have an owner-facing destination.

**Requirements:** R4, R7

**Dependencies:** Unit 1

**Files:**
- Modify: `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md` (Unit 4 remains the structural hand-off; add a "Routing" subsection listing the three destinations with evidence pointers)

**Approach:**
- Structural fix → this document's Unit 4, trigger = finding re-open or owner direction.
- Watcher healer → dedicated lane entry ticket: branch `conductor/run-22fff19a7c24` + cycle-3 review report (3 must-fix P2s) + `batch-with-fixes.diff` sha256 `fc9ffe58…35764`; host timer `magic-hermes-converge.timer` stays disabled until that lane lands and verifies it.
- Host memory/swap capacity → ops-plane note (out of repository scope).
- Mirror the routing block into the plan phase_result's `provisional_future_work` so the conductor's ledger carries it.

**Test expectation:** none — documentation only.

**Verification:**
- Routing subsection lists all three residuals with concrete pointers; phase_result `provisional_future_work` matches it.

- [ ] **Unit 3: Zero-delta fold attestation and terminalization**

**Goal:** Close the run honestly with an empty cumulative changed-surface declaration.

**Requirements:** R2, R3

**Dependencies:** Unit 2

**Files:**
- No source changes. Deliverable is the fold/terminal envelope in the conductor spool.

**Approach:**
- Declare cumulative `changed_surfaces=[]` (or `["docs/plans/…"]` if the engine counts documentation — follow the engine's convention, precedent #18410 cumulative attestation).
- Emit the early terminal envelope: status succeeded with summary stating closure confirmed, zero delta, structural design preserved at Unit 4, residuals routed.
- Do not commit or push anything unless the work order explicitly asks.

**Test expectation:** none — attestation only.

**Verification:**
- Conductor ledger shows the fold with the declared changed surfaces and the terminal envelope; `git status` in the worktree shows no source changes.

- [ ] **Unit 4 (GATED — do not execute unless a trigger fires): Bounded doctor + liveness-corroborated boot-transient severity**

**Triggers:** finding `c7d63424…` (or a successor on `phase2.magic-context.operational` / `phase1.context.operational`) reopens during this run's life; independent review refuses the zero-delta shape; owner directs the structural fix. On a trigger, re-verify the base (master may have moved), re-run the lineage sweep, then execute 4a–4d.

**Goal:** Make `magic-hermes doctor` truthful and bounded under host pressure so the evaluator contract (`rc==0` + `FAIL 0`) holds during corroborated boot transients while genuine outages still FAIL.

**Requirements:** brainstorm R0–R9 (owner-repo only; evaluator contract preserved; genuine outages FAIL; doctor bounded end-to-end — rc 124 impossible; doctor read-only; regression tests for both classes; swim lanes; install convergence; wall-budget constants coherent)

**Dependencies:** Unit 0 identity checks; a reproduced or unit-equivalent hang for 4a's pin.

**Files:**
- Modify: `src/magic_hermes/cli.py` (`run_doctor`: wall-budget enforcement, `elif not sidecar_healthy:` block, typed budget-exhausted verdict)
- Modify: `src/magic_hermes/runtime.py` (`RuntimeClient.call` write path deadline; `_dispose` process-group reap)
- Test: `tests/test_cli.py`, `tests/test_doctor_contract.py`
- Modify (only if the entry point is where the watchdog installs): `src/magic_hermes/__main__.py` or the console-script entry

**Approach:**
- **4a Bound the doctor end-to-end.** Install an outermost hard bound at the CLI entry (a timer armed before any sidecar or store work, re-armed by the wall-budget derivation, with the existing `MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S` override driving it) whose expiry renders a typed `budget-exhausted` verdict row and exits by that row's severity — never silence, never rc 124. Deadline-guard the known un-deadlined spots: stdin writes in `RuntimeClient.call` (the `select` deadline covers reads only), and ensure `_dispose` terminates the whole sidecar process group so no child holds the evaluator's pipes open. Pin the real rc=124 blocking point first (candidates in Deferred to Implementation); the outer bound covers every candidate even before the precise one is fixed.
- **4b Boot-transient severity policy.** In the `not sidecar_healthy` block, classify boot-window transients (`status -6` / `uv_thread_create` crash, hello-timeout before first response, and 4a's budget-exhausted-during-boot) and render WARN instead of FAIL **only** when read-only in-store liveness evidence corroborates (recent `historian_runs` success/noop activity within a bounded window, mirroring the guard's trailing-window pattern). Lane-skew, post-hello, DB-health, and uncorroborated boot failures stay FAIL. Severity feeds the same typed row from 4a.
- **4c Regression tests for both classes** (focused sets only; full battery is the `hermetic_watcher` hazard). Use the env budget override with a fake runtime that blocks past the budget, so no wall-clock-heavy tests are needed.
- **4d Validation + landing.** Targeted tests, then the repository's standard validation; release + `magic-hermes install`/wiring refresh so the evaluator's installed binary changes (install convergence); version fence per repository doctrine.

**Execution note:** Start each sub-unit from a failing test that reproduces the observed signature (crash-then-hello-timeout pair; blocked-runtime budget exhaustion) before changing `run_doctor`.

**Technical design** *(directional guidance, not implementation specification)*:

```
doctor entry
  arm hard bound(budget + render reserve)  ──expiry──▶ render typed row(budget-exhausted, severity := corroborated? WARN : FAIL) → exit
  run_doctor
    sidecar boot ── crash(-6) / hello-timeout ──▶ boot-transient classified
    not sidecar_healthy:
      if boot-transient AND liveness_corroborated(store, window): WARN row  (rc 0, "FAIL 0")
      else:                                                               FAIL row  (rc 1)
    wall budget (existing 75 s path) → existing WARN lane unchanged
  exit per worst severity
```

**Patterns to follow:** the three existing WARN-only lanes in `src/magic_hermes/cli.py` (guard block with its "maestro requires doctor to exit 0" doctrine comment; wall-budget lane; bind-contention lane with "a WARN keeps both rc==0 and FAIL 0"); budget tests in `tests/test_cli.py`; `FAIL 0` contract tests in `tests/test_doctor_contract.py`.

**Test scenarios:**
- Happy path — crash (`status -6`) then hello-timeout with recent corroborating `historian_runs` rows → one WARN row naming the offline-audit remedy, rc 0, stdout contains `FAIL 0`.
- Happy path — budget exhausted during boot with corroboration → typed budget-exhausted WARN row rendered before the evaluator's 90 s, rc 0, `FAIL 0`.
- Error path — same crash pair with no recent store rows → FAIL row, rc 1 (outage keeps teeth on an idle store).
- Error path — budget exhausted without corroboration → typed FAIL row, rc 1, process exits inside the budget (never rc 124).
- Edge case — store rows exactly at the recency-window boundary → treated as uncorroborated (conservative side).
- Edge case — blocked sidecar stdin write with a stalled reader → write deadline fires → typed boot-transient path, not a hang.
- Edge case — sidecar child survives `_dispose` → process group terminated; no pipe held open after exit.
- Integration — fake runtime blocking past a 2 s env-overridden budget in a CLI-level test → typed row on stdout and exit within budget + reserve.
- Regression — lane-skew pair → still FAIL; post-hello failure → still FAIL; existing wall-budget-edge and bind-contention WARN verdict tests unchanged.

**Verification:**
- Both observed failure classes have a failing-then-passing regression test; no test in the focused set regresses; the doctor cannot exceed the configured budget in any test path; release + install refresh verified on the evaluator's binary path before declaring the contract effect.

## System-Wide Impact

- **Primary path (Units 0–3):** none beyond documentation; no runtime, evaluator, or wiring behavior changes; the disabled `magic-hermes-converge.timer` is left untouched.
- **Gated Unit 4 if activated:** doctor severity for corroborated boot transients (evaluator sees WARN instead of FAIL — intended); doctor becomes bounded (evaluator's rc-124 retry lane effectively never fires); no change to the evaluator, historian guard, wiring/lease semantics, or release pipeline semantics; install convergence is a mandatory landing step or the contract effect is nil.
- **Unchanged invariants:** evaluator contract (`rc==0` + literal `FAIL 0`), three existing WARN lanes, 75 s wall budget and render reserve, doctor read-only against the shared store, upstream package pins.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Finding re-opens under the next pressure episode after this run terminalizes | Unit 4 is the ready-to-execute design with triggers and pin duties; a successor finding lane starts from this document, not from scratch |
| Independent review refuses the zero-delta shape | Explicit trigger: switch to Unit 4 execution after re-verifying base and lineage |
| Executing git inside the swept worktree resolves to the foreign agent-home repository | Unit 0 identity checks gate every later unit; foreign markers (`native-rails-migration`, `item-47x`) are an abort signal |
| Engine counts the plan document as a changed surface while the fold declares `[]` | Follow the engine's convention explicitly (precedent #18410); never omit silently |
| Unit 4 activated later on a moved base | Re-verify `master`, re-sweep lineage, re-run gates invalidated by the new base before changing `run_doctor` |
| Unit 4's WARN predicate white-washes a real outage | Corroboration is required and conservative at the boundary; idle store → FAIL; lane-skew/post-hello never downgraded |

## Documentation / Operational Notes

- This plan is spool-authoritative until Unit 0 places it; the evidence pack and routing subsection are the only other documents produced on the primary path.
- Operational note for the owner: the structural defect remains on `master`; the closure reflects episode subsidence. The host converge timer remains disabled by design until the healer lane lands its must-fix items.

## Routing

Residuals of this run and where they go (mirrored into the conductor ledger's provisional future work):

- **Structural fix (gated Unit 4)** — stays with this run's plan as the trigger-gated contingency (finding re-open on `phase2.magic-context.operational` / `phase1.context.operational`, zero-delta fold refusal, or owner direction). A successor finding lane executes Units 4a–4d verbatim from this document; do not re-brainstorm the mechanism. Live specimen for 4a's regression design: observation `21efc00c28496fb8ff6b9b6a` (2026-11-24T19:17:31Z, rc=124, `TimeoutExpired` after 90 s, empty stdout) in the maestro observation store.
- **Watcher-healer candidate** — dedicated lane: branch `conductor/run-22fff19a7c24` (7 commits, salvage `d62fca9`) with its cycle-3 fix-list of 3 must-fix P2s (`conductor-delegate-spool/c77fe7a9ae114c389ef989b815612d33-review/review-report.md`; fix snapshot `batch-with-fixes.diff` sha256 `fc9ffe58c63e76047357323292ed16760a53a9e3a8f4c74421b57f2e88e35764`). The host `magic-hermes-converge.timer` stays **disabled** until that lane lands and verifies it.
- **Host memory/swap capacity** — ops-plane only (root enabler for every probe-based gate); out of repository scope per the run's swim-lane requirements.

## Sources & References

- **Origin document:** conductor delegate spool `b945e7de65e74f61bda17eccbe976f72-brainstorm-magic-context-doctor-flap.md` (untracked in this repository; also `6ec339ee…` copy) — the brainstorm this plan executes from.
- Scope artifact: `~/.local/state/maestro/engineering-context/2cde0915062da43e85f28e81.json` (finding, lineage, requirements).
- Finding state: `~/.local/state/maestro/maestro.db` — `findings` (`c7d63424…` CLOSED 2026-11-24T01:45:17Z; `d5975859…` CLOSED 01:48:10Z), `evaluations` (12/12 PASS through 05:06Z), `observations` (signature split 26/11/3 over 11-19/20; all PASS since 11-21).
- Precedent plan: spool `5934c6e7…-plan-8ff30a72638c.md` (run `6b9bebab2c78`, zero-delta closure-confirmation shape).
- Lineage: `conductor/run-22fff19a7c24` @ `d62fca9`; cycle-3 review `conductor-delegate-spool/c77fe7a9ae114c389ef989b815612d33-review/review-report.md`; `conductor/run-c2ff2a4a2303` @ `f516210` (out of lineage).
- Related code: `src/magic_hermes/cli.py`, `src/magic_hermes/runtime.py`, `tests/test_cli.py`, `tests/test_doctor_contract.py`; evaluator `/work/projects/maestro/src/maestro/context.py` (read-only reference; not modified).
