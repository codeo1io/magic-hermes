# magic-hermes — Roadmap

> Autonomously maintained by the roadmap sync (reliability-first). Items cite reproducible codebase signals; acceptance is proven by cited evidence.

**Vision**: A reliable, customer-friendly repository advanced by evidence-cited roadmap cycles owned by the autonomy loop

**Pillars**: reliability work outranks customer-experience work; every roadmap item cites reproducible codebase signals; acceptance is proven by cited evidence, never claimed

## Fleet context

- dependents (changes here affect): agent
- graph: evidence-derived (imports/refs/deploy surfaces); advisory

## Open items

### Add test coverage for 11 untested module(s)
- id: `rm-003` | track: reliability | priority: 100.0 | status: candidate
- signals: reliability.no_tests:src/magic_hermes/_host_budget.py, reliability.no_tests:src/magic_hermes/bridge/loader.mjs, reliability.no_tests:src/magic_hermes/bridge/migrate-hooks.mjs, reliability.no_tests:src/magic_hermes/cli.py, reliability.no_tests:src/magic_hermes/db_toolkit.py (+6 more)
- acceptance: Every module in ['src/magic_hermes/_host_budget.py', 'src/magic_hermes/bridge/loader.mjs', 'src/magic_hermes/bridge/migrate-hooks.mjs', 'src/magic_hermes/cli.py', 'src/magic_hermes/db_toolkit.py', 'src/magic_hermes/engine.py', 'src/magic_hermes/historian_guard.py', 'src/magic_hermes/memory_provider.py', 'src/magic_hermes/plugin.py', 'src/magic_hermes/provenance.py', 'src/magic_hermes/runtime.py'] has a corresponding test file with at least one passing test
- evidence: full suite green (python -m pytest -q) at HEAD; conductor validation digest validation:v1:<sha> recorded in the shipping PR

### Complete the v0.3.4 release/deploy chain and fix the pending-version skip
- id: `rm-012` | track: reliability | priority: 96.0 | status: in_progress
- acceptance: (a) release.py refuses or re-targets --next-patch when the working-tree version is already a pending release (unit test: pyproject 0.3.4 + latest tag v0.3.3 → resolves v0.3.4, never v0.3.5); (b) tag v0.3.4 + GitHub release published from master b1de439 as-is; (c) probe venv redeployed to 0.3.4 (dist-info) with MAGIC_HERMES_DEPLOY_SMOKE=1 battery green; (d) maestro doctor probe returns rc=0/FAIL 0 across 3 consecutive evaluations
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Land PR #18 — count cached prompt tokens toward Magic Context pressure
- id: `rm-013` | track: reliability | priority: 92.0 | status: in_progress
- acceptance: PR #18 merged onto master (or superseding commit) with the fix counting cache_read_input_tokens + cache_creation_input_tokens (Anthropic) and the OpenAI cached-token path; regression tests cover both provider shapes; release notes carry the #17 symptom table (billed input vs last_input_tokens) for operators diagnosing stuck sessions
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Refactor 20 high-complexity function(s)
- id: `rm-001` | track: reliability | priority: 90.0 | status: candidate
- signals: reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L117, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L167, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L187, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L194, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L214 (+15 more)
- acceptance: Each flagged function is decomposed below the branch threshold with behavior locked by characterization tests
- evidence: ast-based branch-count check passes at HEAD (full suite green; conductor validation digest validation:v1:<sha> recorded in the shipping PR)

### Deliberate upstream 0.44.0 adoption with a coordinated-upgrade runbook
- id: `rm-014` | track: reliability | priority: 90.0 | status: candidate
- acceptance: manifest + package.json → 0.44.0 (workflow-identical via sync_magic_context_release.py); PARITY.md gains a fence-91 section; README gains an UPGRADE/coordination section (stop all hosts on the shared store → update → restart order; doctor lane check before resuming); historian_guard RETIRES_AT_UPSTREAM (:45) re-evaluated against 0.44.0/0.44.1 with the verdict recorded; README documents v0.3.4's doctor budget env knobs (assess F11); full gate green at 0.44.0; released and deployed via the deploy-stage approach (run 2a0fcf9d Approach A — cross-referenced, not re-derived)
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Loader support for the 0.44.1 chunk layout
- id: `rm-015` | track: reliability | priority: 78.0 | status: candidate
- acceptance: loader resolves the 0.44.1 export form (recognize in-chunk declarations, not only `export {x as name}` forms); battery green against 0.44.1 with manifest at 0.44.1; loader unit test pins the new export form (extends rm-003's untested-module gap)
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Sync-workflow guardrails: gate series jumps and fence moves
- id: `rm-016` | track: reliability | priority: 75.0 | status: in_progress
- acceptance: a series-change or fence-move release pauses for approval (protected environment) or opens a PR instead of direct master push; same-series patch releases stay automatic; documented rollback (revert pin+manifest commit); three consecutive scheduled runs green
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Doctor store-ahead verdict on the schema lane
- id: `rm-017` | track: reliability | priority: 72.0 | status: candidate
- acceptance: lane > fence-for-supported-series ⇒ WARN naming remediation (upgrade magic-hermes; identify the newest local copy) — WARN not FAIL, mirroring the maestro-safe precedent (cli.py:572-576); unit test with a mocked ahead lane; docs updated alongside rm-014's UPGRADE section
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### PR-triggered CI test workflow
- id: `rm-018` | track: reliability | priority: 55.0 | status: in_progress
- acceptance: PR/push workflow runs pytest (pinned npm package via MAGIC_CONTEXT_PACKAGE_ROOT with caching) + ruff + node --check; includes a small docs-consistency test (README cron/pin claims vs workflow + package.json); green required before merge (pairs with rm-016's protection work)
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Make the historian integration tests hermetic
- id: `rm-022` | track: reliability | priority: 45.0 | status: candidate
- acceptance: the historian tests (or a shared fixture) seed their own minimal historian config into an isolated config path (tmp dir / env override) so the battery passes on a bare machine with no user config; the ci.yml seed step is then removable; battery green with HOME pointed at an empty dir
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### CLI hygiene: dead conditional, config-backup retention
- id: `rm-021` | track: reliability | priority: 30.0 | status: candidate
- acceptance: dead branch removed or made meaningful; backup writes keep at most N (configurable) newest backups; unit tests for retention; full gate green
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Document the runtime requirements the battery actually has
- id: `rm-023` | track: reliability | priority: 25.0 | status: candidate
- acceptance: README (or docs/) carries a short "Runtime requirements" note: node >= 23 for node:sqlite, the optional user-level CortexKit historian config and what readiness checks do without it (historian disabled, sidecar otherwise functional), linking the solutions doc; docs-consistency test extended to pin the node floor claim against ci.yml
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### Upstream ecosystem presence: dashboard Hermes row + harness listing
- id: `rm-019` | track: customer_experience | priority: 50.0 | status: candidate
- acceptance: upstream issue/PR proposing Hermes in the harness list + dashboard filter, linked from PARITY.md; PARITY row records the outcome (listed / not listed / listed-after-PR); rm-011 dashboard remainder closed either way
- evidence: campaign-recorded in magic-hermes ROADMAP.md

### rm-010 pivot: document the host command-surface limit + `magic-hermes status` stopgap
- id: `rm-020` | track: customer_experience | priority: 40.0 | status: candidate
- acceptance: PARITY.md records the host blocker with citation (rm-010's documented-limitation branch); either a read-only `magic-hermes status` CLI printing pressure/lease/pending-drops/last_input_tokens or an explicit decline recorded in PARITY.md
- evidence: campaign-recorded in magic-hermes ROADMAP.md

## Closed items

- `rm-002` Refresh stale top-level documentation — done
- `rm-004` Adopt upstream Magic Context v0.42.6 — done
- `rm-005` Correct README sync-cadence claim — superseded
- `rm-007` Allowlist-based read_only gating and plugin lifecycle tests — done
- `rm-006` Harden sync-workflow reliability — done
- `rm-008` Runtime reliability hardening (timeouts, shutdown race, unbounded caches) — done
- `rm-010` Hermes slash-command parity (/ctx-status, /ctx-flush, /ctx-recomp, /ctx-wrapup, /ctx-dream) — done
- `rm-011` Docs/DX: storage override, dashboard visibility, 0.42.6 parity notes — done
- `rm-009` Dead code removal — done

<!-- managed by hermes-roadmap render; do not edit by hand -->
