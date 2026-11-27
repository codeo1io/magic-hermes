# Roadmap reconciliation — 2026-11

Dated, hand-owned record reconciling the roadmap store's open items with
shipped repository behavior. Originally recorded 2026-11-24 by conductor run
`eff5ce3988664573b37b660b90e7ef84` (phase `implement:implement-2`); ported
byte-for-byte from that run's origin-pushed branch
(`conductor/run-eff5ce398866` @ `7fadaaf`) and refreshed in place 2026-11-27
by conductor run `aeb1fe50763443f7bb48a21e0894a94d` (phase
`implement:implement-2`) against the run branch
`conductor/run-aeb1fe507634` @ `9215c01` — the tree with this run's Unit 1
(install-example reconcile) integrated (implement-1 checkpoint `f6ce413`);
Unit 1's base is `fcd48a7` (release v0.3.66). Canonical master at refresh time
is `bb26ecc` (release v0.3.71). Facts re-verified from the local checkout and
live `gh` where cited.

Provenance: this record ports, byte-for-byte as its starting point, the
2026-11-24 record written by the 7th lane (run
`eff5ce3988664573b37b660b90e7ef84`) — that lane died before merge, but its
branch was pushed to origin, so its complete work survived and this 9th-lane
run (`aeb1fe50763443f7bb48a21e0894a94d`) re-lands it. The 2026-11-24 record
itself rewrote, from that run plan's embedded spec, the verdict doc originally
drafted 2026-11-12 in the `609fd09da29f` lane (158 lines, 14-row table against
the then-current render); that draft was lost — its worktree was pruned before
the lane landed. The 2026-11-24 re-verified rows below carry forward,
re-verified at this refresh. Structure follows
`docs/ROADMAP-RECONCILIATION-2026-10.md` (2026-10-17, run
`c28c881520a2409ea020d0b43779e6b2`).

Why this lives under `docs/` rather than in `ROADMAP.md`: the roadmap renderer
rewrites `ROADMAP.md` wholesale from its store on every sync — hand-written
content there does not survive (the 2026-09-20 clobber incident,
`docs/ROADMAP-CYCLE1.md`). This file follows the same convention as
`docs/UPSTREAM-SERIES-ADOPTIONS.md`. Nothing in this record edits
`ROADMAP.md`.

## 1. Purpose

The roadmap store's open-item set had drifted from shipped reality: items
whose acceptance was already satisfied by merged work remained `candidate` or
`in_progress`. This record gives every open store item a verdict against
verifiable repository evidence, so the reconciliation is durable in-repo even
though the rendered `ROADMAP.md` itself remains store-faithful (and therefore
store-stale) until the closure-channel gap in §5 is fixed estate-side.

Statuses below are those of the 2026-11-06 render (commit `7063695`,
2026-11-06 00:07:30 UTC), re-confirmed unchanged by the 2026-11-25 render
(`c44dccb`, 2026-11-25 00:45 UTC — re-diffed at this refresh: tracks-only, all
16 changed lines are `track:` reassignments, every item status identical);
`c44dccb` is still the latest render at refresh time 2026-11-27.

## 2. README divergences fixed by this run

This run's Unit 1 (re-hosted from checkpoint `c6ffb98`, folded at `f6ce413`)
repaired the install-example drift class with a self-sustaining fix set:

| # | Divergence | Fix (README anchor) |
|---|---|---|
| R1 | Install example cited v0.3.20 wheel URLs (§Install step 3, two URL lines plus the "shown for" prose) while the release train kept shipping — the example froze at v0.3.20 from before 2026-11-12 through at least v0.3.71, re-recorded at v0.3.52, v0.3.55, v0.3.58, v0.3.61 and v0.3.62 across five reconcile lanes that died before merge (609fd09da29f, 898eac32d451, b385c6c7d8dd, c2ff2a4a2303, eff5ce398866 — the last complete and pushed, re-landed by this run). The release lane maintained the `through X.Y.Z` pin claim but nothing owned the example. | Example refreshed to v0.3.71 (the newest `v*` tag at this run's implement time — the fold-durable anchor: the fence accepts the pyproject version or the newest tag, so the example stays green across master motion between fold and promotion), and the class is closed structurally: `scripts/release.py` `set_version` now auto-refreshes the example slots on every bump (tolerant `README_WHEEL_URL_PATTERN` + `README_EXAMPLE_PROSE_PATTERN` rewrite), and the new fence `test_readme_install_example_matches_shipped_version` in `tests/test_docs_consistency.py` enforces example ∈ {pyproject version, newest `v*` tag} inside the release gate — a stale example aborts a release pre-commit, so the train becomes the example's maintainer instead of its adversary |
| R2 | §Architecture did not mention the startup bind-lock concurrency behavior (shipped v0.3.29, documented only in `docs/v0.3.29-bindlock-release.md`) | §Architecture carries the bind-lock paragraph linking `docs/v0.3.29-bindlock-release.md` |
| R3 | §Verification did not mention the `private-leak-sentinel` full-history credential scan | §Verification carries one operator-facing sentence documenting the workflow (gitleaks via the fleet-wide reusable workflow, every push/PR plus a daily off-peak rescan) |
| R4 | The patch-increment era example cited the `0.3.57` → `0.3.58` pair on the salvage base | Refreshed to the current pair (`0.3.70` → `0.3.71`); cleanliness edit only — the era pair is not fence-enforced and not rewriter-managed |

## 3. Verdict table — open store items vs shipped behavior

Statuses as rendered 2026-11-06 (`7063695`, re-confirmed by `c44dccb` — see
§1). `rm-002` and `rm-004`–`rm-011` are already store-closed and agree with
shipped reality — no divergence, not repeated here. Evidence re-verified
2026-11-24 from the recorded tree and live `gh` where cited, and re-verified
again at the 2026-11-27 refresh where marked.

| item | store status | verdict | evidence |
|---|---|---|---|
| rm-012 — v0.3.4 release/deploy chain | in_progress | done | tag `v0.3.4` exists (re-verified 2026-11-24 and at the 2026-11-27 refresh); deploy gap closed by `release.py --deploy` (2026-10 record §3, PR #27); the release train has since reached v0.3.71 |
| rm-013 — land PR #18 (cached tokens) | in_progress | done | PR #18 state MERGED, mergedAt 2026-09-29T19:05:34Z (gh, re-verified live 2026-11-24 and again at the 2026-11-27 refresh; previously verified 2026-10-17) |
| rm-016 — sync-workflow guardrails | in_progress | done (one passive leg) | series-jump gate repaired and live (2026-10 record §3, `40fe03b`, PR #39); in-tree proof: `tests/test_docs_consistency.py::test_direct_release_is_gated_on_series_jump`, `::test_adoption_pr_step_exists`, `::test_series_jump_gate_payload_compiles`; only "three consecutive scheduled runs green" keeps accruing passively |
| rm-017 — doctor store-ahead WARN | candidate | superseded | recorded design decision: lane skew is a deliberate typed FAIL — README §1d: "why the typed lane-skew FAIL — not discovery — remains the" authoritative signal (README:348, re-verified 2026-11-24 and at the 2026-11-27 refresh — still line 348 on the run branch post-Unit-1) |
| rm-018 — PR-triggered CI | in_progress | done (one untestable leg) | `.github/workflows/ci.yml` triggers on `pull_request` + `push(master)` with the pinned-package battery, ruff and `node --check` (re-verified 2026-11-24); "green required before merge" is branch protection, not locally verifiable |
| rm-014 — deliberate 0.44.0 adoption + runbook | candidate | superseded | the series went 0.44.4 → 0.45.0 → 0.46.0 → 0.47.0 → 0.49.x; the pin is now 0.49.83 (manifest, master `bb26ecc`, re-verified at the 2026-11-27 refresh; 0.49.76 on this run's v0.3.66 base); coordination shipped as the adoption-PR mechanism itself (2026-10 record §3) |
| rm-015 — loader support for 0.44.1 chunk layout | candidate | superseded | loader validated through the 0.49.x lineage (compatibility manifest + green battery at each adoption; pin now 0.49.83, re-verified at the 2026-11-27 refresh); export synthesis for folded/renamed exports landed `b4e8b78` (2026-10 record §3) |
| rm-023 — document runtime requirements | candidate | partially done | node-floor leg landed 2026-10-17 (README §Requirements + fence `test_readme_node_floor_agrees_with_ci_pin`, `tests/test_docs_consistency.py:234`); the historian-config/readiness-note leg (optional user-level CortexKit config, readiness checks without it, solutions-doc link) remains open |
| rm-022 — make the historian integration tests hermetic | candidate | open | not shipped: the battery still depends on a seeded user-level historian config (this run's own validation scope had to route around the full battery for exactly this reason — the focused docs/release suites only); no hermetic fixture in `tests/test_cli.py` as of `9215c01`/master `bb26ecc` (re-verified at the 2026-11-27 refresh) |
| rm-012-adjacent note — v0.3.4 "pending-version skip" leg | — | satisfied by the train's evolution | `--next-patch` now resolves from the actual tag state (v0.3.4 → … → v0.3.71 without skips); the 2026-10 record's rm-012 verdict already covers the chain |

## 4. Genuinely open — not closed by this record

- rm-001 — high-complexity functions: detector-live signals, recounted every cycle.
- rm-003 — untested modules: detector-live signals, recounted every cycle.
- rm-019 — upstream ecosystem presence: external upstream-repo action, unstarted.
- rm-020 — `magic-hermes status` stopgap: no top-level status subcommand shipped
  (only guard/db sub-commands); no PARITY record of the decline.
- rm-021 — CLI hygiene (dead conditional, backup retention): not shipped.
- rm-023 — remainder only: the historian-config/readiness-note leg (§3).

## 5. Structural finding — roadmap closure-channel gap (escalated, not fixed here)

Unchanged since the 2026-10 record; re-escalated here because the affected
items are still open in the store:

- `ROADMAP.md` is renderer-owned: the render writes it unconditionally from
  the store (`/work/projects/hermes-autonomy/runtime/roadmap-state/magic-hermes/items.jsonl`),
  and the autonomy-side clobber guard preserves only files without the
  managed marker.
- The hermes-roadmap CLI exposes infer / show / validate / expand / ledger /
  render / verify / repair — **no item-close command**.
- Campaign-evidenced items carry the circular evidence expectation
  "campaign-recorded in magic-hermes ROADMAP.md": the renderer owns that
  file, so the expectation can never be satisfied from inside this
  repository.
- The store's only closure path is detector re-run reconciliation
  (`builder.py`), which these items' detectors do not cover.

Store-refresh note (added at the 2026-11-27 refresh): the 2026-11-25 render
(`c44dccb`) was re-diffed against `7063695` — tracks-only, no item opened or
closed — so the gap assessment is unchanged by it; no fresh escalation filing
was made at this refresh (the standing estate-side escalation carries
forward).

Consequence: rm-012/rm-013/rm-016/rm-018-class items can never close through
in-repo work; the rendered `ROADMAP.md` stays store-stale until the estate
adds a closure channel. Escalated to the autonomy estate as a separate work
item (this run's scope lock forbids writing another project's operational
state; hand-editing `ROADMAP.md` is a managed-marker contract violation and
was already disproven by the 2026-09-20 incident). This document is the
interim durable record.

## 6. Durability leg — the README side is now self-sustaining; the roadmap side remains store-owned

- **Mechanism (this run's Unit 1):** the example fence
  `test_readme_install_example_matches_shipped_version` (example ∈ {pyproject
  version, newest `v*` tag} — the tag anchor keeps the fence fold-durable
  across master motion — runs inside the release battery pre-commit) plus the
  tolerant `set_version`-tail auto-refresh mean every future release of this
  tree carries the example forward — drift becomes unshippable instead of
  recurring.
- **In-flight proof:** trains v0.3.53 through v0.3.71 shipped while the
  example stayed frozen at v0.3.20 on master (re-verified 2026-11-24 at
  `c1822ee` and at the 2026-11-27 refresh at `bb26ecc` — README:373/:380);
  the fix set survived five dead lanes (609fd09da29f implement-folded and
  lost, 898eac32d451 died post-brainstorm, b385c6c7d8dd died post-implement —
  its committed checkpoint survived, c2ff2a4a2303 died mid-implement leaving
  the completed diff uncommitted, eff5ce398866 died post-implement with its
  complete work pushed at `7fadaaf` — byte-verified as this run's re-land
  source) plus the 7th lane's two infrastructure-failed first attempts
  (7d630b62, e6e233b0 — delegate process deaths before any work; nothing to
  re-do) and the 8th lane's two session-stalled attempts, before landing in
  this 9th lane (`aeb1fe507634`).
- **Landing state at refresh:** Unit 1 is folded on the run branch
  (`conductor/run-aeb1fe507634` @ `9215c01`, implement-1 checkpoint
  `f6ce413`, base `fcd48a7` = v0.3.66) pending the conductor's promotion;
  canonical master (`bb26ecc`, v0.3.71, upstream pin 0.49.83) still carries
  the v0.3.20 example. On the first release after the fix reaches master, the
  fence will flag the stale slots and the rewriter auto-heals them — the
  class cannot recur silently.
- **Roadmap side:** unchanged ownership — the store closes only estate-side
  (§5); this dated record is the sanctioned in-repo reconciliation until
  then. `ROADMAP.md` is untouched by this run (byte-identical to `c44dccb`'s
  render in the recorded tree — verified by diff at this refresh).
