# Roadmap reconciliation — 2026-10

Dated, hand-owned record reconciling the roadmap store's open items with
shipped repository behavior. Recorded 2026-10-17 by conductor run
`c28c881520a2409ea020d0b43779e6b2` against master `793ad78`, with facts
re-verified from the local checkout and live `gh` where cited.

Why this lives under `docs/` rather than in `ROADMAP.md`: the roadmap renderer
(hermes-roadmap `cli.py` `_cmd_render`) rewrites `ROADMAP.md` wholesale from
its store on every sync — hand-written content there does not survive. The
2026-09-20 clobber incident (`docs/ROADMAP-CYCLE1.md`) established that durable
records belong on a path the renderer never writes; this file follows the same
convention as `docs/UPSTREAM-SERIES-ADOPTIONS.md`. Nothing in this record edits
`ROADMAP.md`.

## 1. Purpose

The roadmap store's open-item set had drifted from shipped reality: items whose
acceptance was already satisfied by merged work remained `candidate` or
`in_progress`. This record gives every open store item a verdict against
verifiable repository evidence, so the reconciliation is durable in-repo even
though the rendered `ROADMAP.md` itself remains store-faithful (and therefore
store-stale) until the closure-channel gap in §5 is fixed estate-side.

## 2. README divergences fixed by this run

| # | Divergence | Fix (README anchor) |
|---|---|---|
| R1 | Status and "Compatibility and failure behavior" described the sync as auto-adopting every new core release; the shipped series-jump gate (resolve-step refusal, PR-gated adoption PR, `allow_series_jump` dispatch confirmation) was undocumented except for a parenthetical in the migration-lane section | Both sections now document the two paths: same-series patches direct-release, series jumps are PR-gated (§Status, §Compatibility) |
| R2 | Requirements said only "Node.js on `PATH`"; the bridge runtime needs `node:sqlite`, usable without `--experimental-sqlite` only on node >= 23 (CI validates on node 24) | Requirements documents the node 23 floor; `tests/test_docs_consistency.py` pins the claim against ci.yml (§Requirements) |
| R3 | Verification showed `ruff check src tests`; ci.yml actually runs `ruff check src tests scripts` | Command matches ci.yml (§Verification) |
| R4 | Install example showed wheel URLs "for v0.3.6"; Latest release is v0.3.16 | Example refreshed to v0.3.16, same URL pattern (§Install step 3) |

The docs-consistency fences continue to guard the pinned phrases
"newest stable core release" and "daily at 00:07 UTC" verbatim.

## 3. Verdict table — open store items vs shipped behavior

Statuses as rendered 2026-10-17. `rm-002` and `rm-004`–`rm-011` are already
store-closed and agree with shipped reality — no divergence, not repeated here.

| item | store status | verdict | evidence |
|---|---|---|---|
| rm-012 — v0.3.4 release/deploy chain | in_progress | done | tag `v0.3.4` exists (`2124396`); deploy gap closed by `release.py --deploy` (`cae53bc`, PR #27, 2026-09-29); the release train has since reached v0.3.16 |
| rm-013 — land PR #18 (cached tokens) | in_progress | done | PR #18 MERGED 2026-09-29T19:05:34Z (gh, re-verified 2026-10-17) |
| rm-014 — deliberate 0.44.0 adoption + runbook | candidate | superseded | the series went 0.44.4 → 0.45.0 → 0.47.0 → 0.49.x; coordination shipped as the adoption-PR mechanism itself (migration-notes merge condition and documented rollback in the PR body, `sync-magic-context.yml`); historian_guard re-evaluations recorded in the adoption ledger |
| rm-015 — loader support for 0.44.1 chunk layout | candidate | superseded | loader validated at 0.49.5 (compatibility manifest + green battery); export synthesis for the 0.45.x folded/renamed exports landed earlier (`b4e8b78`) |
| rm-016 — sync-workflow guardrails | in_progress | done (one passive leg) | series-jump gate repaired and live (`40fe03b`, 2026-09-30, PR #39); PR-gated path exercised end to end (PRs #41/#42/#46, later #72/#79); rollback documented in PR bodies and the ledger; only "three consecutive scheduled runs green" keeps accruing passively |
| rm-017 — doctor store-ahead WARN | candidate | superseded | recorded design decision: lane skew is a deliberate typed FAIL — README §1b-2: "the typed lane-skew FAIL — not discovery — remains the authoritative signal" |
| rm-018 — PR-triggered CI | in_progress | done (one untestable leg) | ci.yml runs on pull_request + push(master): pinned-package battery, ruff, `node --check`; `tests/test_docs_consistency.py` exists; "green required before merge" is branch protection, not locally verifiable |
| rm-023 — document runtime requirements | candidate | partially done by this run | node-floor leg landed here (README §Requirements + the docs-consistency pin); the historian-config/readiness-note leg (optional user-level CortexKit config, readiness checks without it, solutions-doc link) remains open |

## 4. Genuinely open — not closed by this record

- rm-001 — high-complexity functions: detector-live signals, recounted every cycle.
- rm-003 — untested modules: detector-live signals, recounted every cycle.
- rm-019 — upstream ecosystem presence: external upstream-repo action, unstarted.
- rm-020 — `magic-hermes status` stopgap: no top-level status subcommand shipped
  (only guard/db sub-commands); no PARITY record of the decline.
- rm-021 — CLI hygiene (dead conditional, backup retention): not shipped.
- rm-022 — hermetic historian tests: ci.yml still seeds historian config; the
  non-hermetic leg remains.
- rm-023's remaining leg (§3).

## 5. Structural finding — roadmap closure-channel gap (escalated, not fixed here)

The stale half of §3 cannot be fixed by any in-repo action:

- `ROADMAP.md` is renderer-owned: `_cmd_render` writes it unconditionally from
  the store (`/work/projects/hermes-autonomy/runtime/roadmap-state/magic-hermes/items.jsonl`,
  121 rows at 2026-10-17), and the autonomy-side clobber guard preserves only
  files without the managed marker.
- The hermes-roadmap CLI exposes infer / show / validate / expand / ledger /
  render / verify / repair — **no item-close command**.
- Campaign-evidenced items carry the circular evidence expectation
  "campaign-recorded in magic-hermes ROADMAP.md": the renderer owns that file,
  so the expectation can never be satisfied from inside this repository.
- The store's only closure path is detector re-run reconciliation
  (`builder.py`), which these items' detectors do not cover.

Consequence: items like rm-012/rm-013/rm-016 can never close through in-repo
work; the rendered `ROADMAP.md` stays store-stale until the estate adds a
closure channel. Escalated to the autonomy estate as a separate work item
(this run's scope lock forbids writing another project's operational state;
hand-editing `ROADMAP.md` is a managed-marker contract violation and was
already disproven by the 2026-09-20 incident). This document is the interim
durable record.

## 6. Series-adoption provenance 0.45.0 → 0.49.5 (since the ledger's last entry)

The append-only adoption ledger (`docs/UPSTREAM-SERIES-ADOPTIONS.md`) last
recorded 0.45.0 (PR #46, merge `f409f8b`, 2026-10-06). Pin/series deltas since,
re-verified from local git (`git diff <c>^ <c> -- package.json
src/magic_hermes/magic_context_compat.json`; release tags `v0.3.12`…`v0.3.16`
pair as listed):

| date (UTC) | pin delta | landed via | notes |
|---|---|---|---|
| 2026-10-09 06:19 | 0.45.0 → 0.46.0 (series 45→46) | `47f8e53` — run-integration merge ("autonomously integrate run bfb632ff…"), not the sync's adoption path | v0.3.12 followed same-day and moved 0.46.0 → 0.46.1 same-series (`82b7395`) |
| 2026-10-10 09:05 | 0.46.1 → 0.47.0 (series 46→47) | `ae1a7af` — release v0.3.13 carried the series jump; authored and committed by `agent <agent@agent-runner.local>` (a local release-train invocation of `scripts/release.py`, not the workflow, whose automated commits are `github-actions[bot]`) | PR #72 "sync: adopt Magic Context v0.47.0" merged 2026-10-12 (`894b2ca`) as an empty delta — single parent `0bc5b10`, identical tree `b899f59c`, zero file changes — the content was already on master |
| 2026-10-14 10:39 | pin unchanged (0.47.0) | `f0cff73` — PR #79 "Adopt Magic Context v0.49.0 (series bump, PR-gated)" | bridge-only: `loader.mjs` +73/`runtime.mjs` +35 lines; package.json and manifest untouched |
| 2026-10-15 20:56 | 0.47.0 → 0.49.4 (series 47→49, skipping 0.48) | `386bbad` — PR #84 (agent-authored PR carried the pin and bridge tests) | v0.3.15 (`9a82a7f`) between; v0.3.16 (`c717e43`, 2026-10-16) then moved 0.49.4 → 0.49.5 same-series |

Observation (incident record, not normative text): since the gate repair
(`40fe03b`, 2026-09-30), no series jump has actually landed through the sync
workflow's own PR-gated path — 46 arrived via a run-integration merge, 47 via a
local agent release train, and 49 via an agent-authored PR. The README
documents the designed gate; this section records how the pins actually moved.
The 2026-10-10 event is the sharpest of the three: the resolve step's refusal
gates the workflow's own paths, and a local `release.py` invocation never
traverses it. It is disclosed here as an operational observation and escalated;
this run changes no gate code (scope lock). The per-adoption entries, workflow
run traces, and historian_guard re-evaluation verdicts for these jumps are
recorded in the adoption ledger.

## 7. Considered and declined

- R5 — rewording Status "Working development preview": subjective judgment, not
  a factual divergence; declined.
- R6 — documenting `private-leak-sentinel.yml` (including the `793ad78`
  permission/gitleaks.tmp fixes): internal CI hygiene; the README is already
  549 lines; declined.
- Hand-editing `ROADMAP.md` to close stale items: managed-marker contract
  violation, guaranteed clobber by the near-daily renderer refresh; rejected
  (see §5).
- Writing store closures or extending the hermes-roadmap CLI with a close
  command: touches another project's operational state; escalated instead
  (see §5).

*Recorded 2026-10-17 by conductor run `c28c881520a2409ea020d0b43779e6b2`.
Facts verified against the local checkout at `793ad78` and live `gh` where
cited; the adoption-ledger entries it references are appended by the same run
per that ledger's append-only charter.*
