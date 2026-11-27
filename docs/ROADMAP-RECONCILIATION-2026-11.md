# Roadmap reconciliation — 2026-11

Dated, hand-owned record reconciling the roadmap store's open items with
shipped repository behavior, and the scheduled recurrence of the 2026-10
record (`docs/ROADMAP-RECONCILIATION-2026-10.md`, run `c28c881520a2409ea020d0b43779e6b2`,
2026-10-17). Recorded 2026-11-27 by conductor run
`4ba1734095db45f181abb85999e1119f` against base `f5b302b` (v0.3.74) on
`conductor/run-4ba1734095db`, with facts re-verified from the local checkout,
the roadmap store, and live `gh` where cited. Every verdict below is derived
fresh at this base; the 2026-10 verdicts are precedent, never carry-forward.

Why this lives under `docs/` rather than in `ROADMAP.md`: the roadmap renderer
(hermes-roadmap `cli.py` `_cmd_render`) rewrites `ROADMAP.md` wholesale from
its store on every sync — hand-written content there does not survive. The
2026-09-20 clobber incident (`docs/ROADMAP-CYCLE1.md`) established that durable
records belong on a path the renderer never writes. Nothing in this record
edits `ROADMAP.md`.

## 1. Purpose

The run's minting event (`github_docs_due` 2026-11-27T08:34:44Z,
`stale_docs: ["ROADMAP.md"]`, `current_docs: ["README.md"]`): `ROADMAP.md`'s
last commit is the 2026-11-25 managed render `c44dccb`, while the release
train ran ten releases (v0.3.66 → v0.3.75) across 2026-11-24 → 2026-11-27 —
the steward's two-day roadmap freshness clock fired. The rendered file is
store-faithful; the stale half is the render cadence itself (§5a) plus the
store content (§3/§5b), both estate-owned. This record is the in-repo half of
the reconciliation: every open store item re-verdicted, the two verified
README divergences fixed, and the estate-side findings escalated with fresh
evidence.

## 2. README divergences fixed by this run

| # | Divergence | Fix (README anchor) |
|---|---|---|
| D1 | Install example showed wheel URLs "for v0.3.20"; Latest release is v0.3.75 — third recurrence of the 2026-10 record's R4 class, predicted by its 2026-10-21 addendum | Example refreshed to the landing base's release tag (v0.3.74), same URL pattern; the hedge sentence ("newer versions follow the same URL pattern on the Releases page") stays verbatim — it is the designed release-proof answer between reconciliations. Landed by this run's even-lane unit (run plan U2) (§Install step 3) |
| D2 | §1b's doctor summary list named Node, distribution, discovery+pin, wiring, store presence + schema lane, handshake/quick_check/core-symbol — omitting the guard-status WARN row (§1c), the partial-coverage INFO row (§1c), and the provenance rows (§1d) that the README itself documents in depth | §1b's summary list now names the guard, partial-coverage, and provenance families — README-internal consistency, fixed in the same change as this run's new fence pinning it (§1b, §1c/§1d stay the depth sections) (run plan U3, this record's own change) |

The docs-consistency fences continue to guard the pinned phrases verbatim;
this run adds one fence to that family (D2 above, `tests/test_docs_consistency.py`).

## 3. Verdict table — open store items vs shipped behavior

Statuses as rendered at base `f5b302b` (14 open items). `rm-002` and
`rm-004`–`rm-011` remain store-closed and agree with shipped reality — no
divergence, not repeated here.

| item | store status | verdict | evidence |
|---|---|---|---|
| rm-012 — v0.3.4 release/deploy chain | in_progress | done | tag `v0.3.4` exists (local git); `release.py --deploy` shipped via `cae53bc` (PR #27, 2026-09-29, ancestor of base — re-verified by `git merge-base --is-ancestor`); the release train has since reached v0.3.75 |
| rm-013 — land PR #18 (cached tokens) | in_progress | done | `0e860ea` "fix(engine): count cached prompt tokens toward Magic Context pressure (#18)" is an ancestor of base (re-verified); the counting behavior stays maintained — PR #120 rescaled the token-only admission fixtures for 0.49.65+ on 2026-11-24 |
| rm-014 — deliberate 0.44.0 adoption + runbook | candidate | superseded | the series went 0.44.4 → 0.45.0 → … → 0.49.x; no 0.44.0 adoption ever ran; the coordination runbook shipped as the adoption-PR mechanism itself (migration-notes merge condition + documented rollback in PR bodies, `sync-magic-context.yml`), with historian_guard re-evaluations in the adoption ledger |
| rm-015 — loader support for 0.44.1 chunk layout | candidate | superseded | loader validated through 0.49.86 (compatibility manifest at base: `tested_version` 0.49.86, `supported_series` [0, 49]); the plain-export resolution for 0.49.35 message layouts landed as PR #115 (2026-11-02) |
| rm-016 — sync-workflow guardrails | in_progress | done | series-jump gate live (`40fe03b`, PR #39, 2026-10-01); PR-gated path exercised end to end repeatedly (PRs #41/#42/#46, later #72/#79, then #115/#120); the release train ran ten consecutive releases 2026-11-24 → 2026-11-27 on the same-series direct path — the "three consecutive scheduled runs green" leg is satisfied in substance and keeps accruing passively |
| rm-017 — doctor store-ahead WARN | candidate | superseded | recorded design decision (2026-10 §3, re-affirmed): lane skew is a deliberate typed FAIL — the README's migration-lane section still states "the typed lane-skew FAIL — not discovery — remains the authoritative signal" |
| rm-018 — PR-triggered CI | in_progress | done (one untestable leg) | ci.yml triggers on `pull_request` + `push` (verified at base): pinned-package battery, ruff, `node --check`; `tests/test_docs_consistency.py` exists and is green (9/9 at base); "green required before merge" is branch protection, not locally verifiable |
| rm-001 — 20 high-complexity functions | candidate | genuinely open | `src/magic_hermes/bridge/runtime.mjs` is 3,650 lines at base; the detector's complexity hotspots recount every cycle; recent bind-lock work (#101/#104/#108) keeps landing in the same file |
| rm-003 — 12 untested modules | candidate | false-positive drift | 10 of the 12 listed modules have test files at base (`ls tests/`: `_host_budget`, `cli`, `db_toolkit`, `engine`, `historian_guard`, `memory_provider`, `plugin`, `provenance`, `runtime`, `wiring`); the two without — `bridge/loader.mjs`, `bridge/migrate-hooks.mjs` — are JavaScript modules covered by `node --check` (CI gate) and the integration battery; the acceptance's Python-test-file framing does not map onto `.mjs` modules (§5c) |
| rm-019 — upstream ecosystem presence | candidate | genuinely open | no dashboard/harness-listing row exists in `docs/PARITY.md` at base; the external upstream-repo action is unstarted |
| rm-020 — `magic-hermes status` stopgap | candidate | genuinely open | no top-level `status` subcommand at base (cli.py subparser census: install, wire, doctor, guard, db, provenance); PARITY.md's status-UI row ("Pi/OpenCode status line / command UI | N/A") predates the 2026-10 record (`9e0c814`, 2026-08-20) and documents the presentation-only boundary but does not cite the rm-010 pivot / host command-surface limit the acceptance's decline arm asks for — neither arm is satisfied |
| rm-021 — CLI hygiene (dead conditional, backup retention) | candidate | partially done | backup-retention leg SHIPPED since the 2026-10 record ("not shipped" then): `wiring.py` keeps `BACKUP_KEEP = 10` with `_prune_backups` (comment cites "plan W3"), with tests (`test_wiring.py`: `test_backup_retention_keeps_newest_ten`, `test_prune_never_deletes_foreign_backup_siblings`) — real code + tests landed with no store status movement (§5b's freshest evidence); the dead-conditional leg has no named site in the record and no removal evidence found — open |
| rm-022 — hermetic historian tests | candidate | genuinely open | ci.yml still seeds user-level CortexKit historian config (workflow lines 50–61) so the battery is not hermetic on a bare machine; the HOME-isolation leg remains |
| rm-023 — document runtime requirements | candidate | partially done | node-floor leg done and fenced (README Requirements: "Node.js 24 or newer"; `test_readme_node_floor_agrees_with_ci_pin` pins it to ci.yml); the historian-config/readiness-note leg (optional user-level CortexKit config, what readiness checks do without it — historian disabled, sidecar otherwise functional — and the solutions-doc link) is still absent from the README at base |

## 4. Genuinely open — not closed by this record

- rm-001 — high-complexity functions: detector-live signals, recounted every cycle.
- rm-019 — upstream ecosystem presence: external upstream-repo action, unstarted.
- rm-020 — `magic-hermes status` stopgap: neither the CLI arm nor the
  acceptance's specific decline-citation arm is satisfied (§3).
- rm-021's dead-conditional leg (§3).
- rm-022 — hermetic historian tests: the ci.yml historian-config seed remains.
- rm-023's remaining historian-config leg (§3).

## 5. Structural findings (escalated, not fixed here)

### 5a. Render-stoppage — the fresh half of this mint

The renderer's commit cadence on `ROADMAP.md` (git history, re-verified):

- 22 near-daily render commits 2026-09-30 → 2026-11-06, timestamps
  ~00:00–01:30 UTC (a daily-cadence pass).
- **19-day silence** 2026-11-06 → 2026-11-25.
- One render 2026-11-25 00:45 (`c44dccb`) — whose diff vs the prior render is
  cosmetic only (track relabels + backticks): the store's open set was
  essentially unchanged.
- **No render since**: the remote `ROADMAP.md` still sits at `c44dccb` at
  record time (live `gh`, 2026-11-27), while remote master is v0.3.75
  (`9026f547`, 2026-11-27T16:32:06Z) and the run base f5b302b (v0.3.74) is
  one release behind live master.

The store itself keeps churning — `items.jsonl` mtime 2026-11-27 00:26
(203 rows; the latest rows are rm-001/rm-003 detector recounts) — so the
render is stale relative to its own store, not just to the code. Two open
hypotheses, resolvable only estate-side (hermes-autonomy roadmap-sync):

1. the renderer pass stopped running (process/cadence regression), or
2. the renderer ran but produced no output delta and thus no commit — a
   no-op render leaves no in-repo trace by construction.

Distinguishing them requires roadmap-sync run logs, which live outside this
repository. Escalated as an estate-side incident with this timeline as the
evidence base. Nothing in this repo can restore the render cadence; this
record exists precisely because the in-repo half of the reconciliation must
not wait for it.

### 5b. Closure-channel gap — re-affirmed with 40-day-newer evidence

The 2026-10-17 escalation stands, and has aged:

- `ROADMAP.md` remains renderer-owned; the hermes-roadmap CLI still exposes
  infer / show / validate / expand / ledger / render / verify / repair —
  **no item-close command**.
- Campaign-evidenced items still carry the circular evidence expectation
  "campaign-recorded in magic-hermes ROADMAP.md" — a file the renderer owns.
- rm-012/rm-013/rm-016 are still `in_progress` (rm-018 same) although done —
  re-verified at this base, 40 days after the first escalation.
- The store grew 121 → 203 rows without a single closure of a done item.
- **Freshest evidence**: rm-021's backup-retention leg shipped real code and
  tests after the 2026-10 record (§3) with zero store status movement — the
  gap is not merely historical residue, it is live.

Consequence unchanged: items like rm-012/rm-013/rm-016 can never close through
in-repo work; the rendered `ROADMAP.md` stays store-stale until the estate
adds a closure channel. This run's scope lock still forbids writing another
project's operational state. This document remains the interim durable record.

### 5c. no_tests detector false-positive (rm-003)

The store recounts "12 untested modules" every cycle from a signal list whose
Python-test-file mapping is 10/12 satisfied at base; the two unsatisfied
entries are `.mjs` bridge modules — JavaScript, already covered by the CI
`node --check` gate and the integration battery. The acceptance's
"corresponding test file with at least one passing test" cannot be satisfied
in its named sense for `.mjs` modules, so the item can never close as
written. Estate-side fix (detector change, not this repo): skip `.mjs`
modules or map them to the `node --check` gate. Recorded here so the recount
is not read as an actual coverage gap.

## 6. Series-adoption ledger currency (0.49.5 → 0.49.86)

The append-only adoption ledger (`docs/UPSTREAM-SERIES-ADOPTIONS.md`) records
series jumps only. The last recorded jump is 0.47.0 → 0.49.4 (PR #84,
2026-10-16; v0.3.15 between, v0.3.16 then moved 0.49.4 → 0.49.5 same-series).
Since then, through the pin at base — 0.49.86 — every pin move stayed
same-series (manifest `supported_series` [0, 49]) and landed on the direct
same-series release path (ten releases 2026-11-24 → 2026-11-27 are the most
recent). **The ledger is current by its own charter; no backfill entry is
required.** The 2026-10 record's §6 observation (series jumps historically
landing outside the workflow's PR-gated path) has no new instance — no series
jump has occurred since 2026-10-16.

## 7. Doctor-closure representation gap (new since the 2026-10 record)

The Magic Context doctor closure-confirmation saga (finding `c7d63424`,
maestro `phase2.magic-context.operational`) now has shipped in-repo
representation, but the roadmap store knows none of it:

- `docs/plans/2026-11-24-001-fix-magic-context-doctor-closure-confirmation-plan.md`
  landed in-tree via PR #127 (merged 2026-11-27T02:17:09Z; single file) and
  the master merge `d1a34df` (run `7e67635cad8948e1bf45911b5a7dfc5c`
  integration): the banked zero-delta closure-confirmation plan with the
  gated structural contingency (U4).
- The lane-6 remediation salvage (typed failure-taxonomy + boot-capacity WARN
  family, ~755 insertions across `cli.py`, `runtime.py`,
  `test_doctor_contract.py`, `test_runtime.py`, and a README paragraph) sits
  **un-folded** on `conductor/run-645bbb3d4f00` at `4c59be86` (local and
  remote; zero salvage markers at base HEAD).
- Gated U4 (bounded doctor + liveness-corroborated severity + env-budget
  regression tests + install convergence) remains banked in the plan doc for
  finding re-open, per that run's stewardship contract.
- No roadmap store item references the finding, the plan doc, or the salvage
  branch — there is no in-repo channel to create one (§5b).

This section is that representation: a future reconciliation (or the estate's
closure channel) should treat the c7d63424 lane as delivered-and-gated,
not absent.

## 8. Considered and declined

- Bindlock README paragraph (the #88/#101/#104 mutex/theft/owner-identity
  guards): boot-internals; depth already lives in
  `docs/v0.3.29-bindlock-release.md`; declined — revisit if operators report
  hitting bindlock errors blind.
- `release.py` install-example auto-refresh (ends the R4/D1 recurrence class
  at the source): a release-lane behavior change, explicitly deferred by the
  2026-10-21 addendum; separate future change.
- Hand-editing `ROADMAP.md` to close stale items, or writing store closures,
  or extending the hermes-roadmap CLI: managed-marker contract violation /
  another project's operational state; rejected (§5).
- An "example == latest" docs-consistency pin for the install example: breaks
  the hourly release train by construction; the hedge sentence is the
  designed release-proof answer; rejected.
- rm-023's historian-config leg, rm-022's hermeticity, rm-001's complexity
  decomposition, rm-019's upstream action, rm-020's status CLI: open work
  items outside this run's scope lock.

*Recorded 2026-11-27 by conductor run `4ba1734095db45f181abb85999e1119f`
against base `f5b302b` (v0.3.74) on `conductor/run-4ba1734095db`. Facts
verified against the local checkout, the roadmap store at
`/work/projects/hermes-autonomy/runtime/roadmap-state/magic-hermes/items.jsonl`
(203 rows, mtime 2026-11-27 00:26), and live `gh` (remote master `9026f547`
= v0.3.75; remote `ROADMAP.md` still `c44dccb`). The 2026-10 record remains
authoritative for its window; its addendum conventions apply here.*
