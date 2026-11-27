---
title: "fix: Magic Context state unreadable — store readability SLA, bounded doctor, boot-transient severity adoption"
type: fix
status: active
date: 2026-11-28
origin: spool brainstorm 1c6eef7b1b5f4739a3a64f99e04d1891 (approaches + plan-handoff docs; untracked in this repo — see Sources)
---

# fix: Magic Context state unreadable — store readability SLA, bounded doctor, boot-transient severity adoption

**Target repo:** magic-hermes (this worktree). Run: 7ca7ed8dee6e4ed0a3a8292e6f29cec7, finding d59758598379e27cadcaa817 (contract `phase1.context.operational`, entity magic-context, HIGH, OPEN).

## Overview

Maestro's `phase1.context.operational` evaluation re-opened finding d59758598379 on a new failure class: its bounded-operational-scan reads the shared context store read-only (`mode=ro`, `busy_timeout=8000`) after a healthy doctor verdict, and three times (2026-11-27 08:13:59Z, 12:03:33Z; 2026-11-28 00:21:23Z) the read sequence threw `OperationalError: database is locked` mid-scan — an exclusive lock window ≥ 8 s on the 10.6 GB WAL store colliding with the 8 s reader budget. The doctor passed inside all three failing evaluations, so the prior boot-severity remediation cannot heal this class.

This plan lands, in one fold: (A) a store-readability SLA lane — a lock-window census sampler, a timer-pinned store keepalive that keeps the WAL index valid and checkpoints PASSIVE-only, a doctor readability WARN row, and a maestro-reader-replica fence test suite; (B) the remainder of the banked gated-U4 design (outermost hard wall bound so the doctor can never hang to rc 124, plus a stdin-write deadline); and (C) R5 lineage adoption of the unlanded boot-transient severity policy from `conductor/run-9f3d6bce2fc6` (924 validated, never-folded lines), reconciled into the current verdict block.

## Problem Frame

The shared store `~/.local/share/cortexkit/magic-context/context.db` (10.6 GB, WAL mode, ~10+ concurrent pi/node connections with constant churn, chronic ~298 K pending_ops) occasionally presents ≥ 8 s windows in which a read-only connection cannot read. In WAL mode a writer never blocks a reader, so the blocking state is an exclusive-equivalent window. Empirical correction from adversarial review (SQLite 3.53.1, maestro-reader replica reproduced byte-for-byte): a `mode=ro` connection SURVIVES stale/`-shm`-deleted/corrupted-shm and concurrent-writer scenarios — the wal-index-recovery hypothesis is falsified as a reader blocker on this host. The only deterministically reproducible reader-blocking WAL state is a **live holder in `PRAGMA locking_mode=EXCLUSIVE`** (which the keepalive does not heal). magic-hermes's own OPS-03 comment (src/magic_hermes/bridge/runtime.mjs, bind-contention block) documents "WAL checkpoint storms" and "20s+ holds observed" on this store. The exact holder of the three observed events is unpinned; the census unit (Unit 1) discriminates it with evidence, and its verdict GATES the closure claim (contingency below): the capability units land regardless, but if the census pins an exclusive-hold holder outside magic-hermes's reach, the zero-recurrence target is unreachable with this delta and must be escalated before fold.

The evaluator contract (immutable input, maestro-owned): doctor_ok = rc 0 + "FAIL 0" in stdout; the scan then issues a fixed query sequence (`sqlite_master` count → pending counts → `historian_runs` → `dream_runs` → `task_schedule_state` → `mirror_cursors` → `memory_verifications`); any exception → state ERROR, reason "Magic Context state unreadable", regardless of doctor_ok. Closure requires PASS plus three subsequent PASS evaluations with zero recurrence at a ~1.5 events/day rate — the read-exception probability must go to ~0, not merely shrink.

## Requirements Trace

- R1 (engineering-context R0/R1): fix lands in magic-hermes only; no maestro edits; no vendored-adapter edits; swim lanes preserved (repo ships capability, estate wires scheduling). → Units 1-6.
- R2 (R2): normal CE/Conductor workflow — this plan; implement via conductor units; fold through the engine. → Unit 7.
- R3 (R3): regression tests reproducing the failure class — a maestro-replica reader under adversarial lock fixtures. → Units 3, 5, 6.
- R4 (R4): targeted + full validation before landing. → Unit 7.
- R5 (R5): lineage adoption — rebase the 9f3d6bce severity branch rather than reimplement. → Unit 6.
- R6 (R6): commit + push through the normal workflow (engine checkpoints; never amend). → Unit 7.
- R7 (R7): structural prevention over runtime healing — keepalive + crash hygiene + PASSIVE-only checkpoint policy, not a retry ladder. → Units 2, 5.
- R8 (closure contract): drive the scan's read-exception probability toward zero — deterministically via the fence suite (exclusive-hold repro) and the outer bound; against the real-world holder via the keepalive (if census pins a keepalive-healable family) or the escalation gate (if it pins an out-of-repo exclusive holder). The fence suite is the deterministic anchor; live census evidence decides the closure posture.

## Scope Boundaries

- No change to maestro's reader (its `mode=ro` + 8 s budget is immutable input by R0).
- No upgrade/patch of the vendored `@cortexkit/pi-magic-context` adapter (OPS-03: version bumps are a dead end).
- No host capacity/swap work (ops-plane, escalated only).
- No pending_ops semantic redesign (adapter-owned table; only its read-side observability is touched).
- No wiring of the estate systemd timer inside this repo delta — the timer unit is documented in README ops notes; the estate (hermes-infra plane) owns `~/.config/systemd/user/` wiring.
- The hermes-agent session-store sibling finding (`phase1.session.operational`) is out of scope.

### Deferred to Separate Tasks

- Watcher-healer lane (`conductor/run-22fff19a7c24` + `magic-hermes-converge.timer`): stays disabled per the 2026-11-24-001 plan's routing; separate lane.
- Ops-plane host-load escalation: raised as a note, not repo work.
- Engine work items (watchdog-side ERROR-vs-load telemetry correlation, pytest-xdist pre-seed): fed to the conductor ledger, not this repo.

## Context & Research

### Relevant Code and Patterns

- `src/magic_hermes/db_toolkit.py` — the `magic-hermes db` action group (`status`/`blockers`/`migrate`): already has `store_path()`, `scan_store()` with a `mode=ro, timeout=5.0` probe recording "store unreadable: {exc}", `list_holders()` (lsof-based holder census), `main_db()` dispatch, `--json` conventions. The keepalive and census extend this group.
- `src/magic_hermes/bridge/runtime.mjs` — OPS-03 `openDatabaseWithLockGuard()`: cross-process bind mutex (atomic mkdir + pid token, 90 s deadline, holder-death reclaim) + bounded transient-lock retry with cooldown-aware backoff. The keepalive's open mirrors this pattern's Python equivalent already used by `src/magic_hermes/historian_guard.py` (`BUSY_TIMEOUT_MS = 10_000`, `_connect_rw`/`_connect_ro`).
- `src/magic_hermes/cli.py` — `run_doctor()` (wall budget `_DOCTOR_WALL_BUDGET_S = 75` via `MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S`, render reserve, wall-exhausted WARN lane, bind-contention WARN lane, lane-skew typed verdict); doctor subcommand parser; `main()`.
- `src/magic_hermes/runtime.py` — `RuntimeClient`: select-based read deadline in `call()`, `_write_payload()` under `_stdin_lock` (NO write deadline — the un-guarded hang point), `_dispose()` with process-group SIGTERM→SIGKILL escalation (`start_new_session`), idle sweeper reaping.
- Lineage branch `conductor/run-9f3d6bce2fc6` (+ `da9a080`) — `_DOCTOR_BOOT_TRANSIENT_LIVENESS_S = 24h`, `_DOCTOR_BOOT_TRANSIENT_FAILED_ROW_S = 72h`, `_boot_transient_corroboration(db_path)` (read-only in-store liveness: recent success/noop rows across historian/dream/activity tables, "a gate that cannot see must not absolve"), boot-transient classification into the `not sidecar_healthy` block; +389 lines of contract tests.
- Banked design `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md` Unit 4 — the verbatim design for the outer bound + typed budget-exhausted verdict + corroborated severity; its trigger ("a successor on `phase1.context.operational` re-opens") is satisfied by this run.
- Estate wiring precedent: `magic-context-watch.service`/`.timer` (systemd user unit, 15-min `OnUnitActiveSec`, `Persistent=true`, `Nice=10`, oneshot) — the shape the keepalive timer follows.
- `tests/test_bind_lock_race.py`, `tests/test_doctor_contract.py` (fixtures: `isolated_home`, `make_fixture_db`, `_run_doctor` helpers), `tests/test_db_toolkit.py`, `tests/test_cli.py`, `tests/test_runtime.py`.

### Institutional Learnings

- Fold gates are structured-field contracts: `validation_evidence` must be a structured phase_result field; implement-phase envelopes declare engine-delta changed surfaces, never the unit worktree's full footprint.
- Worktree test runs require `PYTHONPATH=src` (the canonical venv editable-installs point at the canonical tree — silent wrong-tree hazard).
- `uv run` rewrites `uv.lock` — revert immediately (`git checkout -- uv.lock`); any working-tree drift moves the validation digest.
- Static lane: 6 pre-existing base findings in `src/magic_hermes/memory_provider/__init__.py`-adjacent surface — escalate, never fix in-run.
- Commit phase may be satisfied by attesting the engine's integration checkpoint; never amend engine history. Landing checks must verify the daemon-visible version, never shell `metadata.version` (ambient-PYTHONPATH shadow).

## Prior-Art Survey

```json
{
  "schema_version": 2,
  "verdict": "extend",
  "scope": "magic-hermes repository (src/magic_hermes, tests/, docs/plans) at 2d1550c046caf966dc2d5b1e519171919fc60ac, plus lineage branches conductor/run-9f3d6bce2fc6 and da9a080",
  "freshness": {
    "vcs_reference": "2d1550c046caf966dc2d5b1e519171919fc60ac"
  },
  "budget": {
    "max_search_passes": 1,
    "max_candidate_inspections": 1,
    "exhausted": false
  },
  "candidates": [
    {
      "path_or_symbol": "conductor/run-9f3d6bce2fc6:src/magic_hermes/cli.py (boot-transient severity + corroboration, +208) and tests/test_doctor_contract.py (+389)",
      "description": "validated, unlanded severity policy for this finding's prior class; adopt via rebase per R5",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md Unit 4",
      "description": "banked gated design for the bounded doctor (outer hard bound, typed budget-exhausted verdict, corroborated severity); trigger fired",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "src/magic_hermes/db_toolkit.py (db status/blockers/migrate, store_path, scan_store, list_holders, main_db)",
      "description": "existing shared-store CLI group and holder-census machinery; keepalive and census extend it",
      "disposition": "extend"
    },
    {
      "path_or_symbol": "src/magic_hermes/bridge/runtime.mjs openDatabaseWithLockGuard (OPS-03) and src/magic_hermes/historian_guard.py busy-timeout connect helpers",
      "description": "bind-mutex + transient-retry pattern and bounded busy-timeout connection pattern for the keepalive's open",
      "disposition": "reuse"
    },
    {
      "path_or_symbol": "src/magic_hermes/cli.py wall-budget / bind-contention / lane-skew WARN lanes",
      "description": "existing typed-verdict doctrine and tests in run_doctor; readability WARN row and outer bound follow them",
      "disposition": "extend"
    },
    {
      "path_or_symbol": "src/magic_hermes/runtime.py RuntimeClient._dispose (process-group SIGTERM->SIGKILL) and call() read deadline",
      "description": "already-landed process-group reap; Unit 5 only adds the missing write deadline and CLI-entry outer bound",
      "disposition": "reuse"
    }
  ]
}
```

## Key Technical Decisions

- **Store-side fix only (R0/R1).** The ERROR is minted by maestro's exception handler, but the only owner-repo lever is the store's lock profile. The plan makes the store readable within an 8 s budget instead of teaching maestro to wait.
- **Keepalive is a transient timer-pinned CLI, not a resident daemon.** Follows the estate's `magic-context-watch` oneshot+timer pattern: no new always-on failure domain, no supervision surface. Its remedies are WAL-bound enforcement (PASSIVE-only checkpoints) and WAL-index freshness (prevention); per the review-corrected mechanism, it is NOT a heal for a live exclusive-lock holder — that family routes to the census/escalation gate.
- **Keepalive never TRUNCATE-checkpoints.** It runs `wal_checkpoint(PASSIVE)` only when the WAL exceeds a size threshold, mirroring hermes-agent's maintenance doctrine ("a TRUNCATE reset from a transient CLI would race a live writer"). The keepalive's own open uses the historian-guard busy-timeout pattern (10 s) with bounded transient retry.
- **The census gates the closure claim, not the capability landing.** The units land regardless of the census verdict, but the fold envelope's closure posture depends on it: census pins a keepalive-healable family (WAL-index staleness, WAL-growth checkpoint pressure) → the remedy is credited; census pins a live exclusive-lock holder outside magic-hermes's estate → escalate out-of-repo before fold (the zero-recurrence target is unreachable with this delta). Adversarial review empirically falsified the stale-shm reader-blocker hypothesis, so the deterministic regression anchor is the exclusive-hold repro, and live census evidence is the only path to crediting the keepalive remedy against the real-world holder.
- **Doctor readability row is a cheap WARN, not a FAIL.** A 2 s-budget `mode=ro` probe row inside `run_doctor` (naming the keepalive remedy) — observable every evaluation cycle without pressuring the 75 s wall budget. The maestro scan's ERROR remains the closure signal; the doctor row is the human/ops surface.
- **Outer bound via a CLI-entry hard deadline (SIGALRM-style) below the evaluator's 90 s kill.** Armed at the doctor entry at `budget + render reserve` (defaults 75 + 10 s) with a DISTINCT env override from the in-doctor wall budget (shared override makes small-budget tests unreachable — the in-doctor lane would fire first); expiry renders a typed `budget-exhausted` verdict and exits — never silence, never rc 124. The expiry path performs NO new blocking I/O: severity at expiry is FAIL unless corroboration was already computed before the deadline (cached value) — a fresh corroboration probe (its own 5 s busy timeout) inside the 10 s render reserve can overshoot the 90 s kill in exactly the scenario the outer bound exists to close. The in-doctor 75 s budget lane stays unchanged; the outer bound covers the un-deadlined paths (stdin write hang, store scan overrun, version/install probes).
- **Stdin-write deadline added in `_write_payload`.** A bounded write (write-ready select or a watchdog timeout on the write path) so a stalled sidecar reader converts to the typed boot-transient path instead of hanging the doctor.
- **Severity-policy rebase reconciles three verdict lanes in one block.** Order of specificity in the `not sidecar_healthy` block: (1) lane-skew typed verdict (existing, most specific cause), (2) boot-transient classification with corroboration (adopted), (3) generic sidecar FAIL. Where the 1974d388 design and the banked U4 wording diverge, the U4 wording wins (doctrine: execute verbatim, never re-brainstorm).
- **Fence tests replicate maestro's reader byte-for-byte.** `mode=ro` URI, `timeout=8`, `busy_timeout=8000`, `query_only=ON`, and the exact query sequence in the exact order (the exception point sits between the pending counts and the `historian_runs` read — fixtures must reproduce that position, not just "a locked read").

## Open Questions

### Resolved During Planning

- **Is the doctor the lock holder?** No — the doctor passed inside all three failing evaluations; phase2 doctors passed around each event; no rc-124 kills near the events. The doctor is a victim-of-adjacency at most, and Unit 5 removes its own un-bounded paths.
- **What landed from prior lanes at master?** The wall-budget WARN, bind-contention lane, lane-skew typed verdict, process-group reap, and read deadlines in `call()` are at master; the boot-transient corroboration policy is NOT (it sits only on the unlanded 9f3d6bce branch); the CLI-entry outer bound and stdin-write deadline are NOT (confirmed absent at 2d1550c).
- **Where does the census output live?** Outside the repo (XDG state dir under `~/.local/state/magic-hermes/census/`, JSONL) — evidence files must not become repo working-tree drift.

### Deferred to Implementation

- The census sampling cadence and retention (start at 30 s sampling; retain JSONL capped at a size bound) and, if a no-op immediate txn proves insufficient for WAL-index freshness, whether a heartbeat table OUTSIDE the corroboration set is warranted (returns to the fold envelope — never a row in the corroboration set).
- The precise outer-bound mechanism (SIGALRM handler vs watchdog thread that renders and `os._exit`s) — must compose with the stderr reader thread and the existing signal-free runtime; the banked U4's "timer armed at CLI entry" is the contract, the mechanism is implementation's.
- Rebase conflict resolution details in `tests/test_doctor_contract.py` (branch tests were written against the bfeb007 block shape).
- Whether the doctor readability row measures only `sqlite_master` or the full maestro query sequence within its 2 s budget (start with the cheap probe; widen only if latency allows).

## Implementation Units

### Phase 1 — capability + regression anchor (starts the wall-clock evidence early)

- [ ] **Unit 1: Store census sampler (`magic-hermes db census`)**

**Goal:** A read-only instrumentation action that samples the store's readability in maestro's exact reader shape and records lock-window evidence (holder family discrimination) to an out-of-repo JSONL log.

**Requirements:** R1, R3 (observability half), R8 (closure evidence).

**Dependencies:** None. Started FIRST in implement so its observation window spans the run.

**Files:**
- Modify: `src/magic_hermes/db_toolkit.py` (new `run_db_census` action + sampler; wire into `main_db` dispatch)
- Modify: `src/magic_hermes/cli.py` (db subparser help text for the new action)
- Test: `tests/test_db_toolkit.py`

**Approach:**
- Sampler loop (duration/interval flags): per tick, open the store exactly as maestro does (`file:<db>?mode=ro`, `timeout=8`, `busy_timeout=8000`, `query_only=ON`), run the maestro query sequence in order, and record {timestamp, ok, latency_ms, exception, failing_query}; stat `-wal`/`-shm` (size, mtime) each tick; on a lock event record a holder snapshot via the existing `list_holders()` (lsof) and, if a writable open with the historian-guard timeout succeeds afterward, the recovery verdict.
- Output: JSONL under `~/.local/state/magic-hermes/census/census-<start-ts>.jsonl` (created dir; never inside the repo tree); `--json` prints a summary object (events seen, worst latency, wal max size).
- Read-only discipline: the census NEVER writes the store; the only writable connection attempt is the post-event recovery probe (bounded, busy-timeout, rollback-only).

**Patterns to follow:** `run_db_status`/`run_db_blockers` action shape and `--json` conventions; `list_holders()` for the lsof census; the maestro reader shape (frozen contract, see brainstorm evidence).

**Test scenarios:**
- Happy path: fixture store, sampler over a short interval with no contention → all ticks ok, JSONL written outside repo, summary counts zero events.
- Error path: fixture store with a live holder in `PRAGMA locking_mode=EXCLUSIVE` (the empirically reproducible reader-blocker on this host) → the tick records `OperationalError: database is locked` at the expected query position and a holder snapshot row (the `list_holders()` lsof census must name the holder pid/command).
- Edge case: `--duration 0` single-shot mode → one tick, clean exit, rc 0 (probe ok) or rc 2 (probe failed — distinguishable from harness error).
- Edge case: output dir unwritable → fail loud with an actionable message, rc 1, nothing written to the repo tree.

**Verification:**
- `magic-hermes db census --duration <s> --json` runs green against the live store from the run workspace; the JSONL lands under the XDG state dir; a deliberate lock fixture produces a captured event row; no repo-tree writes occur (`git status` clean).

- [ ] **Unit 2: Store keepalive (`magic-hermes db keepalive`)**

**Goal:** A transient CLI action that keeps the shared store's WAL index valid and its WAL bounded: opens the store read-write with the bounded busy-timeout pattern, performs the minimal freshness transaction, and PASSIVE-checkpoints when the WAL exceeds a threshold.

**Requirements:** R1, R7 (structural prevention), R8.

**Dependencies:** None (census evidence refines tuning, never gates this unit).

**Files:**
- Create: `src/magic_hermes/store_keepalive.py` (self-contained action module)
- Modify: `src/magic_hermes/db_toolkit.py` (`main_db` dispatch: `keepalive` action)
- Modify: `README.md` (§1b-2 extension: keepalive semantics, PASSIVE-only doctrine, estate timer wiring example — `magic-context-watch.timer` shape, 15-min cadence, `Persistent=true`, `Nice=10`)
- Test: `tests/test_store_keepalive.py`

**Approach:**
- Open the store read-write via the historian-guard pattern (`BUSY_TIMEOUT_MS` bounded connect, `pragma busy_timeout`) — NOT via the sidecar/adapter (no bridge boot; this is a store-plane action, not a session-plane one). Bounded transient retry (2 attempts, cooldown backoff) on `SQLITE_BUSY` at open; fail loud with rc 1 and the actionable remedy text otherwise (a keepalive that cannot open must never hang or retry forever).
- Freshness: the minimal transaction that refreshes the WAL index — **decided at plan time: a no-op immediate transaction** (BEGIN IMMEDIATE; COMMIT; — no schema change, no row writes). A heartbeat row is FORBIDDEN: the boot-transient corroboration reads activity tables (historian_runs, source_contents, message_history_index, session_projects, memories, notes, compartments); a heartbeat row in any of them would white-wash real outages to WARN (review finding). If a no-op txn proves insufficient to refresh the WAL index, a heartbeat may only live in a table OUTSIDE the corroboration set, and that decision returns to the fold envelope.
- WAL bound: if `-wal` size exceeds the threshold flag (default ~64 MB), run `wal_checkpoint(PASSIVE)` and record frames checkpointed; NEVER `RESTART`/`TRUNCATE` (doctrine comment in-code, mirroring hermes-agent's maintenance note).
- `--json` status output: {opened, heartbeat, wal_bytes_before/after, checkpoint_mode: "passive"|"none", duration_ms}; exit 0 on success, 1 on lock exhaustion, 2 on store-missing.
- Estate wiring (README ops note only — NOT a repo delta): a systemd user timer example invoking `magic-hermes db keepalive --json` every 15 min; wiring itself belongs to the estate/infra plane per swim lanes.

**Patterns to follow:** `src/magic_hermes/historian_guard.py` `_connect_rw` busy-timeout discipline; `run_db_status` action shape; `magic-context-watch.service`/`.timer` estate shape (README example); the PASSIVE-only checkpoint doctrine comment from hermes-agent's `persistence/sqlite/admin/_maintenance.py`.

**Test scenarios:**
- Happy path: fixture WAL-mode store with a grown WAL → keepalive opens, heartbeats, PASSIVE-checkpoints, JSON reports the shrink, rc 0.
- Happy path: WAL below threshold → no checkpoint, `checkpoint_mode: "none"`, rc 0.
- Error path: fixture store held by an exclusive writer for the full busy window → open exhausts retries, rc 1 with the actionable message, no hang (bounded wall time asserted in-test).
- Edge case: store path absent → rc 2, distinct message, no directory creation side-effects beyond the state dir.
- Edge case: concurrent keepalive invocations (two processes) → both complete (busy-timeout serialization), no TRUNCATE, WAL intact.
- Integration: a live `locking_mode=EXCLUSIVE` holder fixture → keepalive's open exhausts its bounded retries and fails loud rc 1 (it must never queue behind or break an exclusive holder — that is the escalation plane's call, not the keepalive's); no hang, no TRUNCATE.
- Integration: a stale-shm fixture (killed holder) → the maestro-replica reader reads clean even WITHOUT keepalive (review-verified truth, pinned by Unit 3's fixture d); the keepalive run additionally reports WAL-index freshness — prevention, not healing.

**Verification:**
- `magic-hermes db keepalive --json` green against the live store from the run workspace (its own run also doubles as a live recovery/freshness pass); the stale-shm integration test passes; no TRUNCATE appears anywhere in the new module (grep + test).

### Phase 2 — verdict surface + teeth

- [ ] **Unit 3: Reader-shape fence test suite (the R3 anchor)**

**Goal:** A permanent regression suite that replicates maestro's bounded-operational-scan reader byte-for-byte and asserts no read exception within the 8 s budget under each adversarial lock fixture, with and without the keepalive remedy.

**Requirements:** R3 (the failure class reproduced deterministically), R8.

**Dependencies:** Unit 2 (the remedy under test); the fixture builders are independent.

**Files:**
- Create: `tests/test_store_readability.py`
- Test-support reuse: the maestro query sequence as a shared helper (fixture-scoped, in the new test module; do NOT import maestro code).

**Approach:**
- Reader harness: `file:<db>?mode=ro`, `timeout=8`, `PRAGMA busy_timeout=8000`, `PRAGMA query_only=ON`, then the exact maestro query sequence in order (`sqlite_master` table count → pending_ops/migration_pending/pending_session_cleanup counts → `historian_runs` recent rows → `dream_runs` → `task_schedule_state` → `mirror_cursors` → `memory_verifications` aggregate); failure must surface which statement raised.
- Fixtures: (a) clean WAL store; (b) grown-WAL store; (c) live concurrent writer (short write txns in a helper thread); (d) stale-shm simulation (holder process opens RW, is SIGKILLed with the wal-index left stale — documented to read CLEAN per adversarial review: the suite must assert this, pinning the falsified hypothesis so it is never re-claimed); (e) live `locking_mode=EXCLUSIVE` holder (THE deterministic blocker).
- Assertion matrix (review-corrected): (i) the reader completes with zero exceptions across fixtures (a)-(d) — with AND without keepalive (wal-index staleness is not the blocker); (ii) under fixture (e) the reader fails bounded within its 8 s budget at the expected query position — the reproduced failure class, pinned forever (R3); (iii) keepalive's remedy assertions are its own contract (WAL-index freshness reported, WAL bound enforced, PASSIVE-only) — the keepalive is prevention against the WAL-growth/checkpoint-pressure family, NOT a heal for an exclusive holder; crediting it against the real-world holder requires census evidence.

**Patterns to follow:** `tests/test_bind_lock_race.py` (process-level lock fixtures); `tests/test_doctor_contract.py` fixture helpers (`isolated_home`, `make_fixture_db`).

**Test scenarios:**
- Happy path (each fixture a-d): reader replica completes the full sequence, zero exceptions, latency recorded — with and without keepalive (fixture d pins the falsified stale-shm hypothesis: it reads clean).
- Regression (fixture e — live EXCLUSIVE holder): the replica raises `OperationalError: database is locked` at the `historian_runs` position within the 8 s budget — the observed signature, deterministically reproduced and pinned forever.
- Remedy assertion: keepalive run in-test → its JSON reports WAL-index freshness and WAL-bound enforcement; WAL size shrinks when above threshold; PASSIVE-only (no TRUNCATE/RESTART ever).
- Edge case: fixture with WAL exactly at the keepalive threshold boundary → deterministic no-checkpoint path.
- Error path: replica against a path that does not exist → the harness reports the distinct not-found shape (not a lock), proving the suite discriminates failure modes.

**Verification:**
- The suite passes: fixtures (a)-(d) green with and without keepalive; fixture (e) reproduces the historical signature deterministically; keepalive's own contract assertions green; CI-equivalent run in the run workspace with `PYTHONPATH=src`.

- [ ] **Unit 4: Doctor store-readability WARN row**

**Goal:** `run_doctor` renders one cheap row measuring the store's readability in the reader-replica shape, WARN on lock/slow, naming the keepalive remedy — the SLO becomes visible in every evaluation cycle.

**Requirements:** R1, R7 (observability), R8.

**Dependencies:** Unit 2 (the remedy named in the row's text).

**Files:**
- Modify: `src/magic_hermes/cli.py` (`run_doctor`: one probe + row, placed with the other store-plane rows, guarded by the wall budget)
- Test: `tests/test_doctor_contract.py`

**Approach:**
- Probe: one `mode=ro` open + `sqlite_master` count with a small budget (2 s default, env-overridable) — deliberately cheaper than maestro's 8 s so it never pressures the 75 s wall budget; latency recorded in the row text.
- Verdict: PASS (readable, latency named); WARN (locked or unreadable — quotes the exception, names `magic-hermes db keepalive` and `db census` as the remedies); never FAIL (the closure signal stays the maestro scan's; the doctor row is observability, consistent with the existing WARN-lane doctrine that keeps rc 0 + "FAIL 0").
- Budget interplay: the probe runs inside the existing wall budget; if the wall is already exhausted, the row is skipped (the wall-exhausted WARN already renders).

**Patterns to follow:** the existing guard/wall-budget/bind-contention WARN lanes in `run_doctor` (same block placement, same "maestro requires doctor to exit 0" doctrine comments).

**Test scenarios:**
- Happy path: readable fixture store → PASS row with latency, rc 0, "FAIL 0" preserved.
- Error path: locked fixture store → WARN row quoting the exception and naming the remedies, rc 0, "FAIL 0" preserved (the row must never break the evaluator contract).
- Edge case: wall budget exhausted before the probe → row skipped, wall-exhausted WARN unchanged (existing tests stay green).
- Edge case: store missing → WARN row with the distinct not-found text.

**Verification:**
- Doctor contract tests green including the new rows; a live `magic-hermes doctor --json` run from the run workspace shows the readability row with real latency.

- [ ] **Unit 5: Outermost hard wall bound + stdin-write deadline (banked U4 4a remainder)**

**Goal:** The doctor can never hang past the evaluator's kill: a hard deadline armed at CLI entry (budget + render reserve < 90 s) whose expiry renders the typed `budget-exhausted` verdict and exits; and the runtime's stdin-write path is deadline-guarded so a stalled sidecar reader converts to the typed path instead of hanging.

**Requirements:** R7 (rc 124 impossible), R3 (both hang classes covered by tests).

**Dependencies:** Unit 6 lands in the same verdict block; Unit 5's typed row uses the severity policy's corroboration when available — land Unit 5 first with severity=FAIL default, Unit 6 upgrades it (sequencing note below).

**Files:**
- Modify: `src/magic_hermes/cli.py` (doctor entry: hard-deadline arming before any sidecar/store work; typed `budget-exhausted` verdict render path)
- Modify: `src/magic_hermes/runtime.py` (`_write_payload`: bounded write — write-ready select or watchdog timeout; on expiry, `_dispose` + typed `RuntimeProtocolError`)
- Test: `tests/test_cli.py`, `tests/test_runtime.py`, `tests/test_doctor_contract.py`

**Approach:**
- Hard deadline: armed at the `doctor` command entry (before version probes, installs scan, sidecar boots, store work) at `wall_budget + render reserve` (defaults 75 + 10 s; a DISTINCT env override from the in-doctor budget so tests can reach the outer path without the in-doctor lane firing first); expiry handler renders the typed `budget-exhausted` row and exits by its severity — never silence, never rc 124. The expiry path does NO new blocking I/O: severity defaults FAIL, upgraded to WARN ONLY from a corroboration value computed before the deadline (cached at last successful probe). Mechanism choice (SIGALRM vs watchdog thread with `os._exit` after render) is implementation's, constrained by: must interrupt the main thread's blocking writes, must compose with the stderr reader thread, must leave no sidecar child holding the evaluator's pipes (process-group reap on the way out).
- Stdin-write deadline: the write in `_write_payload` gets a bounded wait (write-ready select under the stdin lock, or a helper-thread write with deadline); expiry disposes the process group and raises the typed `RuntimeProtocolError` that the doctor's failure path already classifies.
- The in-doctor 75 s budget lane and its WARN stay unchanged — the outer bound is strictly belt-and-braces for the un-deadlined paths.

**Execution note:** Start each sub-change from a failing test that reproduces the observed signature (a fake runtime whose stdin never drains; a fake runtime blocking past a 2 s env-overridden budget) before touching the entry or write path.

**Patterns to follow:** the banked U4 4a design text verbatim (docs/plans/2026-11-24-001-…-plan.md); existing budget tests in `tests/test_cli.py`; `test_bind_lock_race.py` process-fixture style for pipe-hang simulation.

**Test scenarios:**
- Happy path: an un-deadlined phase hangs (monkeypatched version probe or install scan that sleeps past a small outer-bound override) → typed `budget-exhausted` row on stdout, exit within budget + reserve, rc by row severity. (Testing via a small WALL_BUDGET override is unreachable — the in-doctor lane fires first; use the distinct outer-bound override or the un-deadlined-phase hang.)
- Happy path: sidecar whose stdin stalls (never reads) → write deadline fires → typed boot-transiant/classified path, no hang, process group reaped (no pipe holder survives — assert via the fixture's pid).
- Error path: budget exhausted without a pre-deadline corroboration value → typed FAIL row (no probe at expiry — assert no new blocking I/O in the expiry path), rc 1, exit inside the bound.
- Edge case: deadline fires while the stderr thread is mid-read → render + exit still complete; thread does not hold the process.
- Edge case: normal fast doctor run → deadline never visible (no row, unchanged output).
- Regression: existing wall-budget-edge and bind-contention WARN verdict tests unchanged.

**Verification:**
- No test path can exceed the configured budget + reserve; both observed hang classes (rc-124 pipe hang, stdin write hang) have failing-then-passing tests; full doctor contract battery green.

### Phase 3 — lineage adoption

- [ ] **Unit 6: Boot-transient severity policy — adopt `conductor/run-9f3d6bce2fc6` onto master (R5)**

**Goal:** Rebase the branch's validated boot-transient severity + in-store liveness corroboration onto the current verdict block, reconciled with the lane-skew and wall-budget lanes, with the branch's contract tests ported.

**Requirements:** R5 (adopt, don't reimplement), R1, R3 (the crash-then-hello class).

**Dependencies:** Unit 5 (the typed budget-exhausted row joins the same classification); reconciled verdict ordering below.

**Files:**
- Modify: `src/magic_hermes/cli.py` (`not sidecar_healthy` block + corroboration helpers: `_boot_transient_corroboration`, the liveness/failed-row window constants, the boot-transient classifier)
- Test: `tests/test_doctor_contract.py` (port the adopted tests, reconciled with current lane-skew tests)
- Reference (read-only): **adoption commit `da9a080`** (the complete content: cli.py +208, tests +389, README +22 — its cli.py patch is byte-identical to `conductor/run-9f3d6bce2fc6`'s checkpoint 2b70520; both apply cleanly onto 2d1550c; the branch alone carries only the 742-line subset) and `conductor/run-9f3d6bce2fc6`; the branch's plan doc is historical context, not carried into the repo.

**Approach:**
- Adopt `da9a080`'s diff for `run_doctor`'s failure classification onto the master block: verdict specificity order (1) lane-skew typed verdict (existing), (2) boot-transient classification — `status -6` / `uv_thread_create` crash shapes, hello-timeout-before-first-response, and Unit 5's budget-exhausted-during-boot — rendering WARN only when `_boot_transient_corroboration` sees recent read-only in-store liveness within the 24 h window, (3) generic sidecar FAIL. Corroboration values computed before any deadline are cached so Unit 5's expiry path can upgrade severity without new I/O.
- Corroboration helper ported as-is (read-only, opens `mode=ro`, "a gate that cannot see must not absolve"); the three crash-string shapes from the 4th-lane brainstorm (bare `can't start new thread`, `[Errno 11]` shape, `Runtime exited during hello with status -6`) all classified — no Errno-keyed-only matcher.
- Reconcile divergences with the banked U4 4b wording (U4 wins where they differ, per the execute-verbatim doctrine).
- Port the branch's test battery; reconcile fixtures against the current `_run_doctor` helpers; drop branch-obsolete expectations only where master's block shape supersedes them (lane-skew ordering).

**Execution note:** Rebase via `git` from the branch in the run workspace; conflicts resolved unit-locally; validation lineage from the branch is recorded in the fold evidence but gates re-run at the new base per R5.

**Patterns to follow:** the branch's own tests (they ARE the pattern); the existing lane-skew typed-verdict test shape; the corroboration window constants from the branch.

**Test scenarios:** (the branch's matrix, reconciled)
- Happy path: crash (`status -6`) then hello-timeout with recent corroborating historian/dream rows → one WARN row naming the remedy, rc 0, stdout contains `FAIL 0`.
- Error path: same crash pair with no recent store rows → FAIL row, rc 1 (teeth preserved on an idle store).
- Edge case: store rows exactly at the 24 h boundary → uncorroborated (conservative side).
- Error path: lane-skew pair → still the lane-skew typed FAIL (specificity order intact).
- Error path: post-hello failure → still FAIL (boot-window scope guard).
- Regression: existing wall-budget-edge and bind-contention WARN verdict tests unchanged.
- Integration: Unit 5's budget-exhausted-during-boot with corroboration → WARN (severity upgrade wiring between the two units).

**Verification:**
- The branch's behavior is fully present at master's base (diff audit: every branch test intent survives, reconciled or superseded with reason); both phase lanes' evaluator contracts hold (rc 0 + `FAIL 0` during corroborated transients).

### Phase 4 — validation + landing

- [ ] **Unit 7: Validation, attestation, and landing envelope**

**Goal:** Run the repository's full validation posture and assemble the honest fold envelope with engine-derived changed surfaces.

**Requirements:** R2, R4, R6, R8.

**Dependencies:** Units 1-6.

**Files:**
- No source changes. Deliverables: validation evidence in the fold envelope; census JSONL evidence reference; README ops notes already landed by Units 1-2.

**Approach:**
- Targeted: `tests/test_store_readability.py`, `tests/test_store_keepalive.py`, `tests/test_db_toolkit.py`, `tests/test_doctor_contract.py`, `tests/test_cli.py`, `tests/test_runtime.py`, `tests/test_bind_lock_race.py` (with `PYTHONPATH=src` in the run workspace; never the canonical venv's editable tree).
- Full battery: the repo's standard suite; static lane per repository doctrine (pre-existing base findings escalated, never fixed in-run; `uv.lock` reverted immediately if any `uv` invocation rewrites it).
- Live corroboration: `magic-hermes db keepalive --json` and `magic-hermes db census --json` runs from the run workspace recorded as evidence; `magic-hermes doctor --json` showing the readability row.
- Fold attestation: engine-derived changed surfaces (run branch vs base in the run workspace) — expect `src/magic_hermes/cli.py`, `src/magic_hermes/runtime.py`, `src/magic_hermes/db_toolkit.py`, `src/magic_hermes/store_keepalive.py`, `tests/test_store_readability.py`, `tests/test_store_keepalive.py`, `tests/test_db_toolkit.py`, `tests/test_doctor_contract.py`, `tests/test_cli.py`, `tests/test_runtime.py`, `README.md`, this plan document; declare exactly those and nothing from the unit worktree's wider footprint (KTD13 discipline).
- Closure-posture statement (review finding — closure-contract timing): the fold envelope MUST state the remedy's production activation state honestly — the systemd timer wiring (estate plane) and install convergence are what activate the keepalive in production, and the 3-PASS recurrence window (~10 min at the ~2-13 min evaluation cadence) completes before the estate can wire the timer. Credit options, in preference order: (a) schedule keepalive invocations during the observation window (the delegate or the engine runs `magic-hermes db keepalive` on the live store at intervals through the window), (b) state in the envelope that the recurrence guard is conditional on estate wiring + install convergence and re-verification is owed post-wiring. Never claim the guard passed on an inactive remedy.
- Landing: engine checkpoints attest; never amend; push non-forced; daemon-visible version verified at the evaluator's binary path.

**Test expectation:** none — envelope unit; the validation IS the test.

**Verification:**
- All targeted and full-battery runs green in the run workspace with the digest re-derived after any lock-file revert; the fold envelope's structured `validation_evidence` carries scope, command, covered surfaces, digest; the census JSONL exists outside the repo with ≥ the recorded observation window.

## System-Wide Impact

- **Interaction graph:** the keepalive adds a 15-min-cadence writable connection to the shared store (estate timer) — its remedies are WAL-bound enforcement (PASSIVE-only) and WAL-index freshness; it does NOT heal a live exclusive-lock holder (that family routes to the census/escalation gate). The doctor's new probe row runs inside every evaluation cycle (2 boots/cycle estate-wide). The outer bound changes the doctor's process lifecycle under pressure (SIGALRM-style path) but not its contract.
- **Error propagation:** keepalive failures are fail-loud rc 1 with remedy text (timer journal records them); the doctor's readability WARN propagates into both evaluation lanes' stdout without affecting their verdict formula (WARN keeps rc 0 + `FAIL 0`); the typed budget-exhausted row propagates through the same severity policy as boot-transients.
- **State lifecycle risks:** the keepalive itself must never become a lock holder (bounded busy-timeout, immediate txn, PASSIVE-only) — an unbounded keepalive would recreate the finding; the census writes only outside the repo; no schema changes anywhere (heartbeat shape constrained to no-op or existing tables).
- **API surface parity:** the `db` action group gains two actions with the existing `--json` conventions; no exported Python API changes; no config-format changes.
- **Integration coverage:** the stale-shm → keepalive → reader-replica chain test (Unit 3) is the cross-layer proof; the doctor readability row's live run is the operational proof.
- **Unchanged invariants:** maestro's reader contract (untouched by construction); the doctor's `FAIL 0`/rc-0 verdict contract; the pending_ops WARN family (the current "WARN 1" row); the bridge's OPS-03 bind semantics; the evaluator's doctor_ok formula.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Rebase conflicts in the verdict block (master's lane-skew vs branch's boot-transient) | Unit 6 reconciles with an explicit specificity order (skew → boot-transient → generic); branch tests port first as the behavioral spec |
| Keepalive becomes a new lock holder or write-pressure source | Bounded busy-timeout, no-op/immediate txn only, PASSIVE-only checkpoint, threshold-gated; integration test asserts concurrent-invocation safety |
| Census observes zero events in the run window (~1.5/day rate) | Census is non-gating for the capability units; fixtures (Unit 3, exclusive-hold repro) carry the deterministic regression; the closure claim then rests on fence + escalate posture, stated in the envelope |
| Census pins a live exclusive-lock holder OUTSIDE magic-hermes's estate | Contingency gate: escalate out-of-repo before fold — the zero-recurrence target is unreachable with this delta; the fold carries the census evidence and the escalation |
| Remedy inactive during the 3-PASS recurrence window (timer wiring is estate-side, window is ~10 min) | Fold envelope states activation state honestly; prefer scheduling keepalive runs through the observation window; recurrence-guard credit conditional on wiring + install convergence |
| Outer-bound mechanism interacts badly with threads (stderr reader, stdin lock) | Mechanism choice deferred with constraints; process-group reap on exit; expiry path does no new blocking I/O; dedicated edge-case tests for mid-read deadline fires |
| Doctor readability probe pressures the 75 s wall budget | 2 s probe budget (env-overridable), skipped when the wall is exhausted; latency asserted in tests |
| Static-lane pre-existing findings block the fold | Escalated per doctrine, never fixed in-run (digest discipline) |
| Install convergence: evaluator's binary path lags the repo | Landing checklist verifies the daemon-visible version at the evaluator's installed path; install/wiring refresh step in Unit 7 |
| Census evidence drifts the repo working tree (validation digest) | JSONL strictly under the XDG state dir; `git status` asserted clean in Unit 1 verification |

## Documentation / Operational Notes

- README §1b-2 extension (Unit 2): keepalive semantics, PASSIVE-only doctrine, estate timer example (15-min `OnUnitActiveSec`, `Persistent=true`, `Nice=10`), census usage and evidence location.
- The estate wiring itself (systemd unit creation) is an infra-plane step, documented but not repo delta — the fold envelope notes it as the operator action that activates the remedy in production.
- Ops escalation (carried from the brainstorm): host load/swap episodes amplify every lock window on this 10.6 GB store; raised as a note for the operator plane, no repo action.

## Sources & References

- **Origin document:** spool brainstorm artifacts `1c6eef7b1b5f4739a3a64f99e04d1891.approaches.md` and `1c6eef7b1b5f4739a3a64f99e04d1891-brainstorm-magic-context-state-unreadable.md` (conductor-delegate spool; untracked in this repo).
- Engineering context: `/home/agent/.local/state/maestro/engineering-context/9e0e4de641870b8b33524b47.json` (finding d59758598379, requirements R0-R7, closure contract).
- Evaluator contract (read-only reference): maestro `context.py` `ContextCollectors.magic()` — the frozen reader shape Units 1 and 3 replicate.
- Banked design: `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md` Unit 4 (gated U4, trigger fired).
- Adoption source: `conductor/run-9f3d6bce2fc6` (+ `da9a080`), base bfeb007, unlanded on master 2d1550c.
- Estate wiring precedent: `magic-context-watch.service`/`.timer` (systemd user units).
- Related plan: `docs/plans/1974d3889af2448e9e300eadb93f1440-plan-doctor-boot-transient-severity.md` (branch-side historical design; U4 wording wins on divergence).
