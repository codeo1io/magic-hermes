# magic-hermes — Cycle 1 roadmap archive (conductor run 455725e50d504905b0170c26b6ab56f9)

> Archive copy of the cycle-1 roadmap content (items rm-004..rm-011, batch
> selection, outcome, lessons). The renderer only ever writes ROADMAP.md at
> the repo root, so this file is durable against roadmap-sync rewrites.

## Incident record (2026-09-20)

The 2026-09-20 01:42:39 UTC hermes-autonomy roadmap-sync run
(run_e4abe044fc044133) wholesale-replaced ROADMAP.md from its store,
destroying the uncommitted +124-line cycle-1 content (no git object held
it). Root cause: files carrying the 'managed by hermes-roadmap render'
marker are rewritten from the store on every sync
(hermes-autonomy/orchestrator/core.py:1341-1357); the clobber guard
protects only marker-less hand-written files. Found by independent_review
(attempt 1d9ef0a4b9494b1982f38a44c0be3856, P1); fixed by the review_fix
attempt fdfcd8a96cd64711b4e84b6a05da5877, which re-materialized ROADMAP.md
from the authoring session transcript, dropped the marker (guard now
preserves the file verbatim), and mirrored all 8 items into the renderer's
store (runtime/roadmap-state/magic-hermes/items.jsonl — a simulated render
emits all 11 items with 5x in_progress).

Recovery corrections applied during re-materialization (review-directed):
statuses use the RoadmapStatus vocabulary (in_progress for the implemented
five); rm-010's track is customer_experience; the outcome section cites
+189/-27 numstat.

---

<!-- cycle 1 additions (conductor run 455725e50d504905b0170c26b6ab56f9, 2026-09-20, assess + research evidence; recovered 2026-09-20 after the roadmap-sync clobber — see recovery note at end of file; conductor-delegate spool 4b696136f70e4ce5a83a61cf44bf9e9f.json and cd0305620de0477bbbc5e5a17cb1c9f9.json) -->

### Adopt upstream Magic Context v0.42.6
- id: `rm-004` | track: reliability | priority: 88.0 | status: in_progress
- signals: npm dist-tags latest=0.42.6 (published 2026-09-19T02:59Z) vs pinned 0.42.5 (package.json); 2026-09-19 04:26Z sync run failed in pytest (2 plugin tests, 'Runtime timed out after 30.0s during dreamer_tool_schemas'); fix already on master as 8c157dc; forward-compat smoke of the current bridge against extracted 0.42.6 passed (hello/bind/render_context/ctx_note/dreamer_tool_schemas/doctor, core_symbols_ready=true)
- acceptance: package.json pins 0.42.6; a sync workflow run completes green and lands the pin; test/PARITY notes cover the 0.42.6 contract changes (ctx_note single note_ids field; queued ctx_reduce drops ride the next cache bust; filler args ignored on ctx_expand/ctx_memory/ctx_search/ctx_reduce); full gate green at the new pin
- evidence: gh run list --workflow=sync-magic-context.yml shows success after 8c157dc; npm view @cortexkit/pi-magic-context version matches package.json; PYTHONPATH=src pytest -q passes at 0.42.6
- outcome: implemented in cycle 1 (pin bumped locally + full gate 74 passed / 0 skips at 0.42.6); the green-CI clause remains open under rm-006

### Correct README sync-cadence claim
- id: `rm-005` | track: reliability | priority: 85.0 | status: in_progress
- signals: README.md 'every 15 minutes' vs .github/workflows/sync-magic-context.yml cron '7 0 * * *' (commit 507c759 'Run Magic Context sync daily')
- acceptance: README states the daily 00:07 UTC cadence (or the cron is restored to 15 minutes); no other stale cadence claims
- evidence: grep -n 'minutes' README.md returns only accurate claims; git diff README.md
- outcome: implemented in cycle 1 (README now states 'daily at 00:07 UTC')

### Allowlist-based read_only gating and plugin lifecycle tests
- id: `rm-007` | track: reliability | priority: 80.0 | status: in_progress
- signals: src/magic_hermes/plugin.py:83-87 enforces read_only via hardcoded blocklist {write_file,patch,terminal,process}; plugin.py:61-72 subagent registration depends on subagent_start firing on the launching thread (threading.local set at :307-308); no unit tests for _on_pre_tool_call gating, _matching_trace (:181), _request_text truncation (:140), or subagent hook registration (tests/test_plugin.py covers none of these)
- acceptance: read_only enforced deny-by-default (allowlist of read-only tools, mirroring the memory capability's :97-102 pattern); unit tests cover capability gating for read_only+memory children, stop-event trace correlation, request-text truncation boundaries, and child registration/unregistration; all pass in CI
- evidence: pytest tests/test_plugin.py -q green with the new tests; grep shows no mutating tool outside the allowlist passes _on_pre_tool_call
- outcome: implemented in cycle 1 (deny-by-default allowlist {read_file, search_files, tool_search, tool_describe, tool_call} + ctx_* prefix; 5 new unit tests; independent review verified tool_call cannot reach mutating core tools via toolsets.py:11 + tool_search.py:142-155)

### Harden sync-workflow reliability
- id: `rm-006` | track: reliability | priority: 70.0 | status: candidate
- signals: last scheduled runs = failure 11h52m (2026-09-16), cancelled 30m10s (09-18), failure 16m36s (09-19); runner pytest 637s vs 77s local; actions/checkout@v4, setup-python@v5, setup-node@v4 emit Node-20 deprecation warnings in run logs
- acceptance: per-step timeout-minutes set on every sync-workflow step; action versions bumped past Node-20 deprecation; pytest split or timeout-tolerant on the self-hosted runner (no 30s dreamer_tool_schemas flakes); three consecutive scheduled runs green
- evidence: gh run list --workflow=sync-magic-context.yml shows 3 consecutive successes; run logs free of Node-20 deprecation warnings

### Runtime reliability hardening (timeouts, shutdown race, unbounded caches)
- id: `rm-008` | track: reliability | priority: 60.0 | status: candidate
- signals: src/magic_hermes/runtime.py:469-481 select+blocking readline lets a mid-line stall block past the call deadline (POSIX-only); src/magic_hermes/memory_provider.py:212-215 shutdown() does not join background threads so a thread past its is_set() check can respawn a Node sidecar via _ensure_process (runtime.py:347); memory_provider.py:48 _cached_context never evicted per session; bridge/runtime.mjs:2209 dreamerVirtualSessions map grows unboundedly
- acceptance: call deadline enforced per-line (bounded readline); shutdown joins/cancels background threads and cannot respawn after close; session caches evicted on session end with bounded size; regression tests for each; full gate green
- evidence: pytest -q green including new shutdown/eviction tests; no orphan node processes after a forced-shutdown test (pgrep evidence)

### Hermes slash-command parity (/ctx-status, /ctx-flush, /ctx-recomp, /ctx-wrapup, /ctx-dream)
- id: `rm-010` | track: customer_experience | priority: 50.0 | status: candidate
- signals: upstream 0.42.6 README ships five slash commands on Pi/OMP; the connector RPC already exposes pressure_state, maintenance_run, dreamer_run_manual and render_context(compaction_off) which back them; Hermes users currently have only /compress; docs/PARITY.md marks command UI as N/A
- acceptance: either the five commands are exposed on Hermes via the host plugin command surface (each mapped to the listed RPC methods with triggerTurn:false semantics), or PARITY.md documents the host limitation with the investigation result
- evidence: PARITY.md row updated; live /ctx-status (or equivalent) output pasted in the PR description, or a documented host-capability blocker with citation

### Docs/DX: storage override, dashboard visibility, 0.42.6 parity notes
- id: `rm-011` | track: customer_experience | priority: 45.0 | status: in_progress
- signals: upstream documents MAGIC_CONTEXT_STORAGE_DIR for hosts isolating XDG_DATA_HOME; magic-hermes README never mentions it (the Node child inherits env, so it works but is undiscoverable); upstream Dashboard releases (dashboard-v0.16.0) mention only Pi/OMP/OpenCode harnesses — hermes-row visibility unverified; fresh clones silently skip the integration suite when the pinned package is absent (tests/test_runtime_integration.py:19 skipif)
- acceptance: README documents MAGIC_CONTEXT_STORAGE_DIR (and the shared-store semantics); dashboard hermes-row visibility verified and the outcome recorded in PARITY.md; CI or pytest reports a loud skip-count/fail-fast marker when the runtime package is missing so green runs cannot hide an unvalidated bridge
- evidence: grep README.md for MAGIC_CONTEXT_STORAGE_DIR; PARITY.md dashboard row; pytest -q run on a fresh clone shows an explicit runtime-missing warning
- outcome: local subset implemented in cycle 1 (README STORAGE_DIR note; loud collection-time skip warning with remediation — proven by fresh-clone simulation in independent review); dashboard verification still open

### Dead code removal
- id: `rm-009` | track: reliability | priority: 30.0 | status: in_progress
- signals: src/magic_hermes/engine.py:175-176 assigns _config_threshold_percent/_base_threshold_percent never read anywhere; bridge/runtime.mjs:1352-1355 else-branch unreachable (systemIndex<0 returns at :1291-1296)
- acceptance: dead attributes and branch removed with no behavior change; full gate green
- evidence: git diff; ruff check + pytest -q + node --check all pass
- outcome: implemented in cycle 1

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

<!-- RECOVERY + PROTECTION NOTE (independent_review fix, conductor attempt fdfcd8a96cd64711b4e84b6a05da5877, 2026-09-20): the 2026-09-20 01:42:39 UTC roadmap-sync run (run_e4abe044fc044133) wholesale-replaced this file from its store, destroying the uncommitted +124-line cycle-1 content. All content above was re-materialized verbatim from the authoring session transcript (roadmap/prioritize/compound phase tool calls) with three review-directed corrections: statuses now use the renderer's RoadmapStatus vocabulary (in_progress for the implemented five), rm-010's track is customer_experience, and the outcome section cites +189/-27 numstat. The managed marker is deliberately ABSENT so the sync's clobber guard (hermes-autonomy/orchestrator/core.py:1341-1357) preserves this hand-written file verbatim. The eight items rm-004..rm-011 are also mirrored into the renderer's store at runtime/roadmap-state/magic-hermes/items.jsonl (status vocabulary compliant), and the full cycle-1 content is archived in docs/ROADMAP-CYCLE1.md (a path the renderer never writes). To return this file to managed mode: commit it, re-add the marker, and let the next sync render from the store. -->
