# Roadmap reconciliation — 2026-11

Dated, hand-owned record reconciling the roadmap store's open items with
shipped repository behavior, in the convention of
`docs/ROADMAP-RECONCILIATION-2026-10.md`. Recorded 2026-11-12 by conductor
run `609fd09da29f4192864391e40c1bca33` against master `12dded2`
(release v0.3.52 — this run's base; the 2026-10 record's `12dded2`
predecessors are cited where they matter). GitHub was unreachable this
session: every fact below was re-verified from the local checkout and local
git history (which already holds the exogenous release history through
`2534461`, release v0.3.55); gh-dependent legs are marked
offline-unverifiable rather than cited.

Why this lives under `docs/` rather than in `ROADMAP.md`: unchanged from the
2026-10 record — the roadmap renderer (hermes-roadmap `cli.py` `_cmd_render`)
rewrites `ROADMAP.md` wholesale from its store on every sync, the 2026-09-20
clobber incident (`docs/ROADMAP-CYCLE1.md`) established that durable records
belong on a path the renderer never writes, and nothing in this record edits
`ROADMAP.md`.

## 1. Purpose

Third reconciliation issuance in ~4 weeks (after runs `c28c8815` and
`67677da6`, recorded 2026-10-17 and landed as `195855a`/`914ef5b`): the
store's open-item set still diverges from shipped behavior, and the README
had again fallen behind the release train. This record (a) fixes the README
divergences it lands with, (b) verdicts all 14 open store items against
locally re-verifiable evidence, and (c) re-escalates the closure-channel gap
with fresh counts. It also closes the documentation side of the install-
example rot class durably: the release lane now carries the example forward
and a docs-consistency fence makes a stale example fail the battery that
gates every release and every PR (this run, §6).

## 2. README divergences fixed by this run

| # | Divergence | Fix (README anchor) |
|---|---|---|
| R1 | Install example (§Install step 3) cited wheel URLs and prose "shown for v0.3.20" — the third recorded recurrence of the example-rot class (2026-10 §2 R4, then §8), and a fourth occurred while this run was in flight: the exogenous trains v0.3.53/v0.3.54/v0.3.55 all shipped with the example still at v0.3.20 (verified: `git show 2534461:README.md` — 2 wheel URLs + prose at v0.3.20) | Example refreshed to v0.3.52 in all three sites (prose + both wheel URLs, both version slots); and the class is fenced shut — `scripts/release.py::set_version` now rewrites the example on every bump (`_update_readme_install_example`), enforced by `tests/test_docs_consistency.py::test_readme_install_example_matches_package_version` (§6) |
| R2 | The bind-lock concurrency lane shipped in v0.3.29 (`5f1d71c`, PR #102; doctor handshake `0d1b33e`, PR #108) with no README mention — operators hitting `database is locked` under parallel agents had no documented behavior | §Architecture now documents it in operator terms: bind mutex taken beside the durable SQLite store, bounded PID-publication grace, no age-based eviction of live holders, release-before-retry-cooldown, linking `docs/v0.3.29-bindlock-release.md` |
| R3 | The `private-leak-sentinel.yml` workflow (push/PR/daily full-history gitleaks via the fleet's reusable workflow) was undocumented — an explicit revisit of the 2026-10 §7 R6 decline, which this record supersedes: three issuances later, "the README is already long" is a weak rationale against an operator-facing gap in the verification story | §Verification now carries one sentence noting the sentinel workflow's full-history scan |

The docs-consistency fences continue to guard the cadence, pin, node-floor,
and adoption-policy claims verbatim; R1's fence is new (§6).

## 3. Verdict table — open store items vs shipped behavior

Statuses as rendered 2026-11-12 (`ROADMAP.md` at `12dded2`). Store-closed
items (`rm-002`, `rm-004`–`rm-011`) are not repeated here.

| item | store status | verdict | evidence |
|---|---|---|---|
| rm-001 — refactor 20 high-complexity functions | candidate | open | detector-live signals (`reliability.complexity_hot` on `bridge/runtime.mjs` et al.), recounted every cycle; no decomposition lane shipped |
| rm-003 — test coverage for 12 untested modules | candidate | open, stale signals | 10 of the 12 named modules now have test files that run in the CI battery: `test_cli`, `test_db_toolkit`, `test_engine`, `test_historian_guard`, `test_host_budget`, `test_memory_provider`, `test_plugin`, `test_provenance`, `test_runtime`, `test_wiring` (all present under `tests/`); residual = the two bridge modules `src/magic_hermes/bridge/loader.mjs` and `migrate-hooks.mjs` (no `.mjs` test lane exists) |
| rm-012 — v0.3.4 release/deploy chain + pending-version skip | in_progress | done (still store-stale) | pending-version refusal shipped and tested (`pending_release_version` in `scripts/release.py`, 11 references in `tests/test_release.py`); the release train has reached v0.3.52 at this record's base — and v0.3.55 exogenously since; deploy lane self-completing (`ad1651f`, PR #110). The 2026-10 verdict stands, still unreflected in the store |
| rm-013 — land PR #18 (count cached prompt tokens) | in_progress | done (still store-stale) | code presence re-verified locally: cached-token accounting in the engine path sums `cache_read_tokens`/`cache_write_tokens` into prompt pressure (`src/magic_hermes/engine.py:266-270`), with regression tests covering both provider shapes (`tests/test_engine.py:610-625`). PR-merged leg was verified via live `gh` on 2026-10-17; live PR state today is offline-unverifiable |
| rm-014 — deliberate 0.44.0 adoption + coordinated-upgrade runbook | candidate | superseded | the series went far past 0.44 — the pins now read 0.49.52 (`src/magic_hermes/magic_context_compat.json` `tested_version`; `package.json` dependency); coordination shipped as the adoption-PR mechanism itself (series-jump gate + `gh pr create` in `sync-magic-context.yml`, fenced by `TestSyncWorkflowGuardrails`); the README UPGRADE-section fragment is declined again (§7) |
| rm-015 — loader support for the 0.44.1 chunk layout | candidate | superseded | loader validated at 0.49.52 (compatibility manifest + green battery); the plain-export resolution landed as `6bc81a9` (PR #115) |
| rm-016 — sync-workflow guardrails | in_progress | done (one passive leg) | gate live and fenced: `TestSyncWorkflowGuardrails` pins `pull-requests: write`, the `series_jump` gate, the `gh pr create` adoption step, and gate-payload compilability against the workflow YAML (13 matching guardrail lines in `sync-magic-context.yml`); only "three consecutive scheduled runs green" keeps accruing passively — offline-unverifiable this session |
| rm-017 — doctor store-ahead verdict | candidate | superseded (deliberate design) | lane skew is a deliberate typed FAIL, not a WARN: README §1d — "the typed lane-skew FAIL — not discovery — remains the authoritative signal" (re-verified at base; quote intact through the current tree) |
| rm-018 — PR-triggered CI | in_progress | done (one untestable leg) | `ci.yml` runs on `pull_request` + `push: branches: [master]` + `workflow_dispatch`; the battery (including this docs-consistency file), ruff, and `node --check` all run there; "green required before merge" is branch protection, not locally verifiable |
| rm-019 — upstream ecosystem presence | candidate | open | external upstream-repo action (harness listing + dashboard row), unstarted; unreachable this session in any case |
| rm-020 — `magic-hermes status` stopgap | candidate | open | no `status` verb: `cli.py` sub-commands are install/wire/doctor/guard/db/provenance only; no decline record in `docs/PARITY.md` |
| rm-021 — CLI hygiene: dead conditional, backup retention | candidate | partially done | NEW since the 2026-10 record ("not shipped"): the retention leg landed 2026-10-31 in `0d1b33e` (PR #108) — wiring prunes `config.yaml.bak.<epoch>` siblings to the newest ten (`src/magic_hermes/wiring.py`, keep-newest-`keep` pruning at ~line 377) with `tests/test_wiring.py::test_backup_retention_keeps_newest_ten`. Remaining open: `N` is a code parameter fixed at ten, not operator-configurable, and the dead-conditional leg shows no removal/made-meaningful commit (`git log --grep dead` finds none) |
| rm-022 — hermetic historian tests | candidate | open | `ci.yml` still seeds the historian model config (ci.yml "Seed historian model config" step, lines 50-61); `docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md` stands as the institutional record |
| rm-023 — document runtime requirements | candidate | partially done | node-floor leg shipped 2026-10 and is fenced (`test_readme_node_floor_agrees_with_ci_pin` pins the README "Node.js N or newer" claim against ci.yml); the runtime-requirements note leg remains open — no "Runtime requirements" section exists in the README today (grep verified) |

## 4. Genuinely open — not closed by this record

- rm-001 — high-complexity functions: detector-live, unstarted.
- rm-003's residual — the two bridge `.mjs` modules.
- rm-019 — upstream ecosystem presence: external, unstarted.
- rm-020 — `magic-hermes status` stopgap: neither shipped nor declined.
- rm-021's remainder — operator-configurable retention N; dead conditional.
- rm-022 — hermetic historian tests: CI seed step still required.
- rm-023's remaining leg — the runtime-requirements note.

## 5. Structural finding — closure-channel gap (re-escalated, not fixed here)

Unchanged in kind from 2026-10 §5, now with worse numbers:

- `ROADMAP.md` remains renderer-owned; 13 more "managed ROADMAP.md render
  refresh" commits have landed since `195855a` (verified:
  `git rev-list --count 195855a..12dded2 --grep 'managed ROADMAP.md render
  refresh'` → 13), and none flipped a verdict.
- The store grew 121 rows (2026-10-17) → 175 rows (2026-11-12,
  `hermes-autonomy/runtime/roadmap-state/magic-hermes/items.jsonl`).
- rm-012 and rm-013 still render `in_progress` 26 days after the 2026-10
  record verdicted them done — behavioral proof that renders do not close
  items.
- The hermes-roadmap CLI verb set was re-verified 2026-11-12:
  infer / show / validate / expand / ledger / render / verify / repair
  (plus signature subcommands) — still **no item-close command**; the store's
  only closure path remains detector re-run reconciliation, which these
  items' detectors do not cover.

Consequence unchanged: items like rm-012/rm-013/rm-016 can never close
through in-repo work. Escalation remains with the autonomy estate
(this run's scope lock again forbids writing another project's operational
state; hand-editing `ROADMAP.md` remains a managed-marker contract
violation). This document is the interim durable record.

## 6. Durability leg — the install-example rot class, closed

The 2026-10 record noted the rot twice (§2 R4, §8) and left the lane
unchanged. This run ships the full closure, landing as one change:

- `scripts/release.py::set_version` rewrites the install example on every
  package-version bump (`_update_readme_install_example` +
  `README_WHEEL_URL_PATTERN`/`README_EXAMPLE_PROSE_PATTERN`): both wheel-URL
  version slots and the "shown for vX.Y.Z" prose move to the new version,
  self-healing from ANY previously-cited version; a missing README or zero
  example-shape matches is a silent no-op (mirroring
  `_update_readme_claims`'s tolerant posture). Both release lanes (manual
  `release.py` and the sync workflow's `--next-patch` train) traverse
  `set_version`.
- `tests/test_docs_consistency.py::test_readme_install_example_matches_package_version`
  fences it: every wheel-URL version slot and the prose version must exist
  and equal the pyproject package version. The battery runs in
  `validate_and_build()` BEFORE `commit_and_tag()`, so a stale example
  aborts a release before any commit exists — and CI runs it on every
  push/PR.
- Live proof the class was real until now: while this run was in flight, the
  exogenous release trains v0.3.53/v0.3.54/v0.3.55 each shipped with the
  README example still at v0.3.20 (`git show 2534461:README.md`). The first
  release that runs through the new `set_version` carries the example
  forward in the same commit as the version bump — the durable proof the
  class is closed.
- Operational note for the merge that lands this change: master's pyproject
  is already at v0.3.55 while this branch's example says v0.3.52 (green
  here: this branch's pyproject is v0.3.52). The merge must re-sync the
  example to the then-current package version in the merge commit (three
  sites, §Install step 3) — or master's push-triggered CI reddens until the
  next release train self-heals it.

## 7. Considered and declined

- Hand-editing `ROADMAP.md` to close stale items: managed-marker contract
  violation, guaranteed clobber; rejected (§5).
- Writing store closures or extending the hermes-roadmap CLI with a close
  command: touches another project's operational state; escalated instead
  (§5).
- Adoption-ledger backfill for same-series pins (0.49.4 → 0.49.52): the
  ledger's charter is series adoptions only; declined.
- A new README UPGRADE/coordination section (rm-014 fragment): declined
  again — the coordination behavior already ships where it is used (the
  series-jump gate and adoption-PR mechanism, §Compatibility and the sync
  workflow), and the README is not the runaway lane's bottleneck.
- Any CI or workflow YAML change (including the sentinel's): out of scope;
  the sentinel is documented (§2 R3), not modified.
- Re-verifying gh-dependent legs (live PR/Release state, branch protection,
  scheduled-run history) while offline: recorded as offline-unverifiable
  rather than cited from stale memory.

*Recorded 2026-11-12 by conductor run `609fd09da29f4192864391e40c1bca33`.
Facts verified against the local checkout at `12dded2` (v0.3.52) and local
git history through `2534461` (v0.3.55); GitHub unreachable this session —
gh-dependent legs are marked offline-unverifiable above. The README fixes
this record cites land with it, in the same run.*
