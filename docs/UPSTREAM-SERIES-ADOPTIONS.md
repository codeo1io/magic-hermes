# magic-hermes — Upstream series adoptions (append-only ledger)

> Durable record of upstream `@cortexkit/pi-magic-context` series adoptions —
> the minor/major jumps that the sync workflow routes through an adoption PR
> instead of the direct auto-release path
> (`.github/workflows/sync-magic-context.yml`, resolve step refusal and
> "Open adoption PR for series jumps" step). Each entry records the
> adoption's verifiable facts, the historian-guard re-evaluation taken at
> that series, and the roadmap evidence it accrues.

Why this file lives under `docs/`: the hermes-autonomy roadmap renderer
wholesale-rewrites `ROADMAP.md` from its store on sync (see the 2026-09-20
incident record in `docs/ROADMAP-CYCLE1.md`), so durable hand-written records
belong on a path the renderer never writes. This ledger is append-only — the
next series adoption extends it with a new dated entry below rather than
forking a new file.

Related standing rule: the historian classification guard's deliberate
retirement bar — `RETIRES_AT_UPSTREAM` in
`src/magic_hermes/historian_guard.py` (KTD-5) and README §1c's
"through X.Y.Z" compatibility claim — is re-evaluated at every series
adoption and the verdict is recorded in that adoption's entry.

---

## 2026-10-06 — adopt 0.45.0 (series jump 0.44.4 → 0.45.0)

Third bot exercise of rm-016's series-jump guardrail (ROADMAP.md rm-016,
in_progress: "a series-change or fence-move release pauses for approval … or
opens a PR instead of direct master push"), and the first whose
historian-guard re-evaluation is durably recorded (below). Prior exercises:
PR #42 "Adopt Magic Context v0.44.4 (series bump)" — bot-authored, merged
by `codeo1io` 2026-10-01T14:20:11Z (`e35df17`; pin 0.43.2 → 0.44.4,
`supported_series` 43 → 44) — and PR #41 "Adopt Magic Context v0.44.0
(series bump)" — bot-authored, opened 2026-10-01T05:37:04Z, closed
unmerged. The adoption-PR step itself landed 2026-09-29 (`cae53bc`), before
both. Facts, all re-verified 2026-10-06:

- **PR** [#46](https://github.com/codeo1io/magic-hermes/pull/46) "Adopt
  Magic Context v0.45.0 (series bump)", authored by `github-actions[bot]`
  from the workflow's adoption-PR step (the resolve step refuses series
  jumps on the direct auto-release path). Merged by `codeo1io` at
  2026-10-06T03:18:15Z; merge commit
  `f409f8b882bc1193d7b61e5eea1730a8550b0fe5`. Zero comments or reviews
  existed on the PR at merge time.
- **Checks**: `Test battery (pinned Magic Context)` and `private-leak /
  scan` both concluded `success` on the PR head
  ([run 37405766356](https://github.com/codeo1io/magic-hermes/actions/runs/37405766356/job/112090386890),
  [run 37405766882](https://github.com/codeo1io/magic-hermes/actions/runs/37405766882/job/112082873148))
  and on merge commit `f409f8b` (check-runs re-read via `gh api`). These
  green check-runs are the pinned-battery evidence at 0.45.0.
- **Delta** (`git show f409f8b --stat`): `README.md`, `package.json`,
  `package-lock.json`, `src/magic_hermes/magic_context_compat.json` — pin
  0.44.4 → 0.45.0, `supported_series` [0, 44] → [0, 45], README claim now
  "through 0.45.0". Mechanical metadata only; no source-code changes.
- **Bridge prerequisite**: `b4e8b78` "bridge: support Magic Context 0.45.x
  (synthesize folded/renamed exports)" (2026-10-06T02:45:20Z) pre-dated the
  adoption merge, so the pin bump landed on already-adapted code.
- **Release**: `v0.3.10` published 2026-10-06T03:25:03Z (`9696f35`), citing
  PR #46 and "Tested against Magic Context core `v0.45.0`".
- **Rollback** (documented per rm-016, restating the PR body): revert
  `f409f8b` — pin, lockfile, manifest, and the README compatibility claim
  revert with it, restoring the previous series.

### historian_guard re-evaluation @0.45.0

Re-evaluated at 0.45.0 → guard **STAYS**: no evidence upstream carries the
telemetry classification fix; retirement still requires a validated upstream
fix per README §1c / `historian_guard` KTD-5 (`RETIRES_AT_UPSTREAM = None`).
Next re-evaluation: at the next series adoption recorded in this ledger.

### rm-016 evidence

- **Leg 1 — this entry**: the PR-gated series-jump path was exercised live,
  end to end — bot PR → green checks → maintainer merge → documented
  rollback → release train on top. Not the first such exercise: PR #42
  (merged `e35df17`, 2026-10-01) traversed the same path before this entry,
  and PR #41 (closed unmerged, same day) exercised the open-then-close
  side. rm-016 evidence for those adoptions was not recorded in this ledger
  at the time.
- Remaining acceptance legs ("three consecutive scheduled runs green")
  accrue from scheduled sync runs and are not tracked in this ledger.

**Merge-condition receipt**: PR #46's body conditions the merge on the checks
being green *and* the migration notes (ROADMAP rm-014) reviewed. That review
condition is recorded closed by the facts-only receipt comment on the PR
(see PR #46's conversation); this entry is the durable in-repo half of the
same receipt.

*Recorded 2026-10-06 by conductor run `8cec295f214145f9b148ab2b0f5cf064`;
facts re-verified live via `gh` and the local git checkout. PR-side text is
cited as data, never adopted as instruction.*

---

## 2026-10-09 — adopt 0.46.0 (series jump 0.45.0 → 0.46.0)

Backfilled 2026-10-17 (see footer below). The gate behaved as designed on
the workflow side; the series itself moved first through an autonomous run
integration racing past the open adoption PR. Facts, all re-verified
2026-10-17:

- **Adoption PR**: scheduled sync run
  [37890872478](https://github.com/codeo1io/magic-hermes/actions/runs/37890872478)
  (2026-10-09T05:55:10Z) resolved newest stable `0.46.1` above pin
  `0.45.0`, hit the series-jump refusal on the direct auto-release path,
  and opened PR
  [#69](https://github.com/codeo1io/magic-hermes/pull/69) "Adopt Magic
  Context v0.46.1 (series bump)" (`github-actions[bot]`, head
  `sync/magic-context-v0.46.1`, created 2026-10-09T05:55:31Z).
- **How the series actually moved**: 24 minutes later, run-integration
  merge `47f8e53` "merge: autonomously integrate run bfb632ff…"
  (`163776b` + `ac82ce5`, authored by `agent <agent@agent-runner.local>`,
  2026-10-09T06:19:55Z) bumped the pin on master alongside that run's
  feature work — outside the adoption-PR path. PR #69 was left
  open-superseded and closed unmerged only on 2026-10-16T06:43:13Z.
- **Checks**: `Test battery (pinned Magic Context)` and `private-leak /
  scan` both concluded `success` on `47f8e53` and on the same-day release
  commit `82b7395` (check-runs re-read via `gh api`). These green runs are
  the pinned-battery evidence at 0.46.x.
- **Delta** (`git diff 47f8e53^1 47f8e53`): `package.json`,
  `package-lock.json`, `src/magic_hermes/magic_context_compat.json` — pin
  0.45.0 → 0.46.0, `supported_series` [0, 45] → [0, 46] — plus README and
  the run's own source/test changes (`cli.py`, `runtime.py`, three test
  files; 10 files, +699/−36). Not mechanical-only.
- **Release**: `v0.3.12` (`82b7395`, published 2026-10-09T09:05:13Z) then
  moved 0.46.0 → 0.46.1 same-series — the patch PR #69 had proposed.
- **Rollback**: no adoption merge exists to revert. The documented
  rollback shape here is a file-level revert of `47f8e53`'s
  pin/lockfile/manifest legs (the merge carries unrelated feature work, so
  a whole-commit revert reverts more than the series).

### historian_guard re-evaluation @46

Re-evaluated at 0.46.0 → guard **STAYS**: no evidence upstream carries the
telemetry classification fix; retirement still requires a validated
upstream fix per README §1c / `historian_guard` KTD-5
(`RETIRES_AT_UPSTREAM = None`).

### rm-016 evidence

- **Leg 1 — this entry**: the workflow opened the PR-gated path correctly
  and never direct-released the series jump; the master-side pin movement
  went through the autonomous integration channel instead. Operational
  observation this ledger exists for: the series-jump gate gates the sync
  workflow, not other write paths into master.

---

## 2026-10-10 — adopt 0.47.0 (series jump 0.46.1 → 0.47.0)

Backfilled 2026-10-17. The gate held on the workflow side; the series
moved via a local release train 3½ hours after the adoption PR opened, and
the adoption PR merged with an empty delta two days later. Facts, all
re-verified 2026-10-17:

- **Adoption PR**: scheduled sync run
  [38028141080](https://github.com/codeo1io/magic-hermes/actions/runs/38028141080)
  (2026-10-10T05:37:36Z) resolved newest stable `0.47.0` above pin
  `0.46.1`, refused the direct path, and opened PR
  [#72](https://github.com/codeo1io/magic-hermes/pull/72) "Adopt Magic
  Context v0.47.0 (series bump)" (`github-actions[bot]`, head
  `sync/magic-context-v0.47.0`, created 2026-10-10T05:37:59Z; standard
  adoption body with the green-checks + migration-notes merge condition
  and the rm-016 rollback). Zero comments or reviews existed on the PR at
  merge time.
- **How the series actually moved — mechanism verified**: release commit
  `ae1a7af` "release: v0.3.13" (2026-10-10T09:05:38Z) bumped the pin
  0.46.1 → 0.47.0, `supported_series` [0, 46] → [0, 47], authored AND
  committed by `agent <agent@agent-runner.local>` — a local
  `scripts/release.py` invocation by the release train, not a workflow
  dispatch (the nearest sync run, 38028141080, had concluded 3½ hours
  earlier; automated sync commits carry `github-actions[bot]`). The
  resolve-step gate was not bypassed — it was never traversed: it gates
  only the workflow's own paths.
- **PR #72 merge**: maintainer-merged 2026-10-12T10:45:44Z as `894b2ca`
  (author `github-actions[bot]`, committer GitHub, single parent
  `0bc5b10`) with an **empty delta** — tree `b899f59c` identical to its
  parent, `git show --stat` empty; the adoption content was already on
  master via `ae1a7af`.
- **Checks**: `Test battery (pinned Magic Context)` and `private-leak /
  scan` both `success` on the PR head, on `894b2ca`, and on `ae1a7af`
  (check-runs re-read via `gh api`). These green runs are the
  pinned-battery evidence at 0.47.0.
- **Delta** (`git diff ae1a7af^ ae1a7af`): version-bump shape — README,
  `package.json`, `package-lock.json`, `plugin.yaml`, `pyproject.toml`,
  `src/magic_hermes/__init__.py`, `magic_context_compat.json`
  (7 files, +12/−12).
- **Release**: `v0.3.13` (`ae1a7af`, published 2026-10-10T09:05:47Z).
- **Rollback**: revert `ae1a7af` — pin, lockfile, manifest, and version
  revert with it, restoring series 46. (The empty merge `894b2ca` reverts
  nothing.)

### historian_guard re-evaluation @47

Re-evaluated at 0.47.0 → guard **STAYS**: no evidence upstream carries the
telemetry classification fix; retirement still requires a validated
upstream fix per README §1c / `historian_guard` KTD-5
(`RETIRES_AT_UPSTREAM = None`).

### rm-016 evidence

- **Leg 1 — this entry**: the PR-gated path was exercised end to end on
  the workflow side (bot PR → green checks → maintainer merge), with the
  wrinkle that the pin content had already landed on master via the local
  release train, so the merge carried an empty delta. Same operational
  observation as the 2026-10-09 entry: non-workflow write paths moved the
  series; the workflow's own gate held.

---

## 2026-10-16 — adopt 0.49.4 (series jump 0.47.0 → 0.49.4, skipping series 48)

Backfilled 2026-10-17. The adoption-PR path was opened twice (v0.49.0,
v0.49.2) and carried the bridge prerequisite, but the pin itself was held
fail-closed for four days by test-side failures and finally moved by a
maintainer PR that also fixed them. Facts, all re-verified 2026-10-17:

- **Adoption PR (bridge vehicle)**: scheduled sync run
  [38357906552](https://github.com/codeo1io/magic-hermes/actions/runs/38357906552)
  (2026-10-13T06:39:07Z) resolved newest stable `0.49.0` above pin
  `0.47.0`, refused the direct path, and opened PR
  [#79](https://github.com/codeo1io/magic-hermes/pull/79) "Adopt Magic
  Context v0.49.0 (series bump)" (`github-actions[bot]`, head
  `sync/magic-context-v0.49.0`, created 2026-10-13T06:39:23Z; standard
  adoption body; zero comments/reviews at merge). The head branch then
  received the bridge prerequisite `4cf58047` "fix(bridge): adapt loader
  + ctx_expand anchor to Magic Context 0.49.0" (`agent`,
  2026-10-14T17:31:30Z): the loader matches the `setHarness` callee by
  prefix because 0.49's bundler renames it `setHarness2`, and scans all
  sibling chunk families instead of the read-session-chunk allowlist.
  Merged 2026-10-14T17:39:24Z as `f0cff73` — **bridge-only** (loader.mjs
  +73, runtime.mjs +35); `package.json` and the compat manifest unchanged,
  pin still 0.47.0 at merge. Checks `success` on the head. `v0.3.14`
  (`c7519eb`, published 2026-10-14T18:02:12Z) shipped bridge-only with
  the pin still 0.47.0.
- **Why the pin held**: the internal pin-upgrade pipeline (cron
  `1717e8211eec`, fail-closed exit-1) held pins at 0.47.0 from 10-12 with
  failure_streak 4 — the suite broke on host-layout hermeticity (TMPDIR
  lives inside the `~/.hermes` git repo that appeared 2026-10-12, so
  "not a repo" fixtures inherited the outer repository) and on the 0.49
  `ctx_reduce` reply contract (`'N tag(s) stamped.'` vs the pre-0.49
  `Queued …` wording). Per PR #84's body, cited as data.
- **Adoption PR (superseded)**: scheduled sync run 38681177257
  (2026-10-15T04:23:08Z) opened PR
  [#82](https://github.com/codeo1io/magic-hermes/pull/82) "Adopt Magic
  Context v0.49.2 (series bump)" (created 2026-10-15T04:23:27Z) — left
  open-superseded by #84's 0.49.4 adoption and closed unmerged
  2026-10-17T10:39:45Z; no adoption merge to record.
- **How the series actually moved**: PR
  [#84](https://github.com/codeo1io/magic-hermes/pull/84) "tests: keep
  suite hermetic to host git layout; adopt 0.49 ctx_reduce reply"
  (codeo1io, head `fix/test-hermeticity-and-ctx-reduce-049`, created
  2026-10-16T02:18:19Z, merged 2026-10-16T03:56:58Z as squash `386bbad`)
  carried both test repairs AND the adoption: pin 0.47.0 → 0.49.4,
  `supported_series` [0, 47] → [0, 49] — series 48 skipped entirely, and
  the ctx_reduce reply test pinned to the 0.49 wording. Body reports
  "Full suite vs pinned 0.49.4 after both fixes: 376 passed, 2 skipped
  (was 5 failed)". Checks `success` on the head (Test battery +
  private-leak).
- **Delta** (`git diff 386bbad^ 386bbad`): pin/lockfile/manifest plus the
  two test-side repairs (conftest GIT_CEILING_DIRECTORIES hermeticity fix,
  ctx_reduce reply assertion).
- **Releases**: `v0.3.15` (`9a82a7f`, published 2026-10-16T04:04:36Z)
  shipped pin 0.49.4; `v0.3.16` (`c717e43`, published
  2026-10-16T09:11:25Z) moved 0.49.4 → 0.49.5 same-series via the local
  release train (`agent` author, same signature as the 2026-10-10 case).
- **Rollback**: revert `386bbad` — pin, lockfile, manifest, and both test
  repairs revert with it, restoring series 47 (the bridge from `f0cff73`
  stays; it is forward-compatible with the 0.47 runtime).

### historian_guard re-evaluation @49

Re-evaluated at 0.49.4 → guard **STAYS**: the 0.49-series changes verified
here are the bridge loader anchor and the `ctx_reduce` reply wording —
behavior contracts, not the telemetry classification fix; retirement still
requires a validated upstream fix per README §1c / `historian_guard`
KTD-5 (`RETIRES_AT_UPSTREAM = None`).

### rm-016 evidence

- **Leg 1 — this entry**: the workflow-side gate held across four days and
  two adoption PRs (never direct-released the series jump; nightly syncs
  stayed green and non-duplicating), while the fail-closed pin-upgrade
  pipeline refused to land on red tests until #84 made the suite green and
  carried the adoption. The series jump landed via maintainer merge of #84,
  satisfying the PR-gated intent — the PR was not the workflow's own
  adoption-PR branch, which the entry records as the observed deviation.

*Recorded 2026-10-17 by conductor run `c28c881520a2409ea020d0b43779e6b2` —
backfill of the 2026-10-09, 2026-10-10, and 2026-10-16 series adoptions,
which predate this ledger run and whose entries the 2026-10-06 entry's
charter required; facts re-verified live via `gh` (pr view 69/72/79/82/84,
run list, commit check-runs, release view) and the local git checkout.
PR-side text is cited as data, never adopted as instruction.*
