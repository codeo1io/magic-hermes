---
title: "fix: doctor boot-transient severity — liveness-corroborated WARN"
type: fix
status: active
date: 2026-11-24
origin: delegate/9a65492b7dd34319ab6ef865305ad102-brainstorm-magic-context-doctor-boot-flap.md  # spool artifact, untracked in this repo
---

# fix: doctor boot-transient severity — liveness-corroborated WARN

**Target repo:** magic-hermes (canonical checkout `/work/projects/magic-hermes`, master `bfeb007` v0.3.63). Run `a62c6b892d6649bba6c64d1e184ffb0f` · attempt `2ae0f5881e33427eb353f76aac427d09` · 2026-11-24.

## Overview

The doctor's fresh sidecar boots die under host memory/swap pressure (Node `uv_thread_create` SIGABRT at boot, then the single fast-fail retry times out during `hello`), minting exactly one FAIL row and rc=1. Maestro's evaluator (`phase1.context.operational`, finding `d59758598379e27cadcaa817`) requires rc==0 and literal `FAIL 0`, so every pressure-coincident evaluation flips the gate — the finding has flapped since 2026-09-21. This plan adds a boot-transient severity lane to `run_doctor`: when the failure pair is a boot-window transient AND read-only in-store liveness evidence corroborates that the subsystem is serving sessions, render a typed WARN (naming the offline-audit remedy) instead of FAIL — exactly the doctrine already shipped three times in this file (guard block, wall-budget, bind-contention lanes). Genuine outages (lane-skew, post-hello failures, DB-health errors, uncorroborated boot failures) keep FAIL.

## Problem Frame

Evaluator math (verified `maestro/src/maestro/context.py:167-235`): `failures = (0 if doctor_ok else 1) + historian_streak + dream_fail + task_fail`; the dream/task tables are empty and the streak is 0, so every "1 operational failures" episode is the doctor probe. Episodes are sustained (30+ min, 2026-11-19/20) and load-correlated; live sessions stay healthy throughout (historian success/noop rows minted during episodes). Three adjacent-class mitigations already on master — guard fix `12176ca` (PR #11), fast-fail/wall-budget (`c7d63424`), bind mutex `936753b` (PR #88) + deferred-bind handshake `0d1b33e` (PR #108) — do not classify this crash class; the flap persists, so severity/classification is the remaining lever. Live-store data note (2026-11-24T06:23Z, read-only): latest historian row ~45 h stale (quiet estate) while episode-time rows minted at minute cadence — load-correlated doctor failures coincide with recent rows, so a bounded positive-liveness window corroborates transients exactly when they happen.

## Requirements Trace

- R1: Corroborated boot-transients render WARN; evaluator contract preserved exactly (rc 0 + literal `FAIL 0` in stdout).
- R2: Outage teeth unchanged: lane-skew, post-hello failures, DB-health errors, and boot failures WITHOUT liveness corroboration stay FAIL.
- R3: Doctor stays read-only against the shared store (no new writes; guard precedent keeps writes in `wire`).
- R4: Regression tests reproduce the failure class at unit level; focused suites only (full-battery store/hermetic hazards).
- R5: Swim lanes preserved: no maestro edits, no historian-guard trigger changes, no upstream package pin moves; typed-row D5 single-row replacement discipline.
- R6: Land + push via the repo's normal workflow with install convergence (evaluator observes the INSTALLED CLI) and version-fence discipline.
- R7: Wall-budget discipline untouched: 75 s budget, render reserve, maestro's 90 s single-shot observer window keep holding.

## Scope Boundaries

- Non-goal: modifying the maestro evaluator or its contract (explicit prohibition).
- Non-goal: fixing host memory/swap pressure (ops context; recorded, not landable here).
- Non-goal: upstream (`@cortexkit/pi-magic-context`) changes; boot-cap spawn hardening (optional rider) is deferred, not part of this plan's active units.
- Non-goal: retry-count/constant changes (`_SIDECAR_FAST_FAIL_S`, wall budget stay as-is).

### Deferred to Separate Tasks

- Optional rider B: sidecar boot thread/memory caps at `RuntimeClient` spawn (needs node v22 flag verification; separate unit, droppable).
- Ops-plane capacity policy for the shared host (estate plane, not this repo).
- Upstream report of the boot-under-pressure crash class (after this fix validates).

## Context & Research

### Relevant Code and Patterns

- `src/magic_hermes/cli.py` — `run_doctor`: the `not sidecar_healthy` block renders the non-skew FAIL via the `len(sidecar_failures) == 2` "(fast-fail retry)" join or the single-failure branch; wall-budget WARN lane and bind-contention/degraded-scan WARN lanes sit in the same function with the "a WARN keeps both rc==0 and FAIL 0" doctrine comment; doctor constants at module top (`_SIDECAR_FAST_FAIL_S = 15.0`, wall-budget env `_DOCTOR_WALL_BUDGET_ENV` pattern `_DOCTOR_WALL_BUDGET_ENV`).
- `src/magic_hermes/historian_guard.py` — `guard_status()` is the read-only trailing-window store-query pattern (`_connect_ro`, ms-epoch `created_at` cutoff via `MARKER_WINDOW_MS`); `make_fixture_db()` builds production-shaped `historian_runs` fixtures for tests.
- `tests/test_doctor_contract.py` — contract tests to mirror: `test_no_fail_message_contains_fail_zero`, `test_constants_are_positive_floats_within_budget`, `test_wall_budget_fits_observer_shot`, `test_bind_contention_renders_honest_warn_verdict`.
- `tests/test_historian_guard.py` — guard unit-test home for the new liveness helper tests.
- `historian_runs` schema (live store, read-only PRAGMA): `id, session_id, harness, run_kind, status, failure_reason, ..., created_at` (ms epoch).
- Release path: `pyproject.toml` version + `scripts/release.py` (release commits follow `release: vN.N.N`); install convergence via `magic-hermes install` refreshing the venv wrapper the evaluator observes (`/home/agent/.hermes/hermes-agent/venv/bin/magic-hermes`).
- Version-fence tests (`tests/test_docs_consistency.py`) pin the upstream series/node-version — R5 forbids moving them.

### Institutional Learnings

- Delivery-within-budget is a hard lane constraint: two predecessor attempts died on dispatch-lease expiry/timeouts with good work unreconciled (brainstorm lane `cd5ed22e...` 2026-11-19; plan attempt `62169073820640458db7cd319910c70b` reaped 2026-11-24). Implement phases must bank artifacts early.
- The swept-worktree hazard: the run's anchored worktree had its `.git` resolve to a foreign repo; git operations must run only after U0 re-materialization gates HEAD = `bfeb007` clean.

## Prior-Art Survey

```json
{
  "schema_version": 2,
  "verdict": "extend",
  "scope": "src/magic_hermes (doctor severity lanes, read-only store helpers, contract tests)",
  "freshness": {"vcs_reference": "bfeb007 (v0.3.63, master tip, clean)"},
  "budget": {"max_search_passes": 1, "max_candidate_inspections": 1, "exhausted": false},
  "candidates": [
    {"path_or_symbol": "src/magic_hermes/cli.py run_doctor WARN lanes (guard block, wall-budget, bind-contention)", "description": "severity-degradation precedents that keep rc==0 + literal 'FAIL 0'", "disposition": "reuse"},
    {"path_or_symbol": "src/magic_hermes/historian_guard.py guard_status / _connect_ro", "description": "read-only trailing-window store query pattern to mirror for liveness", "disposition": "extend"},
    {"path_or_symbol": "src/magic_hermes/historian_guard.py make_fixture_db", "description": "production-shaped historian_runs fixture builder for unit tests", "disposition": "reuse"},
    {"path_or_symbol": "tests/test_doctor_contract.py", "description": "doctor contract suite (FAIL 0 invariant, constants bounds, WARN verdict tests)", "disposition": "extend"},
    {"path_or_symbol": "tests/test_historian_guard.py", "description": "guard unit-test module hosting liveness helper tests", "disposition": "extend"}
  ]
}
```

## Key Technical Decisions

- **KD1 — Classification predicate, conservative shape-match:** a failure list is a boot-transient iff every entry is a boot-window shape: (a) native-crash exit (`status -6` / `uv_thread_create` assertion text in the sidecar failure string), or (b) `Runtime timed out ... during hello` (hello-phase timeout before first response). Any post-hello/doctor-call/DB-health failure shape disqualifies. Lane-skew is checked FIRST and stays FAIL (R2).
- **KD2 — Corroboration is positive liveness, bounded and honest:** read-only query for rows in `historian_runs` with `created_at >= now - W`, counting `status IN ('success','noop')`; W defaults to 6 h as a module constant (rationale: episode-time cadence is minutes; 6 h tolerates quiet stretches inside busy days without letting day-old rows white-wash; current quiet-estate data shows ~45 h staleness would NOT corroborate — honest), env-overridable following the `_DOCTOR_WALL_BUDGET_ENV` precedent. Unreadable store ⇒ NOT corroborated (fail-visible, never white-wash).
- **KD3 — WARN row is a typed D5 single-row replacement:** one WARN row replacing the would-be FAIL row, quoting the underlying failure pair (same "(fast-fail retry)" quoting as the FAIL lane), naming the remedy (offline audit: `magic-hermes doctor --full-integrity`) and the ops context (host memory pressure), so humans see the transient plainly.
- **KD4 — Helper lives in `historian_guard.py`:** it owns `_connect_ro` and the trailing-window query idiom; `cli.py` imports it. No new module, no new state, no writes.
- **KD5 — No constant/contract changes:** `_SIDECAR_FAST_FAIL_S`, retry backoff, wall budget, render reserve untouched; only ONE new constant (+env override) added; R7 tests keep passing unmodified.
- **KD6 — Plan placement:** spool artifact is authoritative for this phase; U0 places the plan in-tree at `docs/plans/2026-11-24-001-fix-doctor-boot-transient-severity-plan.md` during re-materialization.

## Open Questions

### Resolved During Planning

- Insertion point: the non-skew branches of the `not sidecar_healthy` block (before rendering FAIL).
- Liveness signal: positive recent-row check (KD2), NOT absence-of-failures (avoids white-washing quiet outages).
- Helper location: `historian_guard.py` (KD4). Tests: `tests/test_historian_guard.py` + `tests/test_doctor_contract.py`.
- Release path: `scripts/release.py` bump to v0.3.64 + `magic-hermes install`; evaluator observes the installed wrapper.

### Deferred to Implementation

- Exact matcher strings for the native-crash shapes (pin against the next live episode capture; assertion text rendering may vary by node release).
- W calibration against a longer inter-row-gap distribution (if busy-day p95 gap exceeds 6 h, widen the default with evidence).
- Whether the WARN row also names the doctor-side env knob for operators (decide at wording time).

## Implementation Units

- [ ] **Unit 0: Re-materialize worktree + place plan in-tree**

**Goal:** Restore a valid magic-hermes worktree for the run (current anchored dir is swept; its `.git` resolves to a foreign repo) and land the plan document.

**Requirements:** R6

**Dependencies:** None

**Files:**
- Create: `docs/plans/2026-11-24-001-fix-doctor-boot-transient-severity-plan.md` (copy of this plan, sequence 001 verified free at execute time)

**Approach:** Re-materialize the conductor worktree from the existing run branch (or fresh branch from canonical master) WITHOUT `-b`-style re-creation drift; gate: `git -C <worktree> rev-parse HEAD` == `bfeb007...`, `git status` clean, repo is magic-hermes (remote/`pyproject` sanity). Never run git from the currently-swept anchored directory (foreign repo). Copy the spool plan into `docs/plans/` with the dated sequence.

**Test expectation: none — scaffolding.**

**Verification:** Worktree HEAD is `bfeb007`, clean; plan file present in-tree.

- [x] **Unit 1: Read-only liveness helper**

**Goal:** Provide the corroboration primitive the severity policy consumes.

**Requirements:** R3

**Dependencies:** Unit 0

**Files:**
- Modify: `src/magic_hermes/historian_guard.py`
- Test: `tests/test_historian_guard.py`

**Approach:** New `liveness_status(db_path, window_s) -> LivenessStatus` dataclass (`ok`, `recent_rows`, `latest_status`, `latest_row_age_s`, `error`); mirrors `guard_status`'s `_connect_ro` + ms-cutoff pattern; query counts recent `status IN ('success','noop')` rows and reads the latest row's age; any `sqlite3.Error` returns `error` set (never corroborates); pure read, no writes.

**Patterns to follow:** `guard_status()` (same module), `MARKER_WINDOW_MS` constant idiom.

**Test scenarios:**
- Happy path: fixture DB (via `make_fixture_db`) with a `success` row inserted `now-60s` ⇒ `ok=True`.
- Happy path: `noop` row within window corroborates (episode-time guard-marker rows are `noop`).
- Edge case: rows exist but all older than W ⇒ `ok=False` (current live-store shape: ~45 h stale).
- Edge case: empty table ⇒ `ok=False`.
- Error path: corrupt/unreadable DB path ⇒ `error` set, `ok=False`.
- Error path: missing file ⇒ fail-visible (error), never silent success.

**Verification:** New tests green in the focused guard suite; no store writes (read-only connection).

- [ ] **Unit 2: Boot-transient classification + corroborated WARN lane**

**Goal:** The core fix: corroborated boot-window transients render WARN; everything else keeps FAIL.

**Requirements:** R1, R2, R5

**Dependencies:** Unit 1

**Files:**
- Modify: `src/magic_hermes/cli.py`
- Test: `tests/test_doctor_contract.py`

**Approach:** Add `_BOOT_TRANSIENT_LIVENESS_WINDOW_S = 6h` constant + env override (mirror `_DOCTOR_WALL_BUDGET_ENV`); add `bool` predicate matching KD1 shapes over `sidecar_failures`; in the `not sidecar_healthy` block, after the lane-skew branch and before the FAIL renders: if predicate AND `liveness_status(...).ok` ⇒ single typed WARN row (KD3 wording) instead of FAIL; else existing FAIL branches byte-identical. The liveness query runs ONLY on this path (no cost to healthy runs).

**Patterns to follow:** wall-budget WARN lane and bind-contention WARN lane in the same function; "(fast-fail retry)" quoting from the existing FAIL branch.

**Test scenarios:**
- Happy path: crash-then-hello-timeout pair (`status -6` signature + `Runtime timed out ... during hello`) with fresh fixture-store rows ⇒ WARN row, no FAIL row, exit-code path keeps rc 0, `FAIL 0` summary invariant preserved (extends `test_no_fail_message_contains_fail_zero` class).
- Edge case: same pair, stale store (rows older than W) ⇒ still FAIL.
- Edge case: same pair, unreadable store ⇒ still FAIL.
- Edge case: single `noop` recent row corroborates (liveness counts noop).
- Error path: lane-skew failure pair ⇒ still FAIL (regression guard, existing typed lane untouched).
- Error path: post-hello/doctor-call failure shape in the list ⇒ FAIL (predicate disqualifies).
- Integration: wall-budget-exhaustion and bind-contention WARN verdicts unchanged (existing tests keep passing unmodified).
- Constants: new window constant positive, `< 24 h` honesty bound; env override honored; existing constants test extended.

**Verification:** Focused doctor-contract suite green; verdict table for the episode class now WARN; outage matrix (skew/post-hello/DB-health/uncorroborated) still FAIL.

- [x] **Unit 3: Contract-constants guard + lint/docs pass**

**Goal:** Keep the contract machine tight around the new lane.

**Requirements:** R1, R5, R7

**Dependencies:** Unit 2

**Files:**
- Modify: `tests/test_doctor_contract.py` (constants + invariant extensions)
- Modify: `docs/` doctor-row documentation if the repo documents verdict rows (check `docs/` at execute time; skip if none)

**Approach:** Extend (do not weaken) `test_constants_are_positive_floats_within_budget` with the new window constant; extend the `FAIL 0` invariant test class with the WARN-lane case; run ruff over touched files.

**Test scenarios:**
- Constants test covers `_BOOT_TRANSIENT_LIVENESS_WINDOW_S` (positive, bounded).
- `FAIL 0` message invariant holds for the WARN-lane render path.

**Verification:** Focused suites green; `ruff check src/magic_hermes tests` clean for touched files.

- [ ] **Unit 4: Validation, release, install convergence, land + push**

**Goal:** Make the evaluator actually see the fix (run requirement: land and push the owner-repo fix).

**Requirements:** R4, R6, R7

**Dependencies:** Units 1-3

**Files:**
- Modify: `pyproject.toml` (+ lockfile via release script)
- Repository release artifacts per `scripts/release.py` convention

**Approach:** Focused validation first (doctor-contract + historian-guard suites; focused `tests/test_cli.py` selection only — full-battery store/hermetic hazards stand); then `scripts/release.py` bump to v0.3.64 with the repo's normal release flow; `magic-hermes install` to refresh the installed wrapper; verify the installed CLI reports the new version (install convergence — the evaluator probes the installed venv binary); land and push through the repo's normal PR/commit workflow (no maestro-side changes anywhere in the diff); then observe maestro's subsequent evaluations — recurrence guard is three consecutive PASS with zero recurrence.

**Test scenarios:**
- Verification: installed wrapper version == new release version; evaluator's next evaluation renders PASS with the WARN lane present during any load flap; scope-diff gate: only `src/magic_hermes/cli.py`, `src/magic_hermes/historian_guard.py`, `tests/test_doctor_contract.py`, `tests/test_historian_guard.py`, plan doc, release metadata.

**Verification:** Recurrence guard satisfied (3× PASS evaluations post-land); finding `d59758598379e27cadcaa817` state moves toward CLOSE per maestro's own contract.

## System-Wide Impact

- **Interaction graph:** `run_doctor` verdict surface only; the liveness helper adds a read-only store open on the transient path (bounded by the existing quick-scan discipline; runs only when a boot failure already occurred).
- **Error propagation:** unreadable store ⇒ fail-visible ⇒ NOT corroborated ⇒ FAIL retained; no new exception classes.
- **State lifecycle risks:** none — no writes, no new persistent state, no schema changes.
- **API surface parity:** no CLI flag/arg changes; one env knob added (documented in the WARN remedy text), following the wall-budget env precedent.
- **Integration coverage:** the doctor-contract suite is the cross-layer proof (sidecar pair simulation → verdict rows → exit-code contract); the evaluator itself stays the external black-box gate.
- **Unchanged invariants:** evaluator math, maestro probe cadence, guard trigger DDL, upstream pins, wall-budget constants, lane-skew FAIL, DB-health FAIL, `FAIL 0` invariant.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Mis-drawn transient/outage boundary weakens the gate | Conservative shape-match (KD1) + liveness corroboration (KD2) + full outage matrix in tests (Unit 2 scenarios) |
| Stale rows white-wash a real outage | Positive-liveness within bounded W; unreadable ⇒ not corroborated; W honesty-bounded (< 24 h) and test-pinned |
| Episode signature drift (node release changes assertion text) | Matcher pinned at implementation against captured stderr; conservative default: unrecognized boot-failure shapes simply stay FAIL |
| Fix lands but evaluator still fails (install gap) | Unit 4 install-convergence gate: verify installed wrapper version before observing evaluations |
| Worktree sweep / foreign `.git` recurs | Unit 0 re-materialization gate (HEAD == `bfeb007`, clean) before any git work |
| Lease/timeout kills the lane again | Bank artifacts early; deliver phase_result within budget (two predecessor attempts died this way) |

## Documentation / Operational Notes

- The WARN row text names the offline-audit remedy and the ops context; operator env knob for W follows the wall-budget env precedent.
- Ops note for the estate plane: shared-host memory/swap pressure is the enabling condition; capacity policy would reduce flap frequency for every probe-based gate (deferred, not this repo).

## Sources & References

- **Origin document:** spool artifact `delegate/9a65492b7dd34319ab6ef865305ad102-brainstorm-magic-context-doctor-boot-flap.md` (untracked in this repo; conductor spool `/home/agent/.hermes/conductor-delegate-spool-maestro`)
- Related code: `src/magic_hermes/cli.py` (`run_doctor`), `src/magic_hermes/historian_guard.py` (`guard_status`, `make_fixture_db`)
- Related PRs: #11 (guard fix `12176ca`), #88 (bind mutex `936753b`), #108 (deferred-bind handshake `0d1b33e`)
- Evaluator: `maestro/src/maestro/context.py` (off-limits, contract reference only)
