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
