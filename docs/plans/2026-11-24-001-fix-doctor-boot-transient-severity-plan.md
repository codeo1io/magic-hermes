---
title: "fix: Doctor boot-transient severity lane with store-liveness corroboration"
type: fix
status: active
date: 2026-11-24
origin: "conductor spool: b670599e332f4209a50e5e6a5dec750d-brainstorm-magic-context-doctor-boot-flap.md (untracked in this repo; spool-only per swept-worktree precedent)"
target_repo: magic-hermes
base: bfeb007 (v0.3.63)
finding: d59758598379e27cadcaa817 (phase1.context.operational)
---

# fix: Doctor boot-transient severity lane with store-liveness corroboration

## Overview

`magic-hermes doctor` currently renders a FAIL row whenever its fresh sidecar boot dies, even when the death is the proven boot-window thread-creation EAGAIN class and the shared store is demonstrably healthy — which maestro's evaluator (doctor_ok = rc==0 AND "FAIL 0") turns into a HIGH finding and a repair lane, repeatedly. This plan adds a typed WARN lane to `run_doctor`: sidecar failures matching the boot-transient family render WARN **only when read-only store liveness corroborates health**; everything else renders FAIL exactly as today.

## Problem Frame

Every observed episode of finding d59758598379 pins to the doctor probe alone (historian streak 0, dreams/tasks empty). The crash class has two verified shapes: Node-side `Runtime exited during hello with status -6` (uv_thread_create SIGABRT) and Python-side `can't start new thread` / `Could not start Magic Context runtime: [Errno 11] Resource temporarily unavailable`; the existing 15 s fast-fail retry fails again under sustained pressure. Episodes self-clear in minutes while the store stays healthy. Nine lineage runs have landed zero bytes; landing this severity lane breaks the dispatch-storm loop (each repair lane consumes the same host resources that cause the crash). See origin brainstorm for full evidence.

## Requirements Trace

- R0. Fix root cause in magic-hermes; no maestro workaround.
- R1. Preserve swim lanes: no guard DDL changes, no upstream pin moves, evaluator untouched, doctor stays read-only.
- R2. Normal CE/conductor workflow (branch, gates, review).
- R3. Regression tests reproducing the failure class (both shapes; WARN and FAIL arms).
- R4. Targeted + appropriate full validation before landing.
- R5. Lineage duty: nothing to adopt (verified again 2026-11-24: no attempts rows beyond the three banked brainstorms, zero code deltas on any predecessor branch; historical implement/landed runs b794c1c/54420fda/ef3d3783/427325d2 addressed *different* shapes — historian guard, degraded-scan WARN, bind-contention WARN, lane-skew typed verdict — none touched the boot-EAGAIN family).
- R6. Commit and push through normal workflow.
- R7. Structural prevention preferred: spawn-cap hardening stays an optional gated rider (deferred); this lane is the only owner-repo remedy that lands inside the observation window.

## Scope Boundaries

- No changes to wall-budget constants (`_DOCTOR_WALL_BUDGET_S`, `_SIDECAR_FAST_FAIL_S`, `_SIDECAR_RETRY_BACKOFF_S`, `_DOCTOR_BIND_WAIT_MARGIN_S`) or their tests.
- No changes to the lane-skew, degraded-scan, or bind-contention lanes' verdicts or wording.
- No retry/budget widening (rejected: maestro's 90 s single-shot observation defeats any retry ladder).
- No evaluator-side tolerance (prohibited by R0).
- No host/estate capacity work (estate-side future work).
- No new doctor state; doctor remains stateless and read-only.

### Deferred to Separate Tasks

- Spawn-level resource caps (UV_THREADPOOL_SIZE etc. at the `RuntimeClient` injection point, `src/magic_hermes/cli.py` ~:816): optional U2 rider, only if a future plan verifies a supported, low-risk cap for the pinned node version. Not in this plan.
- Estate-side host memory/swap capacity policy: operator-plane future work.
- Upstream report of the boot-under-pressure crash class to @cortexkit/pi-magic-context: after this fix validates.

## Context & Research

### Relevant Code and Patterns

- **Classification-helper pattern**: `_shared_store_lane_skew` (`src/magic_hermes/cli.py` ~:461) — compiled regex, searches every accumulated failure text then the retry-pair joined text (with the per-text wrapper stripped), returns None (fail-open to the generic FAIL) on no match. The new classifier mirrors this exactly.
- **Read-only store-probe pattern**: `partial_coverage_success_rows_24h` (`src/magic_hermes/cli.py` ~:420) — `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`, ms-epoch `created_at` predicates, swallow `sqlite3.Error` → None. The new liveness probe mirrors this, with None meaning "not corroborated" (fail-closed for a gate).
- **Guard status**: `guard_status(db_path)` from `src/magic_hermes/historian_guard.py` — `.error`, `.present`, `.matches`, `.marker_rows_24h`; already a WARN-only doctor lane (~:649).
- **Insertion point**: the `not sidecar_healthy` block in `run_doctor` (`src/magic_hermes/cli.py` ~:855–925): `skew` check first (a fence refusal is never a boot transient), then the new transient lane, then the existing pair/single generic FAIL rendering (which becomes the non-corroborated arm, unchanged).
- **Exact crash texts** (pinned from `src/magic_hermes/runtime.py`):
  - `:659` → `Runtime exited during {method} with status {code}; request was not replayed; stderr: …` — the -6 shape is `during hello with status -` (negative code = signal death before first response).
  - `:647` → `Runtime timed out after {n}s during {method}; request was not replayed` — hello-timeout shape keys on `during hello; request was not replayed`.
  - `:458` → `Could not start Magic Context runtime: {exc}` — spawn OSError; `[Errno 11] Resource temporarily unavailable` observed 2026-11-20.
  - raw `can't start new thread` — thread-start failure observed 2026-11-20 as the first attempt's text.
- **Evaluator contract**: WARN rows keep rc==0 and "FAIL 0" in stdout (established doctrine — wall-budget and bind-contention lanes; verified live 2026-11-20: `Summary: PASS 10 / WARN 2 / FAIL 0`, rc 0, evaluator PASS).

### Live-data pins (2026-11-24, read-only queries against the shared store)

- Store: `~/.local/share/cortexkit/magic-context/context.db` (via `shared_db_path()`; `MAGIC_CONTEXT_DB_PATH` override respected).
- historian_runs freshness: most recent row (noop) 44.3 h old; last success 76.3 h; status counts last 7 d: 158 noop + 14 success; last `failed` row **694.6 h** ago. 14-day inter-row gap distribution: p50 36 s, p95 3.17 h, **max 63.4 h** — historian-only recency cannot corroborate a host that legitimately idles for days.
- **Multi-table liveness is the fix**: at the same moment historian sat 44 h idle, `source_contents` wrote 0.00 h ago, `message_history_index` 0.04 h, `session_projects` 0.03 h, `memories` 0.16 h, `notes` 1.4 h. Sessions write the store continuously; a genuine outage stops all of it.
- Guard: active in every observed episode (persistent trigger since 2026-09-21).

### Test anchors (verified at bfeb007)

- `tests/test_doctor_contract.py` — :331 `test_no_fail_message_contains_fail_zero` (the evaluator contract); :446+ constants/WARN verdict tests; fixture style `_run_doctor(tmp_path, monkeypatch, isolated_home, report=…, error=…)` with injected report rows/fake failures.
- `tests/test_cli.py` — :1224–1268 budget/WARN tests (budget-scaling invariants that must keep passing untouched).

## Prior-Art Survey

```json
{
  "schema_version": 2,
  "verdict": "extend",
  "scope": "src/magic_hermes (doctor verdict lanes + store probes), tests/test_doctor_contract.py, tests/test_cli.py",
  "freshness": {
    "vcs_reference": "bfeb007 (v0.3.63)",
    "scope_baseline": "run_doctor not-sidecar_healthy block and typed-lane helpers as of bfeb007"
  },
  "budget": {
    "max_search_passes": 2,
    "max_candidate_inspections": 6,
    "exhausted": false
  },
  "candidates": [
    {
      "path_or_symbol": "src/magic_hermes/cli.py _shared_store_lane_skew + lane-skew FAIL lane",
      "description": "typed verdict for storage-fence refusal; classification-helper and lane-placement template. No existing lane covers the boot-window EAGAIN family — every WARN lane keys on a different failure class (budget, contention, degraded scan), so extension via a new typed lane is the established pattern, not duplication",
      "disposition": "extend"
    },
    {
      "path_or_symbol": "src/magic_hermes/cli.py wall-budget WARN lane",
      "description": "budget-degradation WARN precedent under the same evaluator contract",
      "disposition": "insufficient"
    },
    {
      "path_or_symbol": "src/magic_hermes/cli.py bind-contention WARN lane",
      "description": "honest-degradation WARN precedent; contention class, not boot EAGAIN",
      "disposition": "insufficient"
    },
    {
      "path_or_symbol": "src/magic_hermes/cli.py partial_coverage_success_rows_24h",
      "description": "read-only mode=ro store-probe template with ms-epoch predicates",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "src/magic_hermes/historian_guard.py guard_status",
      "description": "guard presence/drift health signal; corroboration component",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "src/magic_hermes/runtime.py RuntimeProtocolError texts",
      "description": "authoritative crash-text source for the classifier predicates",
      "disposition": "reuse"
    }
  ]
}
```

## Key Technical Decisions

- **Classify-then-corroborate, never classify-alone**: an exact-string match alone must never flip FAIL→WARN; every WARN additionally requires the full corroboration gate. Teeth: any gate miss renders today's FAIL verbatim.
- **Multi-table liveness over historian-only**: live data shows historian can idle 63+ h while the store is actively written (source_contents/message_history_index/session_projects/memories/notes). Corroboration liveness = freshest write across a composite activity-table set within 24 h, historian_runs folded into the set.
- **Windows pinned from live data**: liveness window 24 h (any active host corroborates; a host dormant ≥24 h on *all* activity tables is functionally down → teeth return); historian-failed-row window 72 h (last failed row is 694 h old; a fresh failed historian row is a real health signal that must keep the verdict FAIL).
- **Fail-closed gate semantics**: a store-probe error inside the corroboration gate means "not corroborated" (→ FAIL), unlike the INFO-lane helpers' fail-open None. A gate that cannot see must not absolve.
- **Exact-substring classifier with retry-pair joining**: mirrors `_shared_store_lane_skew` (per-text search, then wrapper-stripped pair join). Scope guard is in the strings themselves (`during hello` only — bind/scan/post-hello failures and protocol errors are excluded); negative codes only for the exit shape (positive exit codes at hello are not signal-death).
- **Lane placement**: after the skew check, before the generic FAIL pair/single rendering; non-corroborated transient falls through to the unchanged generic FAIL rows so today's verdict text survives byte-for-byte.
- **Auditable WARN**: the row names the matched shape and each corroboration component with ages (e.g., "liveness: message_history_index 0.1h / historian noop 44.3h; no failed historian rows in 72h; guard active").
- **Constants as module-level floats** (`_DOCTOR_BOOT_TRANSIENT_LIVENESS_S = 24*3600.0`, `_DOCTOR_BOOT_TRANSIENT_FAILED_ROW_S = 72*3600.0`) so tests can pin them like `_SIDECAR_FAST_FAIL_S`.

## Open Questions

### Resolved During Planning

- Predicate strings → the four runtime.py-sourced families above (pinned from source + two observed episodes).
- Corroboration signal → multi-table liveness (24 h) + no historian failed row (72 h) + guard present/matching + store/schema readable.
- Insertion point → `not sidecar_healthy`, post-skew, pre-generic-FAIL.
- Historian-only corroboration → rejected (63.4 h observed idle gap would leave the fix inert exactly when needed).
- Corroboration on `db_path is None` → FAIL (no store, no liveness evidence).

### Deferred to Implementation

- Final helper names/docstrings (directional: `_sidecar_boot_transient(failures) -> str | None`, `_boot_transient_corroboration(db_path) -> tuple[bool, str]`).
- Exact activity-table list finalization (start: historian_runs, source_contents, message_history_index, session_projects, memories, notes, compartments; trim any table whose timestamp column is absent at the pinned schema lane — query defensively and skip absent columns).
- WARN row final wording (must name shape + components; keep "FAIL 0"-safe).

## Implementation Units

- [ ] **Unit 0: Re-materialize worktree and place this plan in-tree**

**Goal:** Restore the swept run worktree at base bfeb007 and persist the plan document.

**Requirements:** R2, R5.

**Dependencies:** None.

**Files:**
- Create: `docs/plans/2026-11-24-001-fix-doctor-boot-transient-severity-plan.md` (this document, copied verbatim from spool `1974d3889af2448e9e300eadb93f1440-plan-doctor-boot-transient-severity.md`).

**Approach:**
- Worktree was swept between 2026-11-20 and 2026-11-24 (dir empty, no `.git`; git resolves to a foreign repo — never run git from the anchored dir). Re-create the managed worktree from canonical `/work/projects/magic-hermes` at bfeb007, verify identity (branch `conductor/run-5cd6764203ae`, HEAD bfeb007), then place the plan doc before any code change.

**Verification:**
- `git -C <worktree> rev-parse HEAD` == bfeb007; plan doc present in-tree; `git status` clean except the new doc.

- [ ] **Unit 1: Boot-transient classifier**

**Goal:** Typed recognition of the boot-resource-exhaustion family across both observed shapes and the retry-pair join.

**Requirements:** R0, R3.

**Dependencies:** Unit 0.

**Files:**
- Modify: `src/magic_hermes/cli.py` (classifier helper + shape table near `_shared_store_lane_skew`).
- Test: `tests/test_doctor_contract.py` (classifier unit tests).

**Approach:**
- Module-level tuple of `(shape_label, (substring, …))` pairs: hello signal-death (`during hello with status -`), hello timeout (`during hello; request was not replayed`), thread-starvation (`can't start new thread`), spawn-EAGAIN (`Resource temporarily unavailable`, `Errno 11`). Per-text search then wrapper-stripped joined-pair search, mirroring `_shared_store_lane_skew`. No match → None → caller keeps today's FAIL.
- Negative controls stay negative: bind texts, fence-refusal text, `Runtime returned invalid JSON during hello`, generic DB-health errors.

**Patterns to follow:** `_shared_store_lane_skew` (structure, docstring contract, fail-open-to-generic semantics).

**Test scenarios:**
- Happy path: each of the five pinned strings (as they appear in a sidecar_failures entry, prefixed `Magic Context sidecar failed: `) → matched shape label.
- Edge case: failure text split across the fast-fail pair (first attempt tail cut, retry completing the sentence) → matched via joined text.
- Edge case: positive exit code at hello (`during hello with status 1`) → None.
- Error path: fence-refusal sentence, `during bind with status 1`, `invalid JSON during hello`, DB-health text → None (skew lane and generic FAIL keep ownership).

**Verification:** classifier unit tests green; existing lane tests untouched and green.

- [ ] **Unit 2: Store-liveness corroboration gate**

**Goal:** Read-only, fail-closed evidence that the subsystem is healthy despite the boot failure.

**Requirements:** R0, R1, R3.

**Dependencies:** Unit 0.

**Files:**
- Modify: `src/magic_hermes/cli.py` (corroboration helper + the two window constants, near `partial_coverage_success_rows_24h`).
- Test: `tests/test_doctor_contract.py` (corroboration unit tests against a tmp store).

**Approach:**
- One helper returns `(ok, evidence)`; every component runs `mode=ro`:
  1. store opens + `max(version) from schema_migrations` readable;
  2. `migration_pending` table queryable (empty = pending 0; absent table counts as readable/0);
  3. liveness: max `created_at`/`updated_at` across the activity-table composite (incl. historian_runs) within `_DOCTOR_BOOT_TRANSIENT_LIVENESS_S` (24 h); defensive per-table/column skip on `sqlite3.Error`, but if *every* component errors → not corroborated;
  4. no `historian_runs` row with `status='failed'` within `_DOCTOR_BOOT_TRANSIENT_FAILED_ROW_S` (72 h);
  5. `guard_status`: no error, present, matches.
- `db_path is None` → `(False, "no shared store")`. Evidence string lists each component with computed ages for the WARN row.

**Patterns to follow:** `partial_coverage_success_rows_24h` (connection style, epoch math); `guard_status` consumption in the guard lane.

**Test scenarios:**
- Happy path: tmp store with fresh historian noop row + guard trigger applied → ok, evidence names ages.
- Happy path: historian idle but `source_contents`/`message_history_index` fresh (the observed live shape) → ok.
- Edge case: all rows stale > 24 h on every activity table → not ok.
- Edge case: fresh `failed` historian row 1 h old → not ok even with fresh liveness.
- Error path: guard absent → not ok; store file absent → not ok; locked/corrupt store (monkeypatch connect to raise) → not ok (fail-closed).
- Edge case: activity table missing entirely from a lower-lane store → skipped, gate still computable from remaining tables.

**Verification:** gate unit tests green; no test writes to the real store (all fixtures on tmp_path stores).

- [ ] **Unit 3: Wire the WARN lane into `run_doctor`**

**Goal:** The verdict flip — WARN when transient+corroborated, byte-identical FAIL otherwise.

**Requirements:** R0, R1, R3.

**Dependencies:** Units 1–2.

**Files:**
- Modify: `src/magic_hermes/cli.py` (`not sidecar_healthy` block).
- Test: `tests/test_doctor_contract.py` (verdict matrix), `tests/test_cli.py` (budget invariants must pass unchanged).

**Approach:**
- Order in the block: `skew` → transient-classified? → corroborated? → `report.add("WARN", …)` naming shape + evidence; non-corroborated or unclassified falls through to the existing pair/single FAIL rendering unchanged. WARN wording keeps "FAIL 0" contract (no FAIL row emitted).
- No changes to attempt loop, budgets, or retry logic.

**Patterns to follow:** the degraded-scan WARN row (reason + remedy + evidence in one row).

**Test scenarios:**
- Happy path: shape-2 pair (`can't start new thread` + `Could not start Magic Context runtime: [Errno 11]…`) + corroborated tmp store → exactly one WARN row naming the shape, rc 0, stdout contains "FAIL 0".
- Happy path: shape-1 single (`Runtime exited during hello with status -6 … stderr: uv_thread_create …`) + corroborated store → WARN, rc 0, "FAIL 0".
- Error path: same shapes + stale store → the existing generic pair/single FAIL rows verbatim, rc 1.
- Error path: same shapes + fresh failed historian row → FAIL, rc 1.
- Integration: skew text + transient text both present → skew FAIL wins (lane order).
- Integration: full `_run_doctor` pass with no sidecar failure → unchanged summary lines (no new rows in healthy runs).
- Regression: `test_constants_are_positive_floats_within_budget`, `test_wall_budget_fits_observer_shot`, `test_no_fail_message_contains_fail_zero` all green untouched.

**Verification:** verdict-matrix tests green; `magic-hermes doctor` in the worktree against the real store renders PASS/WARN rows only (manual smoke, read-only).

- [ ] **Unit 4: Docs, validation battery, landing checklist**

**Goal:** Ship-ready validation plus the install-convergence sequence that makes the landed fix effective for the evaluator, including the 0.3.62 drift-row reconciliation.

**Requirements:** R4, R6.

**Dependencies:** Unit 3.

**Files:**
- Modify: `README.md` (doctor section: one paragraph on the boot-transient WARN lane and its corroboration evidence).
- Test: full-battery execution per feasibility.

**Approach:**
- Targeted battery first: `tests/test_doctor_contract.py` + `tests/test_cli.py` budget/WARN subset.
- Full battery per R4 as feasible (known test_cli.py watcher hazard under fleet load may constrain — record outcome either way).
- Landing checklist (each step observed, not assumed): land on branch → release bump (next patch version) → `magic-hermes install` → verify `__version__` of the binary the evaluator probes (`/home/agent/.hermes/hermes-agent/venv/bin/magic-hermes`) → observe one PASS evaluation. Reconcile the doctor's "magic-hermes 0.3.62 is installed" row first (one read-only inspection: which copy/distribution it reads vs the venv's 0.3.63) so the checklist verifies the right binary; record the finding in the fold notes — fix only if trivial, else file as follow-up.
- Fold discipline per KTD13/#20812: checkpoint commit + run-worktree mirror + changed_surfaces attestation (including this plan doc) at every fold return.

**Test scenarios:**
- Test expectation: docs-only changes carry no new tests; validation outcomes are recorded evidence, not new assertions.

**Verification:** targeted battery green; full-battery outcome recorded; landing checklist steps executed with observed outputs; three subsequent PASS evaluations with zero recurrence close the finding (owner-gated, outside this plan's claim).

## System-Wide Impact

- **Evaluator contract**: unchanged — WARN rows preserve rc==0 + "FAIL 0"; maestro untouched (R0/R1).
- **Other doctor consumers**: human/agent operators see a new WARN row with named evidence during pressure episodes; healthy-run output unchanged (no new rows).
- **Store access**: strictly more read-only `mode=ro` connections during doctor runs; no writes anywhere.
- **Unchanged invariants**: wall budgets, fast-fail retry, lane-skew/degraded-scan/bind-contention lanes, guard DDL, upstream pin — all byte-identical.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Classifier too broad masks a real persistent outage (e.g., permanent ulimit misconfig producing identical EAGAIN text forever) | Corroboration gate + 24 h liveness window: a host where all activity tables go stale ≥24 h reverts to FAIL; WARN row names evidence so operators see the age |
| Classifier too narrow leaves flap unfixed | Four families pinned from runtime.py source + two observed episodes; joined-pair matching; unknown future shapes fail open to today's FAIL (no regression, flap continues — reportable, not masked) |
| Liveness tables are feature-dependent (a host doing only historian work) | Composite set includes historian_runs; the failure mode is "gate says not corroborated" → today's FAIL, never a wrong PASS |
| Doctor now issues more store queries on the failure path only | Gate runs only when a transient shape matched (rare, boot-failure path); queries are `mode=ro`, bounded, sub-ms |
| 24 h blind window on a genuinely dead-but-idle host | Accepted deliberately: the alternative (FAIL on idle hosts) is the flap being fixed; evaluator's 3-PASS closure guard is unaffected since episodes end in PASS |
| Lane mortality before landing (9 runs, 0 bytes) | This plan is self-contained: exact strings, windows, anchors, checklist; Unit 0 re-materializes the swept worktree first |

## Documentation / Operational Notes

- README doctor section gains one paragraph (Unit 4).
- Landing checklist includes install-convergence verification and the 0.3.62 drift-row reconciliation note.
- Closure contract (from the scope artifact): three subsequent PASS evaluations with zero recurrence — owner-gated, never claimed by the implementing lane.

## Sources & References

- **Origin document:** conductor spool `b670599e332f4209a50e5e6a5dec750d-brainstorm-magic-context-doctor-boot-flap.md` + `.approaches.md` (untracked in this repo; spool-only per swept-worktree precedent). Prior brainstorms `65ba76b4…` (2026-11-19) and `d7fe85fc…` (2026-11-20) — direction unchanged across three lanes.
- Scope artifact: maestro engineering-context `a1c62607f3141c1cc8fd25db.json` (requirements R0–R7).
- Related code: `src/magic_hermes/cli.py` (`run_doctor`, `_shared_store_lane_skew`, `partial_coverage_success_rows_24h`, guard lane), `src/magic_hermes/runtime.py` (:458/:647/:659), `src/magic_hermes/historian_guard.py`.
- Related findings: d59758598379 (this), c7d63424 (degraded-scan/bind-contention/lane-skew precedents, landed).
