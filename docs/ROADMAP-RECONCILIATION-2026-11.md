# Roadmap reconciliation — 2026-11

Dated, hand-owned record reconciling the roadmap store's open items with
shipped repository behavior, in the convention of
`docs/ROADMAP-RECONCILIATION-2026-10.md`. Recorded 2026-11-17 by conductor
run `b385c6c7d8dd4f3ca318d70c39dfe80d` against this run's base `068b013`
(release v0.3.57), with the run's Unit-1 release-lane change present on the
same integration branch (merge `36b4a65`) — the README fixes §2 cites land
with this record, in the same run. Local git history already holds the
exogenous release train through `73ad3f7` (v0.3.59); `gh` was reachable this
session and live citations are marked as such. It supersedes a never-landed
2026-11-12 draft of the same filename (run `609fd09da29f`, whose lane landed
zero delta): every row here was re-verified against the 2026-11-17 tree, and
that draft's fence reference named a variant that did not ship (§6 cites the
shipped one).

Why this lives under `docs/` rather than in `ROADMAP.md`: unchanged from the
2026-10 record — the roadmap renderer (hermes-roadmap `cli.py` `_cmd_render`)
rewrites `ROADMAP.md` wholesale from its store on every sync, the 2026-09-20
clobber incident (`docs/ROADMAP-CYCLE1.md`) established that durable records
belong on a path the renderer never writes, and nothing in this record edits
`ROADMAP.md`.

## 1. Purpose

Fourth reconciliation issuance in ~5 weeks (2026-10-17 `c28c8815`, landed as
`195855a`/`914ef5b`; the 2026-10-21 addendum `6aac093a`; a 2026-11-12 draft
that never landed; this record): the store's open-item set still diverges
from shipped behavior, and the README had again fallen behind the release
train — the install example still pinned v0.3.20 while the train reached
v0.3.59 during this run. This record (a) ships the README divergences it
lands with (§2, this run's Unit 1), (b) verdicts all 14 open store items
against re-verified evidence (§3), (c) re-escalates the closure-channel gap
with fresh counts (§5), and (d) records the 0.49.5 → 0.49.57 same-series
provenance delta (§7).

## 2. README divergences fixed by this run

| # | Divergence | Fix (README anchor) |
|---|---|---|
| R1 | Install example (§Install step 3) cited wheel URLs and prose "shown for v0.3.20" — the third recorded recurrence of the example-rot class (2026-10 §2 R4; the 2026-10-21 §8 addendum, which refreshed the example to v0.3.20; the never-landed 2026-11-12 draft), and the class recurred again while this run was in flight: exogenous trains v0.3.58 (`fbf5a98`) and v0.3.59 (`73ad3f7`) shipped with the example still at v0.3.20 (`git show 73ad3f7:README.md`) — 39 shipped releases past the example at record time | Example refreshed to v0.3.58 in all three sites (prose `README.md:378`; wheel URLs `:383` and `:390` — targeting master-at-implement-time `fbf5a98` per the fence's two-anchor rule, §6); the class is fenced shut — `scripts/release.py::set_version` rewrites the example on every bump (`_update_readme_install_example`), enforced by `tests/test_docs_consistency.py::test_readme_install_example_matches_shipped_version` (§6) |
| R2 | The bind-lock concurrency lane shipped in v0.3.29 (`5f1d71c`, PR #102; doctor handshake `0d1b33e`, PR #108) with no README mention — operators hitting `database is locked` under parallel agents had no documented behavior | §Architecture now documents it in operator terms (`README.md:46-51`): bind mutex taken beside the durable SQLite store (never under scratch storage), bounded PID-publication grace, no age-based eviction of live holders, release-before-retry-cooldown, doctor sidecar deferring the shared-store bind — linking `docs/v0.3.29-bindlock-release.md` |
| R3 | The patch-bump rule was illustrated with `0.2.0` to `0.2.1` (`README.md:512` at base) — an era two minor series and ~40 releases gone | Re-exemplified at the current era: `0.3.57` to `0.3.58` (`README.md:522`) |
| R4 | `ROADMAP.md` store-stale by construction: the renderer rewrites it from the store, whose open-item set diverges from shipped behavior (§3, §5); no in-repo edit can close items | This record itself is the durable channel (§1, §5); `ROADMAP.md` is byte-identical after this run (verified: no `ROADMAP.md` entry in either worktree's delta) |

The docs-consistency fences continue to guard the cadence, pin, node-floor,
and adoption-policy claims verbatim; R1's fence is new (§6).

## 3. Verdict table — open store items vs shipped behavior

Statuses as rendered by `7063695` (2026-11-06 render — still the live render
at record time; `ROADMAP.md` unchanged since). Store-closed items (`rm-002`,
`rm-004`–`rm-011`) are not repeated here. All line anchors re-verified
against the run's integration tree (`36b4a65`).

| item | store status | verdict | evidence |
|---|---|---|---|
| rm-001 — refactor 20 high-complexity functions | candidate | open | detector-live signals (`complexity_hot` on `bridge/runtime.mjs` L104/L140/L189/L251 +15 more per the store row), recounted every cycle; no decomposition lane shipped |
| rm-003 — test coverage for 12 untested modules | candidate | open, stale signals | 10 of the 12 named modules have passing test files under `tests/`: `test_cli`, `test_db_toolkit`, `test_engine`, `test_historian_guard`, `test_host_budget`, `test_memory_provider`, `test_plugin`, `test_provenance`, `test_runtime`, `test_wiring`; residual = the two bridge modules `src/magic_hermes/bridge/loader.mjs` and `migrate-hooks.mjs` (no `.mjs` test lane exists) |
| rm-012 — v0.3.4 release/deploy chain + pending-version skip | in_progress | done (still store-stale) | pending-version refusal shipped and tested (`pending_release_version`: 2 refs in `scripts/release.py`, 11 in `tests/test_release.py`); the train has reached v0.3.57 at this run's base and v0.3.59 exogenously; deploy lane self-completing (`ad1651f`, PR #110, 2026-10-31). The 2026-10 verdict stands, still unreflected in the store — 31 days and counting |
| rm-013 — land PR #18 (count cached prompt tokens) | in_progress | done (still store-stale) | cached-token accounting in the engine path sums `cache_read_tokens`/`cache_write_tokens` into prompt pressure (`src/magic_hermes/engine.py:270`), with regression tests covering both provider shapes (`tests/test_engine.py:622`); **PR #18 MERGED 2026-09-29T19:05:34Z — re-verified via live `gh` 2026-11-17** |
| rm-014 — deliberate 0.44.0 adoption + coordinated-upgrade runbook | candidate | superseded | the series went far past 0.44 — pins now read 0.49.57 (`magic_context_compat.json` `tested_version`; `package.json` dependency) at base, 0.49.60 exogenous at v0.3.59; coordination shipped as the adoption-PR mechanism itself (series-jump gate + `gh pr create` in `sync-magic-context.yml`, fenced by `TestSyncWorkflowGuardrails`, `tests/test_docs_consistency.py:256`); the README UPGRADE-section fragment is declined again (§8) |
| rm-015 — loader support for the 0.44.1 chunk layout | candidate | superseded | loader validated at 0.49.57 (compatibility manifest + green battery); the plain-export resolution landed as `6bc81a9` (2026-11-01, PR #115) |
| rm-016 — sync-workflow guardrails | in_progress | done | gate live and fenced: `TestSyncWorkflowGuardrails` (`tests/test_docs_consistency.py:256`) pins `pull-requests: write`, the `series_jump` gate, the `gh pr create` adoption step, and gate-payload compilability (13 matching guardrail lines in `sync-magic-context.yml`); the "three consecutive scheduled runs green" leg is now positively verified — scheduled runs 2026-11-17 03:57 / 07:43 / 13:33 UTC all `success` (plus green `repository_dispatch` runs 04:50 / 13:36 / 14:51 UTC), live `gh run list` 2026-11-17 |
| rm-017 — doctor store-ahead verdict | candidate | superseded (deliberate design) | lane skew is a deliberate typed FAIL, not a WARN: README — "the typed lane-skew FAIL — not discovery — remains the authoritative signal" (`README.md:348`, quote intact after Unit 1's edits) |
| rm-018 — PR-triggered CI | in_progress | done (one untestable leg) | `ci.yml` runs on `pull_request` + `push: branches: [master]` + `workflow_dispatch` (`ci.yml:4-7`); the battery (including the docs-consistency file), ruff, and `node --check` run there; "green required before merge" is branch protection, not locally verifiable |
| rm-019 — upstream ecosystem presence | candidate | open | external upstream-repo action (harness listing + dashboard row), unstarted |
| rm-020 — `magic-hermes status` stopgap | candidate | open | no `status` verb: `cli.py` sub-commands are install/wire/doctor/guard/db/provenance only (`cli.py:1514-1590`); no decline record in `docs/PARITY.md` |
| rm-021 — CLI hygiene: dead conditional, backup retention | candidate | partially done | the retention leg landed 2026-10-31 in `0d1b33e` (PR #108): wiring prunes `config.yaml.bak.<epoch>` siblings to the newest ten (`src/magic_hermes/wiring.py:376`, `_prune_backups(keep=BACKUP_KEEP)`) with `tests/test_wiring.py:381`. Remaining open: `N` is a code parameter fixed at ten, not operator-configurable, and the dead-conditional leg shows no removal/made-meaningful commit (`git log --grep dead` finds none touching it) |
| rm-022 — hermetic historian tests | candidate | open | `ci.yml` still seeds the historian model config ("Seed historian model config" step, `ci.yml:50`); `docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md` stands as the institutional record |
| rm-023 — document runtime requirements | candidate | partially done | node-floor leg shipped 2026-10 and is fenced (`test_readme_node_floor_agrees_with_ci_pin`, `tests/test_docs_consistency.py:234`, pins the README "Node.js 24 or newer" claim at `README.md:58` against `ci.yml`); the runtime-requirements note leg remains open — no "Runtime requirements" section exists in the README (grep: 0 matches) |

## 4. Genuinely open — not closed by this record

- rm-001 — high-complexity functions: detector-live, unstarted.
- rm-003's residual — the two bridge `.mjs` modules.
- rm-019 — upstream ecosystem presence: external, unstarted.
- rm-020 — `magic-hermes status` stopgap: neither shipped nor declined.
- rm-021's remainder — operator-configurable retention N; dead conditional.
- rm-022 — hermetic historian tests: CI seed step still required.
- rm-023's remaining leg — the runtime-requirements note.

## 5. Structural finding — closure-channel gap (re-escalated, not fixed here)

Unchanged in kind from 2026-10 §5, with fresh numbers:

- `ROADMAP.md` remains renderer-owned; 13 "managed ROADMAP.md render refresh"
  commits have landed since `195855a` (verified:
  `git rev-list --count 195855a..36b4a65 --grep 'managed ROADMAP.md render
  refresh'` → 13), and none flipped a verdict. The render lane has itself
  gone quiet: the last render is `7063695` (2026-11-06) — 11 days before this
  record — while the store kept growing underneath it.
- The store grew 121 rows (2026-10-17) → 175 rows (2026-11-12) → 183 rows
  (2026-11-17, `hermes-autonomy/runtime/roadmap-state/magic-hermes/items.jsonl`).
- rm-012 and rm-013 still render `in_progress` 31 days after the 2026-10-17
  record verdicted them done — behavioral proof that renders do not close
  items.
- The hermes-roadmap CLI verb set was re-verified 2026-11-17:
  infer / show / validate / expand / ledger / verify / repair / render /
  signals-check / signals-publish — still **no item-close command**; the
  store's only closure path remains detector re-run reconciliation, which
  these items' detectors do not cover.

Consequence unchanged: items like rm-012/rm-013/rm-016 can never close
through in-repo work. Escalation remains with the autonomy estate
(this run's scope lock again forbids writing another project's operational
state; hand-editing `ROADMAP.md` remains a managed-marker contract
violation). This document is the interim durable record.

## 6. Durability leg — the install-example rot class, closed

The 2026-10 record noted the rot twice (§2 R4 lineage, §8 addendum) and left
the lane unchanged; the never-landed 2026-11-12 draft designed the closure
around a fence variant that did not ship. This run ships the full closure,
landing as one atomic change (its Unit 1):

- `scripts/release.py::set_version` rewrites the install example on every
  package-version bump (`_update_readme_install_example`,
  `scripts/release.py:136`, called at the `set_version` tail `:133`, using
  `README_WHEEL_URL_PATTERN` `:43` / `README_EXAMPLE_PROSE_PATTERN` `:46`):
  both wheel-URL version slots and the "shown for vX.Y.Z" prose move to the
  new version, self-healing from ANY previously-cited version; a missing
  README or zero example-shape matches is a silent no-op (mirroring
  `_update_readme_claims`'s tolerant posture). Both release lanes (manual
  `release.py` and the sync workflow's `--next-patch` train) traverse
  `set_version`.
- `tests/test_docs_consistency.py::test_readme_install_example_matches_shipped_version`
  (`:151`) fences it: exactly 2 wheel URLs (uv and pip forms, tag slot ==
  filename slot) and at least one "(shown for vX.Y.Z)" prose note, each
  version equal to EITHER the pyproject `version` OR the newest `v*` git tag
  (`_latest_release_tag`, `:46`, tolerates tag-less shallow checkouts by
  returning `None` — pyproject-only anchor there). The battery runs in
  `validate_and_build()` BEFORE `commit_and_tag()`, so a stale example aborts
  a release before any commit exists — and CI runs it on every push/PR.
- Why two anchors: a pyproject-only anchor reddens a landing whose example
  targets a newer master (observed on a prior lane); a tag-only anchor
  reddens inside the release transaction (the tag is created after the
  battery runs). Example version ∈ {pyproject, newest tag} is green in-tree,
  green through the release window, and green on master immediately
  post-merge.
- Live proof the class was real until now: while this run was in flight, the
  exogenous trains v0.3.58 (`fbf5a98`) and v0.3.59 (`73ad3f7`) shipped with
  the example still at v0.3.20 (`git show 73ad3f7:README.md` — prose at
  `:368`). The first release that runs through the new `set_version` carries
  the example forward in the same commit as the version bump — the durable
  proof the class is closed.
- Operational note for the merge that lands this change: this branch's
  example was re-synced to v0.3.64 at targeted_tests time (2026-11-24)
  by running the ported updater itself (`_update_readme_install_example`),
  after master's exogenous trains advanced past the implement-time v0.3.58
  (green here: the newest local tag is v0.3.64, equal to master's
  pyproject). If master ships further releases before landing, the example
  matches neither of master's anchors until the next release commit —
  re-sync the three sites (§2 R1) in the merge again, or accept push-CI red
  until the next train's updater self-heals it. PR CI note: `actions/checkout`'s default `fetch-depth: 1` carries no
  tags, so `_latest_release_tag` returns `None` there and the fence anchors
  on pyproject alone.

## 7. Same-series provenance 0.49.5 → 0.49.57 (since the ledger's last entry)

The append-only adoption ledger (`docs/UPSTREAM-SERIES-ADOPTIONS.md`) last
recorded the 0.45.0 series adoption (2026-10, per the 2026-10 §6 table
ending at 0.49.5 = `v0.3.16`). Pin deltas since, re-verified from local git
— **zero series jumps**: every tag from `v0.3.16` through `v0.3.58` pins
`0.49.x` (full sweep: `git show <tag>:src/magic_hermes/magic_context_compat.json`
per tag; `v0.3.16`=0.49.5, `v0.3.52`=0.49.52, `v0.3.57`=0.49.57,
`v0.3.58`=0.49.59; exogenous `73ad3f7`/v0.3.59 = 0.49.60).

- Window `v0.3.16..068b013` (this run's base): 38 commits touch the manifest
  — 37 by `github-actions[bot]` (the sync workflow's near-daily train), 1 by
  `agent` (`578a4bf`, 2026-10-23, "chore: sync Magic Context compat pin to
  0.49.19" — a local release-train invocation, the author class the 2026-10
  §6 observation recorded).
- 0.49.5 → 0.49.57 is +52 upstream patch versions over those 38 commits:
  catch-up syncs compress multiple upstream patches into one pin bump (e.g.
  `v0.3.57`=0.49.57 → `v0.3.58`=0.49.59 skips 0.49.58).
- Same-series patches are not adoptions by the ledger's charter, so the
  ledger legitimately stays at its last series entry; its preamble still
  reads 0.49.5-era facts — stale, flagged, and out of scope here (§8).

## 8. Considered and declined

- Hand-editing `ROADMAP.md` to close stale items: managed-marker contract
  violation, guaranteed clobber; rejected (§5).
- Writing store closures or extending the hermes-roadmap CLI with a close
  command: touches another project's operational state; escalated instead
  (§5).
- Adoption-ledger backfill for same-series pins (0.49.5 → 0.49.57): the
  ledger's charter is series adoptions only; declined (§7).
- A version-agnostic install example (e.g. a `latest` link or docs URL):
  declined — the public pip/uv audience needs the exact wheel URL, and the
  rot class is now fenced instead (§6).
- A new README UPGRADE/coordination section (rm-014 fragment): declined
  again — the coordination behavior already ships where it is used
  (series-jump gate and adoption-PR mechanism, §Compatibility and the sync
  workflow).
- Documenting `private-leak-sentinel.yml` (superseding the 2026-10 §7 R6
  decline, as the never-landed 2026-11-12 draft proposed): not carried by
  this run's scope — the 2026-10 decline stands on master; the proposal is
  recorded here so the supersede is not silently lost.
- Any CI or workflow YAML change (including `fetch-tags` hardening for the
  fence's tag anchor and the sentinel's): out of scope; noted in §6, not
  carried.
- Re-verifying branch protection or other admin-only state: recorded as
  locally unverifiable rather than cited from stale memory.

*Recorded 2026-11-17 by conductor run `b385c6c7d8dd4f3ca318d70c39dfe80d`.
Facts verified against the run's integration tree (`36b4a65` = base `068b013`
v0.3.57 + Unit 1), local git history through `73ad3f7` (v0.3.59), the roadmap
store (183 rows, 2026-11-17), and live `gh` where marked. The README fixes
this record cites land with it, in the same run (Unit 1, same integration
branch); `ROADMAP.md` is byte-identical.*
