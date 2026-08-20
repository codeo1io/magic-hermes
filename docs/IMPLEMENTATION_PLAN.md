# Magic Hermes — Full Magic Context Parity Implementation Plan

> **Implementation status: complete.** The plan below is the executed engineering
> specification for the current `feat/hermes-connector` implementation. The
> evidence-backed capability matrix and host-specific boundaries are maintained in
> [PARITY.md](PARITY.md).

## 1. Completion definition

`magic-hermes` is complete when Hermes obtains the same functional Magic Context
outcomes as the supported upstream `@cortexkit/pi-magic-context` 0.38.x runtime,
using only public Hermes integration rails and without modifying Hermes core.

Literal Pi/OpenCode presentation surfaces (Pi status line, OpenCode sidebar,
slash-command palette widgets) are not required. Their underlying state and
operations must still be available through Hermes-compatible tools/status/logging.

A capability is **Full** only when policy, persistence, validation, scheduling,
leases, and mutations delegate to upstream Magic Context code. A capability may be
**Hermes-shaped Full** when the host lifecycle seam differs but the resulting
Magic Context state and behavior are equivalent.

## 2. Non-negotiable invariants

1. **One configuration authority.** Magic Context behavior is resolved only by
   upstream MC from `~/.config/cortexkit/magic-context.jsonc` plus
   `<project>/.cortexkit/magic-context.jsonc`, including upstream project-security
   merge rules. Hermes YAML contains only host wiring/trust permissions.
2. **One durable state authority.** MC SQLite is canonical for compartments,
   tags, pending operations, memories, notes, task schedules, leases, primers,
   embeddings, Git state, and maintenance state. Python threads/Futures are never
   authoritative durable schedulers.
3. **One context owner.** With `context.engine: magic-context` and MC
   `compaction.enabled=true`, no Hermes-native compressor or pruning algorithm may
   independently rewrite the conversation.
4. **MC policy, Hermes execution.** MC decides *what/when/model*. Hermes provides
   model calls, child-agent execution, lifecycle notifications, and the request
   context seam.
5. **No policy reimplementation in Python.** Thresholds, protected-tail rules,
   cache TTL, trigger gates, task due-times, retry/backoff, leases, promotion
   thresholds, search scoring, and transform semantics must come from upstream.
6. **No duplicate config schema/defaults.** The adapter may expose diagnostic
   summaries but never become a second config parser.
7. **Fail safely.** Context transforms fail open to the unmodified request unless
   upstream `fail_closed_blocking` requires a blocking failure. Durable mutation
   requests are never automatically replayed after ambiguous transport failure.
8. **Cross-harness coexistence.** Pi, OpenCode, and Hermes sharing the MC DB must
   coordinate through the same upstream lease/schedule state and cannot duplicate
   historian/Dreamer/Git/embedding work.
9. **Live project resolution.** Every session/project switch resolves the current
   project through upstream MC. Plugin discovery cwd must never freeze policy.
10. **Evidence before parity claims.** Every `Full` row requires unit/runtime/E2E
    evidence described below.

## 3. Target architecture

```text
Hermes
  |
  +-- ContextEngine lifecycle ---------+
  +-- MemoryProvider lifecycle --------+---- MagicContextCoordinator
  +-- ctx.llm auxiliary completions ---+          |
  +-- subagent_lifecycle children -----+          |
  +-- plugin status/diagnostics -------+          v
                                              RuntimeClient
                                                  |
                                           Node MC host adapter
                                                  |
                                    upstream Magic Context 0.38.x
                                                  |
                              shared config + shared CortexKit SQLite
```

### Coordinator responsibilities

The Python coordinator/engine owns only host concerns:

- current Hermes session id / cwd / model geometry;
- serializing host lifecycle notifications to MC;
- launching bounded background executor threads;
- invoking Hermes auxiliary LLM calls requested by MC;
- launching Hermes child agents requested by MC task plans;
- bounded shutdown drain;
- reporting diagnostics.

It must not own scheduler policy or durable queues.

## 4. Hermes rails and their intended use

| Hermes rail | Magic Context use |
|---|---|
| `ContextEngine.select_context()` | request-time MC transform/render/materialization |
| `ContextEngine.on_turn_complete()` | observe completed turn; poll/schedule historian, Dreamer, maintenance |
| `ContextEngine.update_from_response()` | report input-token/model/cache-adjacent usage state |
| `ContextEngine.should_compress()` | emergency synchronous host gate only |
| `ContextEngine.compress()` | manual/emergency compatibility materialization only |
| `ContextEngine.get_tool_schemas()/handle_tool_call()` | exact upstream `ctx_*` schemas/executors |
| `MemoryProvider` | Hermes memory injection lifecycle backed by upstream MC render |
| `ctx.llm.complete()` | historian/editor/repair and non-agentic MC model operations |
| `PluginContext.subagent_lifecycle` | tool-using Dreamer child agents |
| Hermes plugin config | trust/permissions only; never MC policy |

`subagent_lifecycle` is valid during `on_turn_complete()` because Hermes binds the
active parent around the entire `run_conversation()` call.

## 5. Full capability matrix

### A. Configuration / identity / compatibility

| Capability | Upstream authority | Hermes rail | Durable state | Failure semantics | Acceptance |
|---|---|---|---|---|---|
| User+project config merge | `loadPiConfig` / upstream security merge | runtime `bind` | config files | fail closed on invalid required config | conflicting user/project fixture resolves exactly upstream |
| Project identity | `resolveProjectIdentityForSession` | session start/switch | MC project registry | fail closed when identity unavailable | cwd/project switching E2E |
| Embedding registration | `ensureProjectRegisteredFromPiDirectory` | bind/maintenance | MC SQLite | preserve last-known-good per upstream | config mutation reload test |
| Version compatibility | adapter package version + required-symbol capability set | runtime preflight | none | fail closed with actionable diagnostic | supported 0.38.x passes; missing symbol/other series fails before mutation |
| Config source diagnostics | upstream `loadedFromPaths`/warnings | status/doctor | none | diagnostic only | reports user/project contributors without secrets |

### B. Raw transcript / identity / restart

| Capability | Authority | Hermes rail | Acceptance |
|---|---|---|---|
| Hermes→Pi normalized message conversion | upstream conversion plus deterministic Hermes stable IDs | all ingest seams | user/assistant/tool arcs match raw MC rows |
| Stable § identity | upstream tagger/tag tables | `select_context` | tags persist and replay across renders |
| Raw FTS indexing | upstream indexer | render/observe/tool | exact messages searchable |
| Branch/rewind reconciliation | upstream reconciliation primitives where possible | session switch/render | rewound branch does not leave searchable stale branch rows |
| Rich raw reconstruction | persisted normalized raw representation / upstream provider | `ctx_expand` | post-restart expansion preserves tool calls/results, not FTS text only |

### C. Context ownership / scheduler / cache behavior

| Capability | Authority | Rail | Failure | Acceptance |
|---|---|---|---|---|
| Percentage threshold | MC scheduler | request/turn | no Python threshold policy | per-model fixture |
| Absolute token threshold | MC scheduler | request/turn | same | overrides percentage fixture |
| Cache TTL / defer→execute | MC scheduler | `select_context` | request remains intact on error | queued drop deferred then materialized after MC execute |
| Protected tags | MC config/tag operations | `select_context` | never bypass | protected tag cannot drop |
| Commit-cluster trigger | MC historian trigger | `on_turn_complete` | no duplicate worker | low-pressure cluster trigger fixture |
| Emergency pressure | MC escalation bands | `should_compress` | host blocks only emergency | 95%/upstream band E2E |
| Manual `/compress` | MC forced/emergency semantics | `compress(force=True)` | bounded synchronous fallback | manual run publishes upstream compartment |
| Gateway hygiene | selected MC ContextEngine | gateway agent | no native double compression | real gateway hygiene E2E |
| `compaction.enabled=false` | upstream config semantics | all rails | knowledge layer stays active, context mutation off | no § tags/reductions/historian; memory/search/notes remain usable |
| proactive Hermes prune | disabled/no-op unless MC requests | `prune_tool_results_only` | never native prune MC session | regression proves no independent mutation |

### D. Request-transform pipeline

The ordering target is upstream-equivalent:

1. resolve live project/config/model state;
2. reconcile/assign stable message ids;
3. in compaction-on mode inject temporal markers;
4. construct/tag transcript and replay persisted statuses;
5. evaluate MC scheduler and materialize only MC-due pending operations;
6. render MC m[0]/history/memory/docs/mural state as applicable;
7. schedule historian based on upstream trigger state (turn-complete rail in Hermes);
8. apply note nudges;
9. run auto-search hint against **prior** durable context, never the live prompt itself;
10. apply todo/synthetic context cues where Hermes can faithfully represent them;
11. persist/index the completed live request state;
12. return request-only transformed messages without mutating Hermes canonical transcript.

| Surface | Upstream function/state | Hermes implementation target | Acceptance |
|---|---|---|---|
| § tags | tagger/transcript | already request-time | persistence + reduction E2E |
| queued reductions | pending-op APIs | already request-time | defer→execute→restart |
| compartment/history render | MC compartment render | request system block | history budget/decay fixture |
| temporal gap markers | `injectPiTemporalMarkers` | upstream call before tagging | >5m gap fixture; replay stable |
| note nudges | `applyNoteNudges` | upstream call after transform | eligible smart/ordinary note fixture |
| auto-search | `runAutoSearchHintForPi` | upstream call after prior indexing | prior memory/history hint; no self-match |
| mural | upstream injection/render functions | request system block | enabled/disabled fixture |
| todo cues | upstream synthetic todo state where host-neutral | request-only synthetic message | state replay/no duplicate cue |
| prompt guidance | upstream prompt-surface builder | request system message / plugin system block | preset/model config changes reflected |
| compaction markers | MC persisted boundary state | request renderer, not Hermes native summary | restart/resume boundary fixture |

### E. `ctx_*` tools

| Tool | Target | Acceptance |
|---|---|---|
| `ctx_search` | exact upstream schema/executor | FTS + semantic + Git + primer + note sources |
| `ctx_expand` | exact executor with rich restart provider | plain + tool-call range pre/post restart |
| `ctx_reduce` | exact executor + § lifecycle | queue/protect/materialize/replay |
| `ctx_note` | full including smart-note fields once evaluator runs | write/update/dismiss/surface-condition E2E |
| `ctx_memory` | exact executor | write/read/update/merge/archive + workspace/project scope |
| `todowrite` if MC exposes host-neutral schema | expose only when Hermes representation is faithful | state/update/replay |

### F. Historian

| Capability | Upstream authority | Execution | Acceptance |
|---|---|---|---|
| trigger decision | `resolvePiHistorianTriggerInputs` + `checkCompartmentTrigger` | `on_turn_complete` | threshold + commit cluster fixtures |
| boundary/chunk | trigger boundary + `readSessionChunk` | Node runtime | protected tail exact |
| prompt/reference/memory | upstream builders | Hermes `ctx.llm` | prompt snapshot parity |
| model/fallback route | resolved historian config | Hermes trusted model override | project/user-security routing test |
| repair | upstream validator/repair prompt | Hermes `ctx.llm` | malformed first pass repairs |
| two-pass editor | upstream editor | Hermes `ctx.llm` | valid replacement / invalid fallback |
| publication | upstream compartment/fact/event APIs | MC SQLite | transaction integrity |
| user-memory candidates | upstream collection gate | publish side-channel | configured task gate fixture |
| lease | upstream compartment lease | background worker | two-process exclusion |
| async normal path | MC trigger + Python executor only | daemon worker | foreground returns before historian finishes |
| emergency sync path | same MC APIs | `compress` | overflow safety E2E |
| crash cleanup | upstream lease expiry/meta reconciliation | next process | kill worker then recover without duplicate compartment |
| post-publish maintenance | upstream embedding/primer scheduling | maintenance executor | newly published compartment enters downstream pipeline |

### G. Dreamer — all 12 canonical tasks

Canonical tasks:
`map-memories`, `verify`, `verify-broad`, `curate`, `compress-cues`,
`classify-memories`, `retrospective`, `maintain-docs`,
`evaluate-smart-notes`, `review-user-memories`, `promote-primers`,
`refresh-primers`.

Shared rules for every task:

- Runtime configs from `buildDreamTaskRuntimeConfigs`.
- Due state from upstream task schedule planner.
- Gate/backlog logic from upstream.
- Lease key/domain from upstream `leaseKeyFor`.
- Retry and next-due advancement from upstream.
- Task model/fallback/thinking/timeout from upstream config.
- Python may execute a planned task but may not decide it is due.
- Tool-using tasks run through `PluginContext.subagent_lifecycle` with the minimum
  toolsets needed; host-agnostic deterministic tasks call upstream directly.
- Task completion is committed to MC schedule state only after the actual child or
  upstream task succeeds.

| Task | Upstream behavior to preserve | Hermes execution strategy | Acceptance |
|---|---|---|---|
| `curate` | canonical curate prompt; `ctx_memory` tool mutations | real Hermes child, `context_engine` toolset | child performs real memory mutation; due state advances once |
| `maintain-docs` | canonical docs prompt + protected-region enforcement | Hermes child with filesystem-safe task toolsets + MC post-validation | docs change + protected region test |
| `map-memories` | batched mapping, lease/backlog, module path fallback | expose upstream batch planner; Hermes child/model executor per batch | mapping backlog drains/retries |
| `verify` | evidence verification and memory mutation | upstream planner + Hermes execution callbacks | verified/updated/archived delta |
| `verify-broad` | broad-cycle progress semantics | same, preserving cycle state | partial successful cycle advances correctly |
| `classify-memories` | staged classification/backlog | upstream planner + Hermes model/child executor | stage transition/backlog drain |
| `compress-cues` | mural cue compression | upstream batch planner + Hermes model executor | cues written; disabled mural becomes successful no-op |
| `retrospective` | friction gate, deepen pass, watermark, memory/user-observation writes | upstream planner + Hermes model child/callbacks | watermark advances; duplicate window suppressed |
| `review-user-memories` | candidate promotion/merge/dismiss | upstream planner + Hermes model executor | candidate state transitions |
| `promote-primers` | promotion threshold and embedding registration | upstream deterministic/task executor | candidate→primer promotion |
| `refresh-primers` | stale primer regeneration from raw evidence | upstream planner + Hermes executor | stale primer refreshed once |
| `evaluate-smart-notes` | evaluator/compiled-condition ownership | upstream evaluator; Hermes model only when requested | due note surfaced, schedule state persisted |

If an upstream function is too tightly bound to the OpenCode SDK client, the adapter
must expose the *upstream planner/state machine* and replace only the host execution
primitive. Reimplementing task policy in Python is prohibited.

### H. Primer pipeline

1. historian produces upstream primer candidates;
2. candidates persist with upstream evidence/provenance;
3. `promote-primers` uses upstream threshold/gates;
4. promoted primers are embedded through upstream embedding registration;
5. `refresh-primers` uses canonical raw evidence provider;
6. `ctx_search` returns primer source;
7. restart/cross-harness use the same rows.

Acceptance: historian candidate → scheduled promotion → searchable primer → stale
refresh, across a runtime restart.

### I. Embeddings / Git

| Capability | Authority | Execution | Acceptance |
|---|---|---|---|
| provider registration | upstream config/registration | bind | local/off/remote fixture without leaking credentials |
| compartment embedding | `getEmbeddingCoverageStatus`/`runEmbedDrain` | background maintenance | published compartment becomes embedded |
| memory embeddings | upstream provider/backlog | MC maintenance/task | write/update invalidates/re-embeds |
| primer embeddings | upstream | Dreamer/maintenance | promoted primer semantically searchable |
| Git indexing | `sweepGitCommits` | background maintenance | commit row + FTS row |
| Git embeddings | upstream backlog drain | same sweep/registration | semantic Git search when embedding enabled |
| generation/model change | upstream generation fingerprint | config reload/maintenance | stale generation not queried; replacement created |
| provider failure | upstream fallback/observation semantics | background | foreground request unaffected; retry state retained |

### J. Memory/provider integration

- Keep Hermes `MemoryProvider` as the selected external-memory rail.
- It must use upstream active/permanent visibility and budget logic.
- Avoid duplicate system guidance between MemoryProvider and ContextEngine.
- Refresh after MC memory mutations and task completions.
- Project/session switch must invalidate cached render and bind live cwd.
- Workspace/shared-project behavior must use upstream identity expansion, not Python.

Acceptance: new session recalls upstream memory without tool call; project switch
changes injected memory; archived/expired memory disappears per upstream rules.

### K. Failure, concurrency, recovery

Every durable task must be tested for:

- two Hermes sessions in one process;
- two Hermes processes;
- Hermes racing Pi/OpenCode against the same MC DB;
- worker crash after lease acquisition;
- provider timeout before mutation;
- transport timeout after ambiguous mutation (no replay);
- malformed model output;
- DB busy/contention;
- stale lease expiry/recovery;
- process shutdown while work is active;
- project/model switch during background work.

Policy:

- Request transform failure: fail open unless upstream fail-closed says otherwise.
- Historian failure: original transcript remains; abort/release if ownership retained.
- Dreamer/maintenance failure: record upstream retryable/terminal state; never block
  foreground conversation.
- Tool mutation transport ambiguity: surface error; never automatically replay.
- Config/schema/version incompatibility: fail loudly before context mutation.

## 6. `compaction.enabled=false` exact behavior

When MC shared config disables compaction:

- no MC § prefix/tag lifecycle;
- no `ctx_reduce`;
- no pending drop/materialization;
- no temporal/context-management markers;
- no historian/compartment preparation;
- no MC reclaim/folding/synthetic context-management todos;
- fail-closed compaction blocking is inert;
- raw FTS indexing remains;
- memory, docs/user-profile/key-file injection remains where supported;
- Dreamer, notes, `ctx_search`, `ctx_expand`, `ctx_memory`, embeddings remain;
- Hermes native compression ownership is a host configuration choice, but
  `magic-hermes` itself must not secretly invoke a second compressor.

The adapter's runtime renderer must branch on this mode before tagging.

## 7. Version/symbol capability fence

The adapter currently reaches bundled private symbols. Completion requires:

1. maintain an explicit required-symbol manifest grouped by feature;
2. validate all core required symbols at runtime startup;
3. validate optional feature groups before advertising their tools/status;
4. expose a `doctor` runtime method reporting package version, resolved package
   root, config sources, DB health, feature-group symbol readiness, project
   identity, selected context ownership, and background-task capability;
5. never print credentials or raw embedding keys;
6. reject unsupported major/minor series before opening/mutating the DB when
   feasible;
7. test a deliberately missing symbol using a fixture/shim.

## 8. Implementation phases and dependency order

### Phase 0 — freeze baseline and planning evidence

- Save this plan.
- Record current git diff/status and baseline tests.
- No parity status upgraded without acceptance evidence.

### Phase 1 — ownership correctness and compaction-off

- Add runtime capability/doctor manifest.
- Implement exact `compaction.enabled=false` renderer branch.
- Remove any remaining Python MC-policy defaults where upstream can return an
  effective value.
- Ensure Hermes proactive prune stays no-op.

Exit: compaction-on and compaction-off ownership tests pass.

### Phase 2 — complete upstream request-transform order

- temporal markers;
- note nudges;
- auto-search ordering (already substantially implemented);
- prompt guidance and host-neutral injected context;
- mural rendering;
- todo/synthetic cues only where faithfully representable;
- persisted boundary/marker replay.

Exit: transform golden tests and restart replays pass.

### Phase 3 — raw/restart fidelity

- persist/recover rich normalized message arcs needed by `ctx_expand`;
- implement branch/rewind reconciliation;
- remove FTS-text-only expansion fallback as the normal restart path.

Exit: tool-call expansion pre/post restart is equivalent.

### Phase 4 — historian completion/hardening

- ensure lease renewal for long historian calls;
- crash/stale-lease recovery;
- post-publish primer candidate and embedding hooks exactly upstream;
- full fallback-model semantics if current Hermes auxiliary route only uses primary.

Exit: async, emergency, timeout, crash, two-process tests pass.

### Phase 5 — Dreamer scheduler foundation

- expose upstream task configs, due planner, gate/backlog state, leases, retries,
  finish/failure transitions through Node runtime;
- pass a plugin-safe child launcher into the engine/coordinator;
- create generic Python task executor that launches/waits/cancels Hermes children
  while Node retains MC durable ownership;
- heartbeat/renew upstream lease while child runs.

Exit: synthetic due task runs exactly once across two processes.

### Phase 6 — Dreamer tasks

Implement and prove each task individually in canonical order/domain groups:

1. curate
2. maintain-docs
3. map-memories
4. verify
5. verify-broad
6. classify-memories
7. compress-cues
8. review-user-memories
9. retrospective
10. promote-primers
11. refresh-primers
12. evaluate-smart-notes

Do not mark Dreamer Full until all twelve task rows pass.

### Phase 7 — primers, embeddings, Git full E2E

- complete candidate/promotion/refresh pipeline;
- live embedding-enabled E2Es;
- generation change/rebuild;
- Git semantic search;
- provider failure/retry.

### Phase 8 — host surface hardening

Test:

- `hermes chat --cli -q`;
- interactive/session resume;
- gateway normal turn;
- gateway session hygiene;
- memory-provider-only startup;
- context engine + memory provider together;
- project switching;
- model switching;
- concurrent sessions;
- process restart;
- manual compress;
- MC compaction off.

### Phase 9 — documentation/release closure

- rewrite `docs/PARITY.md` from evidence;
- README config guidance: MC policy only in CortexKit shared/project config;
- remove pseudo-Dreamer/obsolete parser/dead compatibility code;
- full pytest;
- Ruff;
- Node syntax checks;
- package build;
- `git diff --check`;
- SQLite integrity check;
- final source review for duplicated policy;
- final standard CLI + gateway E2E;
- commit only when repo is clean and evidence supports every claimed row.

## 9. Acceptance-test matrix

Minimum required suites:

### Unit

- message conversion/stable IDs;
- config summary diagnostics only;
- host model routing/trust failures;
- engine host gate/emergency behavior;
- task executor cancellation/timeout;
- status/doctor formatting.

### Real upstream runtime integration

- shared config merge/security;
- compaction on/off;
- § tags/drop/cache scheduler;
- temporal/note/auto-search transforms;
- historian trigger/boundary/publish;
- lease exclusion;
- every `ctx_*` tool;
- Dreamer planner/state transitions;
- primer pipeline;
- Git/embedding maintenance.

### Cross-process

- historian duplicate exclusion;
- Dreamer lease domain exclusion;
- stale lease recovery;
- Git sweep cooldown/lease;
- shared DB visibility after process restart.

### Hermes host E2E

- standard CLI tool/search;
- async historian foreground non-blocking;
- child Dreamer using real `ctx_memory`;
- memory injection in a fresh process;
- post-restart `ctx_expand` with tools;
- gateway hygiene uses `MagicContextEngine`;
- compaction-off no mutation;
- project/model switch.

## 10. Completion checklist

The connector is **fully complete** only when all are true:

- [ ] no Magic Context policy config exists in Hermes-specific settings;
- [ ] no Python scheduler/threshold/drop/Dreamer policy duplicates upstream;
- [ ] `compaction.enabled=false` matches upstream knowledge-only semantics;
- [ ] complete request-transform ordering is implemented;
- [ ] `ctx_search`, `ctx_expand`, `ctx_reduce`, `ctx_note`, `ctx_memory` are full;
- [ ] historian is upstream-scheduled, async normally, sync only emergency/manual;
- [ ] all 12 Dreamer tasks use upstream scheduling/gates/leases/retries;
- [ ] primer pipeline is complete;
- [ ] embeddings and Git are produced and searched under shared config;
- [ ] branch/restart behavior preserves rich raw history;
- [ ] cross-process/cross-harness leases prevent duplicate work;
- [ ] version/symbol doctor detects incompatible upstream changes;
- [ ] CLI and gateway E2Es pass;
- [ ] full test/lint/build/diff/DB-integrity checks pass;
- [ ] `docs/PARITY.md` contains no unsupported Full claims;
- [ ] no Hermes source code was modified.
