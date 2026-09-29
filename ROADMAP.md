# magic-hermes — Roadmap

> Autonomously maintained by the roadmap sync (reliability-first). Items cite reproducible codebase signals; acceptance is proven by cited evidence.

**Vision**: A reliable, customer-friendly repository advanced by evidence-cited roadmap cycles owned by the autonomy loop

**Pillars**: reliability work outranks customer-experience work; every roadmap item cites reproducible codebase signals; acceptance is proven by cited evidence, never claimed

## Fleet context

- upstreams (this repo builds on): hermes-agent
- dependents (changes here affect): (host)
- graph: evidence-derived (imports/refs/deploy surfaces); advisory

## Open items

### Refactor 21 high-complexity function(s)
- id: `rm-001` | track: reliability | priority: 90.0 | status: candidate
- signals: reliability.complexity_hot:*, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L130, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L143, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L163, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L181 (+16 more)
- acceptance: Each flagged function is decomposed below the branch threshold with behavior locked by characterization tests
- evidence: ast-based branch-count check passes in CI

### Add test coverage for 1 untested module(s)
- id: `rm-003` | track: reliability | priority: 83.0 | status: candidate
- signals: reliability.no_tests:src/magic_hermes/bridge/loader.mjs
- acceptance: Every module in ['src/magic_hermes/bridge/loader.mjs'] has a corresponding test file with at least one passing test
- evidence: CI: pytest collects the new test files and they pass

### Refresh stale top-level documentation
- id: `rm-002` | track: reliability | priority: 43.0 | status: candidate
- signals: reliability.stale_doc:README.md
- acceptance: Docs regenerated/updated; staleness detector reports 0 signals
- evidence: inference.stale_docs returns [] for the repo

<!-- cycle 1 additions (conductor run 455725e50d504905b0170c26b6ab56f9, 2026-09-20, assess + research evidence; recovered 2026-09-20 after the roadmap-sync clobber — see recovery note at end of file; conductor-delegate spool 4b696136f70e4ce5a83a61cf44bf9e9f.json and cd0305620de0477bbbc5e5a17cb1c9f9.json) -->

### Adopt upstream Magic Context v0.42.6
- id: `rm-004` | track: reliability | priority: 88.0 | status: in_progress
- signals: npm dist-tags latest=0.42.6 (published 2026-09-19T02:59Z) vs pinned 0.42.5 (package.json); 2026-09-19 04:26Z sync run failed in pytest (2 plugin tests, 'Runtime timed out after 30.0s during dreamer_tool_schemas'); fix already on master as 8c157dc; forward-compat smoke of the current bridge against extracted 0.42.6 passed (hello/bind/render_context/ctx_note/dreamer_tool_schemas/doctor, core_symbols_ready=true)
- acceptance: package.json pins 0.42.6; a sync workflow run completes green and lands the pin; test/PARITY notes cover the 0.42.6 contract changes (ctx_note single note_ids field; queued ctx_reduce drops ride the next cache bust; filler args ignored on ctx_expand/ctx_memory/ctx_search/ctx_reduce); full gate green at the new pin
- evidence: gh run list --workflow=sync-magic-context.yml shows success after 8c157dc; npm view @cortexkit/pi-magic-context version matches package.json; PYTHONPATH=src pytest -q passes at 0.42.6
- outcome: implemented in cycle 1 (pin bumped locally + full gate 74 passed / 0 skips at 0.42.6); the green-CI clause remains open under rm-006
- 2026-09-29 supersession note: master pins **0.43.2** today (landed via scheduled sync releases v0.3.0–v0.3.3); this item is complete — the next adoption is rm-014

### Correct README sync-cadence claim
- id: `rm-005` | track: reliability | priority: 85.0 | status: in_progress
- signals: README.md 'every 15 minutes' vs .github/workflows/sync-magic-context.yml cron '7 0 * * *' (commit 507c759 'Run Magic Context sync daily')
- acceptance: README states the daily 00:07 UTC cadence (or the cron is restored to 15 minutes); no other stale cadence claims
- evidence: grep -n 'minutes' README.md returns only accurate claims; git diff README.md
- outcome: corrected 2026-09-29 (run 624fc5ea): README now states 'daily at 00:07 UTC'; the cycle-1 claim was premature (README still said 'every 15 minutes' until this fix)

### Allowlist-based read_only gating and plugin lifecycle tests
- id: `rm-007` | track: reliability | priority: 80.0 | status: in_progress
- signals: src/magic_hermes/plugin.py:83-87 enforces read_only via hardcoded blocklist {write_file,patch,terminal,process}; plugin.py:61-72 subagent registration depends on subagent_start firing on the launching thread (threading.local set at :307-308); no unit tests for _on_pre_tool_call gating, _matching_trace (:181), _request_text truncation (:140), or subagent hook registration (tests/test_plugin.py covers none of these)
- acceptance: read_only enforced deny-by-default (allowlist of read-only tools, mirroring the memory capability's :97-102 pattern); unit tests cover capability gating for read_only+memory children, stop-event trace correlation, request-text truncation boundaries, and child registration/unregistration; all pass in CI
- evidence: pytest tests/test_plugin.py -q green with the new tests; grep shows no mutating tool outside the allowlist passes _on_pre_tool_call
- outcome: implemented in cycle 1 (deny-by-default allowlist {read_file, search_files, tool_search, tool_describe, tool_call} + ctx_* prefix; 5 new unit tests; independent review verified tool_call cannot reach mutating core tools via toolsets.py:11 + tool_search.py:142-155)

### Harden sync-workflow reliability
- id: `rm-006` | track: reliability | priority: 70.0 | status: candidate
- signals: last scheduled runs = failure 11h52m (2026-09-16), cancelled 30m10s (09-18), failure 16m36s (09-19); runner pytest 637s vs 77s local; actions/checkout@v4, setup-python@v5, setup-node@v4 emit Node-20 deprecation warnings in run logs
- 2026-09-29 refresh (run 230b8e0beab8): two more failures 2026-09-25 (38s, 31s) then green 09-26/27/28 (10–15s, gap-only); the next scheduled run (window ~04:30–05:10Z per the 3-day pattern) sees upstream drift (npm latest 0.44.1 vs pin 0.43.2) and will adopt v0.44.0 — the series-jump + fence-move case rm-016 exists to gate, and, until rm-012's pending-version guard lands, it will cut v0.3.5 skipping the still-pending v0.3.4
- acceptance: per-step timeout-minutes set on every sync-workflow step; action versions bumped past Node-20 deprecation; pytest split or timeout-tolerant on the self-hosted runner (no 30s dreamer_tool_schemas flakes); three consecutive scheduled runs green
- evidence: gh run list --workflow=sync-magic-context.yml shows 3 consecutive successes; run logs free of Node-20 deprecation warnings

### Runtime reliability hardening (timeouts, shutdown race, unbounded caches)
- id: `rm-008` | track: reliability | priority: 60.0 | status: candidate
- signals: src/magic_hermes/runtime.py:469-481 select+blocking readline lets a mid-line stall block past the call deadline (POSIX-only); src/magic_hermes/memory_provider.py:212-215 shutdown() does not join background threads so a thread past its is_set() check can respawn a Node sidecar via _ensure_process (runtime.py:347); memory_provider.py:48 _cached_context never evicted per session; bridge/runtime.mjs:2209 dreamerVirtualSessions map grows unboundedly
- 2026-09-29 refresh (assess 230b8e0beab8 F5/F7/F8/F9/F13): close() sets _closed (runtime.py:661-665) but neither call() (:559) nor _ensure_process() (:454) checks it — a post-close call silently resurrects the sidecar (only _reap_if_idle :370 reads _closed); renew_lease() (engine.py:362-388) shares the serialized request chain with slow historian calls; hostCallback (bridge/runtime.mjs:2837) parks resolvers in pendingHostCallbacks with no timeout; _cached_context written per session at memory_provider.py:247, never removed; _drain_stderr has no exception guard (runtime.py:454 area)
- acceptance: call deadline enforced per-line (bounded readline); shutdown joins/cancels background threads and cannot respawn after close; session caches evicted on session end with bounded size; regression tests for each; full gate green
- evidence: pytest -q green including new shutdown/eviction tests; no orphan node processes after a forced-shutdown test (pgrep evidence)

### Hermes slash-command parity (/ctx-status, /ctx-flush, /ctx-recomp, /ctx-wrapup, /ctx-dream)
- id: `rm-010` | track: customer_experience | priority: 50.0 | status: candidate
- signals: upstream 0.42.6 README ships five slash commands on Pi/OMP; the connector RPC already exposes pressure_state, maintenance_run, dreamer_run_manual and render_context(compaction_off) which back them; Hermes users currently have only /compress; docs/PARITY.md marks command UI as N/A
- 2026-09-29 refresh (run 230b8e0beab8 research): host blocker CONFIRMED — hermes-agent 0.21.4 exposes no plugin command-registration / slash-dispatch surface (greps over agent/ + cli/); rm-020 records the documented-limitation branch + a `status` stopgap
- acceptance: either the five commands are exposed on Hermes via the host plugin command surface (each mapped to the listed RPC methods with triggerTurn:false semantics), or PARITY.md documents the host limitation with the investigation result
- evidence: PARITY.md row updated; live /ctx-status (or equivalent) output pasted in the PR description, or a documented host-capability blocker with citation

### Docs/DX: storage override, dashboard visibility, 0.42.6 parity notes
- id: `rm-011` | track: customer_experience | priority: 45.0 | status: in_progress
- signals: upstream documents MAGIC_CONTEXT_STORAGE_DIR for hosts isolating XDG_DATA_HOME; magic-hermes README never mentions it (the Node child inherits env, so it works but is undiscoverable); upstream Dashboard releases (dashboard-v0.16.0) mention only Pi/OMP/OpenCode harnesses — hermes-row visibility unverified; fresh clones silently skip the integration suite when the pinned package is absent (tests/test_runtime_integration.py:19 skipif)
- 2026-09-29 refresh (run 230b8e0beab8 research): dashboard-v0.18.0's Sessions-viewer harness filter enumerates OpenCode/OpenCode 2/Pi/OMP — Hermes rows are invisible BY CONSTRUCTION, not by bug; the verification question is answered and carried to rm-019 (upstream presence ask)
- acceptance: README documents MAGIC_CONTEXT_STORAGE_DIR (and the shared-store semantics); dashboard hermes-row visibility verified and the outcome recorded in PARITY.md; CI or pytest reports a loud skip-count/fail-fast marker when the runtime package is missing so green runs cannot hide an unvalidated bridge
- evidence: grep README.md for MAGIC_CONTEXT_STORAGE_DIR; PARITY.md dashboard row; pytest -q run on a fresh clone shows an explicit runtime-missing warning
- outcome: local subset implemented in cycle 1 (README STORAGE_DIR note; loud collection-time skip warning with remediation — proven by fresh-clone simulation in independent review); dashboard verification still open

### Dead code removal
- id: `rm-009` | track: reliability | priority: 30.0 | status: in_progress
- signals: src/magic_hermes/engine.py:175-176 assigns _config_threshold_percent/_base_threshold_percent never read anywhere; bridge/runtime.mjs:1352-1355 else-branch unreachable (systemIndex<0 returns at :1291-1296)
- acceptance: dead attributes and branch removed with no behavior change; full gate green
- evidence: git diff; ruff check + pytest -q + node --check all pass
- outcome: implemented in cycle 1

<!-- cycle 2 additions (conductor run 230b8e0beab842ef8a75ec63239bdd46, 2026-09-29; assess attempt 93e483be8b9d findings F1–F14 + research attempt 83888459e2f9 survivors S1–S8; artifacts: conductor-delegate spool 93e483be8b9d4540a06ea0e6825924b9.json + -notes.md, 83888459e2f94474b6c5066b03cd3a5a-research.md) -->

### Complete the v0.3.4 release/deploy chain and fix the pending-version skip
- id: `rm-012` | track: reliability | priority: 96.0 | status: in_progress | time-critical
- signals: master = b1de439 'release: v0.3.4 (#16)' while GitHub tags and releases still end at v0.3.3 (verified via API 2026-09-29T02:39Z); the probe venv runs magic_hermes-0.3.3 dist-info; scripts/release.py:133 next_patch_version(current_version()) with current_version() (:53) reading pyproject (already 0.3.4) and previous_tag() (:283) reading tags ⇒ any release run cuts v0.3.5 and folds v0.3.3→v0.3.5 notes; last sync run 2026-09-28T05:10:56Z predates upstream v0.44.0 (11:46:59Z)/v0.44.1 (19:25:03Z), so the next scheduled run (~04:30–05:10Z window) auto-adopts 0.44.0 AND skips v0.3.4 unless this lands first (assess F1+F3)
- acceptance: (a) release.py refuses or re-targets --next-patch when the working-tree version is already a pending release (unit test: pyproject 0.3.4 + latest tag v0.3.3 → resolves v0.3.4, never v0.3.5); (b) tag v0.3.4 + GitHub release published from master b1de439 as-is; (c) probe venv redeployed to 0.3.4 (dist-info) with MAGIC_HERMES_DEPLOY_SMOKE=1 battery green; (d) maestro doctor probe returns rc=0/FAIL 0 across 3 consecutive evaluations
- evidence: gh api repos/codeo1io/magic-hermes/git/refs/tags ends v0.3.4; gh release view v0.3.4; ls probe-venv …/site-packages shows magic_hermes-0.3.4.dist-info; deploy-smoke pytest output; new release.py unit test green
- 2026-09-29 update (cycle-2 compound): code unit implemented — release.py grew latest_tag()/pending_release_version()/next_release_version() so --next-patch RELEASES a pending pre-bumped version (F3 probe: pyproject 0.3.4 over tag v0.3.3 → resolves 0.3.4, never 0.3.5). Operational completion (tag/release/probe-venv deploy/deploy smoke/maestro ×3) rides the merge-release gate. RACE STILL LIVE at 2026-09-29T05:28Z: tags end v0.3.3, master pin 0.43.2, and no 09-29 sync run has fired (last run 09-28T05:10:56Z) — the skip has not materialized.
- 2026-09-29 06:09Z addendum (review-fix, supersedes the race line above): sync run 36526732184 fired 05:33:34Z on the unguarded master and FAILED at the 'Publish Magic-Hermes patch release' battery — exactly the 4 historian-readiness tests (ready:false, 173 passed). Consequences: the skip did NOT materialize (no v0.3.5, no 0.44.0 adoption; the v0.3.6-backfill fallback branch is moot), master's nightly is RED until this batch lands, and the delayed-cron cause of the silent 05:28Z window is resolved (it fired ~5.5h late). The release-path env now carries the same node-24 pin + historian seed ci.yml proved (review-fix this cycle); independent review also fixed commit_and_tag to tag HEAD when the release commit sits in history (P1), so the merge-release transaction can complete.

### Land PR #18 — count cached prompt tokens toward Magic Context pressure
- id: `rm-013` | track: reliability | priority: 92.0 | status: in_progress
- signals: issue #17 (2026-09-28, Jonathannkayy): engine.update_from_response reads uncached input_tokens (engine.py:248 on b1de439) so pressure never builds on cached routes; incident: Discord-DM sessions billed 972,207/969,272/990,206 tokens against a 1M window while session_meta.last_input_tokens=2, 885 tagged items dropped 0; affects Anthropic Messages AND OpenAI-style routes (litellm normalize_usage subtracts cache buckets in both); PR #18 (+61/−1, engine.py + 3 tests) OPEN/MERGEABLE (mergeStateStatus UNSTABLE — no CI exists to validate it, assess F2); upstream v0.44.0 #534 confirms full-prompt usage counting is the intended semantics
- acceptance: PR #18 merged onto master (or superseding commit) with the fix counting cache_read_input_tokens + cache_creation_input_tokens (Anthropic) and the OpenAI cached-token path; regression tests cover both provider shapes; release notes carry the #17 symptom table (billed input vs last_input_tokens) for operators diagnosing stuck sessions
- evidence: gh pr view 18 state=MERGED; pytest -q green including new cached-route pressure tests; a red→green case reproducing the 972K-billed/last_input_tokens=2 shape
- 2026-09-29 update (cycle-2 compound): verified merge-ready at head 8ce116d via GitHub fetch — engine suite 18 passed at head, +61/−1 touching only engine.py/test_engine.py (zero overlap with the batch). Merge rides the pr gate this cycle, as-is (never rebased/re-authored).

### Deliberate upstream 0.44.0 adoption with a coordinated-upgrade runbook
- id: `rm-014` | track: reliability | priority: 90.0 | status: candidate
- signals: npm @cortexkit/pi-magic-context latest=0.44.1; v0.44.0 (2026-09-28T11:46:59Z) moves schema fence 90→91 fail-closed — 0.43.x copies refuse the v91 shared store on next open; local blast radius: shared store 3.76 GB + 479 MB WAL at lane 90 (schema_migrations max=90), deployed probe venv 0.3.3, local package copies 0.43.2 (managed root, 2× .pi, .omo/npm) + stale 0.37.0 (~/.omo/agent); forward-compat smoke PROVEN: full battery vs 0.44.0 with workflow-identical manifest sync = 177 passed/1 skipped (183s); adoption benefits map onto this host: SQLite lock-waiting (#533; historian_guard.py:47-49 already notes conductor sessions minting rows every few seconds on the live WAL), bounded session-history reads in primer/retrospective (material on a 3.76 GB store — the d59758598379 root cause was unbounded work over exactly this store), live historian/dreamer maxTokens reload (#551), GPT-5.6+ 30-min cache-TTL defaults
- acceptance: manifest + package.json → 0.44.0 (workflow-identical via sync_magic_context_release.py); PARITY.md gains a fence-91 section; README gains an UPGRADE/coordination section (stop all hosts on the shared store → update → restart order; doctor lane check before resuming); historian_guard RETIRES_AT_UPSTREAM (:45) re-evaluated against 0.44.0/0.44.1 with the verdict recorded; README documents v0.3.4's doctor budget env knobs (assess F11); full gate green at 0.44.0; released and deployed via the deploy-stage approach (run 2a0fcf9d Approach A — cross-referenced, not re-derived)
- evidence: npm view matches pin; MAGIC_CONTEXT_PACKAGE_ROOT smoke battery green; grep PARITY.md 'fence'; grep README.md UPGRADE + MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S; gh release for the shipping version

### Loader support for the 0.44.1 chunk layout
- id: `rm-015` | track: reliability | priority: 78.0 | status: candidate
- signals: full battery vs 0.44.1 = 46 failed/131 passed, single root cause loader.mjs:209 'Unsupported Magic Context adapter: missing required exports: resolveCacheTtl' — 0.44.1 moved function resolveCacheTtl out of dist/index.js top-level (present 0.43.2:1882 and 0.44.0:1901; only resolveCacheTtlDisplay remains in 0.44.1) and no chunk matches loader.mjs:186-206 deferred-resolution 'as'-re-export regexes; without this the nightly sync fails hard the run after 0.44.0 lands (sidecar refuses to boot)
- acceptance: loader resolves the 0.44.1 export form (recognize in-chunk declarations, not only `export {x as name}` forms); battery green against 0.44.1 with manifest at 0.44.1; loader unit test pins the new export form (extends rm-003's untested-module gap)
- evidence: MAGIC_CONTEXT_PACKAGE_ROOT=<0.44.1 package> pytest -q green; new loader test file collected and passing; node --check clean

### Sync-workflow guardrails: gate series jumps and fence moves
- id: `rm-016` | track: reliability | priority: 75.0 | status: in_progress
- signals: sync-magic-context.yml auto-adopts the next core release with contents:write and no branch protection (gh api …/branches/master/protection → 404); trigger condition is now real and scheduled: the next run = series jump 0.43→0.44 + fence move 90→91, gate proven green, auto-published (assess F4); upstream's own updater advances pins only after checks and offers auto_update:false — same opt-in principle; GitHub-native approach: protected environment with required reviewers on the publish step, or open a PR instead of pushing
- acceptance: a series-change or fence-move release pauses for approval (protected environment) or opens a PR instead of direct master push; same-series patch releases stay automatic; documented rollback (revert pin+manifest commit); three consecutive scheduled runs green
- evidence: workflow diff; a gated run visible in gh run list (queued/action_required) on the next series jump; 3× success runs after
- 2026-09-29 update (cycle-2 compound): implemented — is_series_jump() in scripts/next_magic_context_release.py feeds the workflow's resolve step; auto-resolved series jumps (explicit --version requests bypass) route to an idempotent `sync/magic-context-<tag>` adoption PR and the direct release steps gate on series_jump != true; pull-requests:write added. UNCOMMITTED: protects nothing until the commit gate lands it on master.
- 2026-09-29 06:09Z addendum (review-fix): the explicit-request bypass is gone — a dispatched series jump now aborts unless confirmed with a new allow_series_jump input, and even confirmed jumps route through the adoption PR (a series jump never direct-releases); the adoption branch pushes with --force-with-lease so a stale branch from a closed PR cannot wedge the nightly; the dispatch version is regex-validated before use; and the PR body now documents rollback (close unmerged or revert the merge), closing the 'documented rollback' acceptance term.

### Doctor store-ahead verdict on the schema lane
- id: `rm-017` | track: reliability | priority: 72.0 | status: candidate
- signals: doctor prints 'INFO Shared DB schema migration lane' only (cli.py:578-580) — after any host on the shared store adopts 0.44.x, every 0.43.x host fails closed per-prompt while doctor still says INFO; upstream's own doctor grew exactly this check in 0.44.0 (names each cached copy that cannot open the DB, with the fix); read-only lane SQL already exists (db_schema_lane, cli.py:432)
- acceptance: lane > fence-for-supported-series ⇒ WARN naming remediation (upgrade magic-hermes; identify the newest local copy) — WARN not FAIL, mirroring the maestro-safe precedent (cli.py:572-576); unit test with a mocked ahead lane; docs updated alongside rm-014's UPGRADE section
- evidence: doctor-contract pytest extended (lane-ahead fixture prints WARN); manual run against a lane>90 fixture

### PR-triggered CI test workflow
- id: `rm-018` | track: reliability | priority: 55.0 | status: in_progress
- signals: .github/workflows contains only private-leak-sentinel.yml (gitleaks) — PRs #11/#13/#16 merged with zero test execution; PR #18 sits MERGEABLE with empty statusCheckRollup (assess F2); the full battery is 169–183s on hosted runners — cheap; docs drift recurred twice (README 15-minute cadence claim, rm-005; pin/cadence prose untested)
- acceptance: PR/push workflow runs pytest (pinned npm package via MAGIC_CONTEXT_PACKAGE_ROOT with caching) + ruff + node --check; includes a small docs-consistency test (README cron/pin claims vs workflow + package.json); green required before merge (pairs with rm-016's protection work)
- evidence: workflow file in .github/workflows/; green run URL on the next PR; docs test proven by failing on an introduced stale claim (test-of-test)
- 2026-09-29 update (cycle-2 compound): implemented and PROVEN on a GitHub-hosted runner via ephemeral validation PR #22 — battery 109 passed in 19s, ruff + node --check clean, gitleaks clean; the two red cycles that got there isolated the node>=23 (node:sqlite) and user-config (historian.<harness>.model) requirements, both now baked into ci.yml (docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md). The "green required before merge" clause activates when the commit/pr gates land ci.yml on master.

### Upstream ecosystem presence: dashboard Hermes row + harness listing
- id: `rm-019` | track: customer_experience | priority: 50.0 | status: candidate
- signals: dashboard-v0.18.0 Sessions-viewer harness filter lists OpenCode/OpenCode 2/Pi/OMP — Hermes rows invisible by construction (closes rm-011's open remainder with a fresh answer); upstream wizard/README target the same three harnesses; the only upstream mention of Hermes is closed issue cortexkit/magic-context#79; magic-hermes is upstream's only Hermes surface and upstream does not know it exists
- acceptance: upstream issue/PR proposing Hermes in the harness list + dashboard filter, linked from PARITY.md; PARITY row records the outcome (listed / not listed / listed-after-PR); rm-011 dashboard remainder closed either way
- evidence: upstream issue URL recorded in PARITY.md; upstream release/README diff if accepted

### rm-010 pivot: document the host command-surface limit + `magic-hermes status` stopgap
- id: `rm-020` | track: customer_experience | priority: 40.0 | status: candidate
- signals: hermes-agent 0.21.4 exposes no plugin command-registration / slash-dispatch surface (greps over agent/ + cli/ — rm-010's five commands are blocked at the host); the backing RPCs exist (pressure_state, maintenance_run, dreamer_run_manual, render_context(compaction_off)); session_meta.last_input_tokens doubles as the #17 stuck-session diagnostic
- acceptance: PARITY.md records the host blocker with citation (rm-010's documented-limitation branch); either a read-only `magic-hermes status` CLI printing pressure/lease/pending-drops/last_input_tokens or an explicit decline recorded in PARITY.md
- evidence: PARITY.md row; `magic-hermes status` sample output (or decline note) pasted in the PR

### CLI hygiene: dead conditional, config-backup retention
- id: `rm-021` | track: reliability | priority: 30.0 | status: candidate
- signals: cli.py:958 'if root.exists() and not any(root.glob("node_modules")): pass' is a no-op with no else (assess F12); configure_hermes writes config.yaml.bak.<epoch> into ~/.hermes on every run with no retention bound (cli.py:361, assess F14)
- acceptance: dead branch removed or made meaningful; backup writes keep at most N (configurable) newest backups; unit tests for retention; full gate green
- evidence: git diff; pytest -q green with new retention tests; no unbounded .bak.* accumulation in a simulated repeated-configure run

### Make the historian integration tests hermetic
- id: `rm-022` | track: reliability | priority: 45.0 | status: candidate
- signals: the full battery requires two machine-global conditions to pass — node >= 23 (unflagged node:sqlite for the sidecar historian) and a user-level CortexKit config carrying historian.<harness>.model ($HOME/.config/cortexkit/magic-context.jsonc; resolved via loadPiConfig's user layer → resolveHistorianModel, decision site runtime.mjs:967); fresh machines fail 4 tests in tests/test_runtime_integration.py with ready:false "historian-disabled"; ci.yml currently papers over this with a node-24 pin + a seeded config (see docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md) — the right long-term fix is the tests not depending on operator machines at all
- 2026-09-29 06:09Z addendum (review-fix): the sync workflow's release path is an affected surface too and now carries the same node-24 pin + seeded config (proven live by the failed 05:33:34Z nightly, run 36526732184) — both seed steps become removable when this item lands
- acceptance: the historian tests (or a shared fixture) seed their own minimal historian config into an isolated config path (tmp dir / env override) so the battery passes on a bare machine with no user config; the ci.yml seed step is then removable; battery green with HOME pointed at an empty dir
- evidence: pytest -q green under HOME=<empty tmp>; ci.yml diff removing the seed step; docs/solutions/test-failures doc updated to point at the fixture instead of the workflow step

### Document the runtime requirements the battery actually has
- id: `rm-023` | track: reliability | priority: 25.0 | status: candidate
- signals: node >= 23 (node:sqlite) and the user-level historian config are load-bearing for magic-hermes's test suite and sidecar, yet neither requirement appears in README/docs — every fresh contributor machine and CI runner rediscovers them the hard way (this cycle: two failed CI cycles); the CI incident doc (docs/solutions/test-failures/) records the diagnosis but lives outside the operator-facing docs
- acceptance: README (or docs/) carries a short "Runtime requirements" note: node >= 23 for node:sqlite, the optional user-level CortexKit historian config and what readiness checks do without it (historian disabled, sidecar otherwise functional), linking the solutions doc; docs-consistency test extended to pin the node floor claim against ci.yml
- evidence: README diff; test_docs_consistency assertion on the node floor; grep README 'node'

## Cycle 2 selected batch (conductor run 230b8e0beab842ef8a75ec63239bdd46, prioritize phase, 2026-09-29)

Selected for end-to-end implementation this cycle — the "v0.3.4 pipeline-integrity train" (impact/risk/effort/dependency-screened; every acceptance criterion verifiable with the existing gate + the CI this batch itself adds):

1. rm-018 — PR-triggered CI test workflow (lands first so PR #18 and the guard commits merge green, not untested)
2. rm-013 — land PR #18: count cached prompt tokens toward pressure (user-reported #17 rides the pending v0.3.4 release)
3. rm-012 — release.py pending-version guard + complete the v0.3.4 release/deploy chain (tag, release, probe-venv deploy, deploy smoke, maestro PASS ×3)
4. rm-016 — sync-workflow guardrail gating series jumps and fence moves (must land before the next scheduled sync window ~04:30–05:10Z; PR-mode fallback if repo-admin protection is unavailable)

Deferred with reasons: rm-014/015/017 (the deliberate 0.44.x train — effort-L/risk-H: fence 90→91 coordinated restarts, loader 0.44.1 fix, doctor lane WARN; next cycle, unblocked by rm-016's gate); rm-006 clause advanced by rm-018 (kept open); rm-001/002/003/008 carry; rm-010 host-blocked (pivot rm-020); rm-019/020/021 opportunistic.
Rationale and scoring table: conductor delegate spool 5d0d86ba8d7242e3beeb451cbabf35fd-selected.md.

## Cycle 2 outcome (conductor run 230b8e0beab842ef8a75ec63239bdd46, 2026-09-29)

Implemented pre-review as of this compound phase: **rm-018** (CI workflow +
docs-consistency test + README cadence fix), **rm-013** (PR #18 verified
merge-ready at head 8ce116d; merge itself rides the pr gate), **rm-012** code
unit (release.py pending-version guard + tests), **rm-016** (sync guardrail:
series jumps → idempotent adoption PR, direct release gated), and the docs unit
(this file's cycle-2 sections) — all as one uncommitted batch in the run
worktree at bc21096 (7 modified + 3 new files), verified byte-rebase-clean
onto master b1de439.

Validation outcomes consumed by this phase (no tests re-run): targeted lane —
run_repo_impacted_tests resolved the 7 changed surfaces to 4 suites (stem-match
pulled in tests/test_sync_magic_context_release.py), **35 passed**, ruff +
workflow-YAML clean; full lane — the authoritative validator routed to an
ephemeral GitHub-hosted CI PR (#22): **109 passed** battery + ruff +
node --check + gitleaks green, collection parity 109 confirmed. Note for the
commit gate: 109 is the bc21096-base count — the b1de439 battery (~178)
exists only after the rebase, so re-run the full gate there.

Race status at 2026-09-29T05:28Z: **not yet materialized** — tags end v0.3.3,
master pin 0.43.2, PR #18 OPEN, and no 09-29 sync run had fired (last
09-28T05:10:56Z). The window passed silently; the next sync run still adopts
0.44.0 and cuts v0.3.5 unless the guard + release land first, and master's
workflow is still the unguarded one until the commit gate lands rm-016.

06:09Z addendum (review-fix, supersedes the paragraph above): the next sync
run DID fire — 36526732184 at 05:33:34Z — and failed closed at the release
battery (4 historian ready:false, 173 passed; fresh-runner env, see
docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md).
So the skip never materialized: tags end v0.3.3, no 0.44.0 adoption, and the
v0.3.6-backfill fallback is moot — but master's nightly is RED until this
batch lands. Two consequences for the record: (1) lesson 5's assumption that a
battery-green nightly "will publish" is empirically false on fresh runners —
the 177/1 green was host-local, whose user config satisfies historian
readiness; (2) the hazard is currently self-limiting at the battery and
RE-ARMS when rm-022 makes the tests hermetic, unless this batch (rm-016 guard
+ node-24/seed release env, both review-fixed into the workflow) lands first.
The commit gate is the only remaining blocker to a guarded, green nightly.

### Reusable lessons / prevention rules (pre-review evidence only)

1. **Stacked environment causes share one symptom.** The CI battery's 4
   historian failures had two independent causes (node 22 lacking unflagged
   node:sqlite; missing user-level historian config); fixing either alone
   produced zero visible change. When green-locally/red-in-CI, enumerate
   machine-global deltas (HOME config, node version, caches) and bisect them
   LOCALLY — the decisive probes were `NODE_OPTIONS=--no-experimental-sqlite`
   and a fake-$HOME run, not CI cycles.
2. **Trust the refusal reason string.** `ready:false "historian-disabled"`
   named the config gate exactly; two dead-end hypotheses chased downloadable
   artifacts (embeddings model, ~/.pi/agent) the reason never mentioned.
   Documented with the full diagnosis chain in
   docs/solutions/test-failures/ci-battery-historian-readiness-fresh-runners-2026-09-29.md
   (new items rm-022/rm-023 carry the durable fixes).
3. **git tag listings are version-sorted; test stubs must be too.**
   `--sort=v:refname` puts v0.3.10 after v0.3.4 (not lexicographic). Two
   implement-phase test failures were stubs modeling lexicographic order,
   silently misrepresenting previous_tag()/latest_tag() resolution.
4. **Docs drift recurs — convert prose claims into tested contracts.** The
   README's sync-cadence claim had drifted twice across cycles (rm-005, then
   the 'every 15 minutes' line); tests/test_docs_consistency.py now pins
   cadence + package pin against the workflow and package.json. Same pattern
   applies to any operator-facing claim (rm-023 extends it to the node floor).
5. **Completing the release chain RE-ARMS the auto-release hazard.** The
   pending-version guard makes --next-patch release v0.3.4 — after which the
   next scheduled sync legitimately cuts v0.3.5, adopts 0.44.0 (battery-green,
   so it will publish) and migrates the 3.76 GB shared store to fence 91,
   fail-closing 0.43.x hosts. rm-016's gate is what defuses that, not the
   guard; the two are one system and must land together.
6. **An uncommitted guardrail protects nothing.** rm-016 is implemented and
   green but lives only in the worktree; master's sync workflow remains
   unguarded until the commit gate. Time-critical items are done when they are
   ON MASTER, not when they pass tests.
7. **Engine-side traps this cycle hit (for future cycle planning, reported
   info-only to the engine):** run_repo_impacted_tests' uv branch fails with
   'No module named pytest' on no-uv-lock repos where pytest is a dev extra
   (worked around by provisioning the .venv), and uv-run residue (uv.lock) is
   a FULL_IMPACT surface that must be removed after every runner invocation.

### Next-cycle candidates / context

- **The pre-formed 0.44.x train** — rm-014 (deliberate adoption + fence-91
  coordinated-upgrade runbook) + rm-015 (loader 0.44.1 chunk-layout fix; MUST
  precede the first 0.44.1 sync attempt or the nightly fails hard) + rm-017
  (doctor lane WARN). Unblocked by rm-016's gate landing on master this cycle;
  0.44.0 battery-green is already proven (177/1).
- **New from this cycle's CI incident:** rm-022 (hermetic historian tests —
  retire the ci.yml config seed) and rm-023 (document node>=23 + historian
  config requirements).
- **Commit-gate obligations first:** rebase the batch onto b1de439, re-run the
  full battery at the rebased base (~178 tests), then pr gate merges #18
  as-is, then merge-release completes rm-012's operational chain (tag v0.3.4,
  release, probe-venv deploy + smoke, maestro ×3).
- **Race fallback (pre-recorded):** if v0.3.5 is already cut when those gates
  run, rm-012's acceptance shifts to the guard + a v0.3.6 backfill — see the
  prioritize artifact (5d0d86ba8d7242e3beeb451cbabf35fd-selected.md).
- **Unchanged carry-overs:** rm-001/002/003/008; rm-019/020/021 opportunistic;
  rm-010 host-blocked (rm-020 is the pivot).
- **Worktree carry state at compound close:** 7 modified + 3 new untracked
  (ci.yml, test_docs_consistency.py, docs/solutions/test-failures/…md) at
  bc21096; canonical checkout untouched.

## Cycle 1 selected batch (conductor run 455725e50d504905b0170c26b6ab56f9, prioritize phase, 2026-09-20)

Selected for end-to-end implementation this cycle (impact/risk/effort/dependency-screened; every acceptance criterion locally verifiable with the existing gate — pytest/ruff/node --check/build):

1. `rm-005` README sync-cadence correction — trivial effort, zero risk, restores documentation truth (P2 docs defect).
2. `rm-009` dead code removal (engine.py:175-176; runtime.mjs:1352-1355) — trivial, zero-risk hygiene in files the batch already touches.
3. `rm-011` local subset — README MAGIC_CONTEXT_STORAGE_DIR note + loud skip/fail visibility when the pinned runtime package is absent; dashboard verification stays out (external dependency).
4. `rm-007` allowlist-based read_only gating + plugin lifecycle unit tests — closes the only security-class assessment finding; fully testable locally.
5. `rm-004` adopt upstream v0.42.6 — manual pin bump + PARITY/test notes for the note_ids and ride-only-drop contract changes + full gate at the new pin (forward-compatibility already smoke-proven). Lands last so the gate runs once at the final pin.

Batch dependency: sync the conductor worktree to origin/master 8c157dc before implementing (worktree base 91b4b91 predates the test fix that unblocks 0.42.6).

Deliberately deferred (cannot complete end-to-end this cycle): `rm-001`+`rm-003` (large refactor/new-test tracks, high effort), `rm-006` (acceptance needs three green CI runs), `rm-008` (highest regression-risk fix set — own batch next cycle), `rm-010` (depends on Hermes host command-API investigation), rm-011 dashboard verification (external).

## Cycle 1 outcome (conductor run 455725e50d504905b0170c26b6ab56f9, 2026-09-20)

Implemented (pre-review as of the compound phase; the independent review
subsequently verified the batch sound — see recovery note): rm-004, rm-005,
rm-007, rm-009, rm-011 — 12 files, +189/-27 (git diff --numstat, lockfile
included) in conductor worktree
`/home/agent/.hermes/conductor-worktrees/magic-hermes-31abeceaad/run-455725e50d50-455725e5`
at base 8c157dc; pin 0.42.5→0.42.6 with regenerated lockfile and local
node_modules; version 0.2.7→0.2.8 (pyproject/`__init__`/plugin.yaml);
PARITY.md 0.42.6 contract notes. Gate at the new pin: full pytest
74 passed / 0 skips (127.6s), ruff clean, `node --check` clean, targeted
plugin tests 10/10 — independently re-verified by the review phase
(74 passed / 243.0s, fresh-clone loud-skip simulation included).

### Reusable lessons / prevention rules (pre-review evidence only)

1. **Deny-by-default capability gates.** Any gate over an open-ended host
   tool namespace must be an allowlist (rm-007): a blocklist silently
   admits every future host tool. The `memory` capability in
   `plugin.py:_on_pre_tool_call` still uses a host-file blocklist — next
   cycle candidate to convert or explicitly justify. Optional further
   hardening (review P4): inspect `tool_call`'s inner tool name, since
   other plugins' tools registered into the 'file' toolset stay reachable
   through the deferred-registry bridge.
2. **Squash-merge topology breaks fast-forward worktree sync.** PR #1 was
   squashed, so the worktree base 91b4b91 was not an ancestor of
   origin/master 8c157dc; a pointer move (`git reset --hard origin/master`
   on a clean branch) synced without creating commits. Always verify with
   `git merge-base --is-ancestor` first.
3. **Upstream adoption can be validated ahead of CI.** `npm pack` both
   versions + `MAGIC_CONTEXT_PACKAGE_ROOT` smoke test proved 0.42.6
   compatibility while the scheduled sync run was failing on unrelated
   `dreamer_tool_schemas` 30s-handshake flakiness (fixed on the 8c157dc
   lineage). Manual pin bumps remain valid when the local full gate is
   green; CI then confirms.
4. **Silent skips inflate confidence.** The integration suite could skip
   invisibly on fresh clones (no node_modules), so green runs did not
   validate the bridge (fixed, rm-011). Rule: every environment-availability
   `skipif` must emit a collection-time warning with remediation.
5. **Docs drift from automation.** README claimed a 15-minute sync cadence
   while the cron has been daily since commit 507c759. Rule: prose that
   restates a workflow's schedule should name the workflow file; consider a
   docs-consistency check in CI (candidate below).
6. **Version bumps mirror the release contract.** Validated upstream
   adoption bumps the patch version in three places (pyproject.toml,
   `src/magic_hermes/__init__.py`, plugin.yaml) exactly as
   scripts/release.py does for scheduled syncs.
7. **Renderer-owned files are write-through hazards.** A file carrying the
   'managed by hermes-roadmap render' marker is wholesale-replaced from the
   store on every sync (hermes-autonomy/orchestrator/core.py:1341-1357);
   uncommitted hand edits — including append-ahead-of-marker convention
   content — are destroyed with no git recovery. Protection: commit before
   the next sync window, mirror items into the store
   (runtime/roadmap-state/<slug>/items.jsonl), or drop the marker (the
   clobber guard then preserves hand-written files verbatim).

### Next-cycle candidates / context

- **rm-008** (runtime reliability: readline deadline, shutdown respawn
  race, cache eviction) — pre-selected as the next batch anchor.
- **rm-006** (sync-workflow hardening) — now also carries "confirm the
  first green scheduled sync run at the 0.42.6 pin"; the 2026-09-19
  failure chain is fully diagnosed (test flakiness, not connector code).
- **rm-001/rm-003** (complexity refactor, loader.mjs coverage) unchanged.
- **rm-010** (slash-command parity) — needs a Hermes host-surface
  feasibility probe before committing.
- **rm-011 remainder** — dashboard hermes-row verification.
- **Memory-capability allowlist conversion** (lesson 1) and **tool_call
  inner-name inspection** (review P4) as new security-hygiene candidates.
- Carry-over state: worktree = 12 uncommitted modified files at 8c157dc
  (+ worktree-local node_modules at 0.42.6); canonical checkout =
  ROADMAP.md uncommitted planning edits (this file); 27 ignored *.bak
  clutter files in the canonical checkout root.

## Cycle 2 additions — context and proposed batch (run 230b8e0beab842ef8a75ec63239bdd46, 2026-09-29)

- Sources: fresh adversarial assessment at origin/master b1de439 (attempt 93e483be8b9d, findings F1–F14; baseline 177 passed/1 skipped, ruff+node clean) + repository-extension research (attempt 83888459e2f9, survivors S1–S8 with upstream npm/GitHub evidence; artifacts in the conductor delegate spool). No prior-round findings were recycled.
- Time-critical state at authoring (2026-09-29T02:39Z): v0.3.4 is merged to master but untagged/unreleased/undeployed; upstream released v0.44.0 + v0.44.1 on 09-28 AFTER the last sync run (05:10Z); the next scheduled sync (~04:30–05:10Z window) will therefore adopt v0.44.0 green and auto-cut v0.3.5, silently skipping v0.3.4 (rm-012 is the race; rm-016 is the durable guardrail).
- Proposed batch order: rm-012 (race) → rm-013 (user waiting, MERGEABLE) → rm-018 (makes everything else reviewable) → rm-016 → rm-014 + rm-015 + rm-017 as one 0.44.x-coordinated release (rm-016 lands before rm-014 so the series jump is gated; rm-015 must precede the sync's first 0.44.1 attempt) → rm-019 / rm-020 / rm-021 opportunistic.
- Dependencies: rm-014 depends on rm-012's release guard and pairs with rm-017 (lane WARN) in the same release; rm-013 is file-independent of the 0.44.x cluster (pure Python engine seam); deploy-stage mechanics are owned by run 2a0fcf9d's Approach A (cross-referenced, deliberately not re-derived here).

<!-- RECOVERY + PROTECTION NOTE (independent_review fix, conductor attempt fdfcd8a96cd64711b4e84b6a05da5877, 2026-09-20): the 2026-09-20 01:42:39 UTC roadmap-sync run (run_e4abe044fc044133) wholesale-replaced this file from its store, destroying the uncommitted +124-line cycle-1 content. All content above was re-materialized verbatim from the authoring session transcript (roadmap/prioritize/compound phase tool calls) with three review-directed corrections: statuses now use the renderer's RoadmapStatus vocabulary (in_progress for the implemented five), rm-010's track is customer_experience, and the outcome section cites +189/-27 numstat. The managed marker is deliberately ABSENT so the sync's clobber guard (hermes-autonomy/orchestrator/core.py:1341-1357) preserves this hand-written file verbatim. The eight items rm-004..rm-011 are also mirrored into the renderer's store at runtime/roadmap-state/magic-hermes/items.jsonl (status vocabulary compliant), and the full cycle-1 content is archived in docs/ROADMAP-CYCLE1.md (a path the renderer never writes). To return this file to managed mode: commit it, re-add the marker, and let the next sync render from the store. -->
