# Magic Hermes parity map

This document compares magic-hermes with `@cortexkit/pi-magic-context` 0.38.x.
The connector's rule is simple: Magic Context owns context policy and durable
state; Hermes supplies lifecycle events, model execution, tool-capable children,
and request presentation through public integration rails.

“Full” means the Hermes path calls the same upstream implementation and preserves
the same durable outcome. “Hermes-shaped” means the same underlying behavior is
available at a different host lifecycle seam. Pi/OpenCode-only UI affordances are
marked N/A rather than reimplemented.

| Surface | Hermes status | Evidence / boundary |
|---|---|---|
| Shared config | Full | The upstream resolver owns user config at `~/.config/cortexkit/magic-context.jsonc` (or `$XDG_CONFIG_HOME/...`) plus project overrides at `<project>/.cortexkit/magic-context.jsonc`. Python does not parse or merge MC policy. |
| Shared SQLite/project identity | Full | Uses the upstream DB, project registration, and session/project identity functions with harness `hermes`. |
| Hermes host surfaces | Full | Current host E2Es pass normal `hermes chat --cli`, optimized `hermes -z`, gateway-style `AIAgent` construction, background historian execution, and real Dreamer child delegation. |
| Raw-message conversion + FTS | Full | Hermes messages are normalized to Pi entries, converted/indexed by upstream functions, and reconciled on live-tail rewrites. |
| Restart/resume identity | Full | A restored full Hermes transcript is recognized from persisted source IDs so post-compartment ordinals are not double-offset. |
| Branch/rewind reconciliation | Hermes-shaped | Live-tail rewrites truncate only stale tail index/tag state. A rewind into already-compacted history invalidates session-derived MC state and rebuilds from the authoritative Hermes branch while preserving project memory. |
| `ctx_search` | Full | Exact upstream schema/executor over the shared store, including memory/message/git/primer/note sources. |
| `ctx_expand` | Full | Exact upstream tool with `sessionManager.getBranch()` backed by the canonical Hermes transcript. Rich tool-call inputs/results survive sidecar restart when Hermes restores the session transcript. |
| `ctx_reduce` | Full | Upstream § tagging, pending-op, source-content and cache-safe drop lifecycle is active. Generic Hermes tool-result pruning remains disabled unless MC owns the reduction. |
| `ctx_note` | Full | Exact upstream ordinary-note and smart-note schema/executor. `surface_condition` compiles and evaluates through the upstream QuickJS sandbox and can surface a ready note. |
| `ctx_memory` | Full | Exact upstream write/get/list/update/merge/archive behavior and shared persistence. |
| m[0]/m[1] injection | Full | Upstream `injectM0M1Pi` owns docs, user profile, project memory, decayed compartments, cache materialization and boundary layout. The Hermes MemoryProvider does not duplicate injection. |
| Prompt guidance | Full | Uses upstream prompt-surface resolution and `buildMagicContextBlock`; no connector-owned MC policy prompt remains. |
| Temporal markers | Full | Uses upstream temporal-marker injection; Hermes timestamp normalization supports epoch seconds/milliseconds, numeric strings and ISO timestamps. Replay is idempotent. |
| Automatic search | Full | Uses upstream auto-search after prior history is indexed but before the live user message is indexed, preventing self-retrieval. |
| Historian scheduling | Full / Hermes-shaped wakeup | Upstream scheduler owns percentage/absolute thresholds, cache TTL, protected tail, commit-cluster triggers and emergency pressure. Hermes evaluates it on its lifecycle rails rather than reproducing the policy. |
| Background historian | Full | Normal turn completion schedules a background upstream historian pass. `compress()` is only the synchronous manual/emergency compatibility seam. |
| Historian transaction | Full | Upstream chunking, prompts, validation, repair, optional editor, publication, facts/events, queued drops, protected-tail floor, note trigger, primer candidates and embedding dispatch are used. |
| Historian leases | Full | Upstream compartment lease plus its 60-second renewal cadence protect long Hermes LLM passes; abort/failure releases state safely. |
| User-observation candidates | Full | Historian stores them only when the upstream review-user-memories configuration enables collection. |
| Dreamer scheduler/task state | Full / Hermes-shaped wakeup | Upstream `buildDreamTaskRuntimeConfigs`, `runDueTasksForProject`, `runManualDream`, gates, backlogs, leases, retries and schedule patches are authoritative. Due work is evaluated on active Hermes lifecycle turns; Hermes does not invent a second cron state machine. |
| Dreamer child execution | Hermes-shaped | Upstream Dreamer creates its virtual task sessions; a duplex host callback launches real Hermes public subagents. Tool-using children reach exact upstream `ctx_*` handlers through Hermes's scoped progressive-disclosure bridge. |
| `curate` | Full | Real Hermes-child E2E performs `tool_search` → `tool_call` → upstream `ctx_memory(merge/get)` and mutates the shared pool. |
| `map-memories` | Full | Upstream parser/apply path writes file mappings. Hermes child capability is read-only project inspection. |
| `verify` / `verify-broad` | Full | Upstream verification parsers and timestamps are used; Hermes child capability is read-only project inspection. |
| `classify-memories` | Full | Upstream minimum-pool gate and classification metadata are preserved. |
| `compress-cues` / mural | Full | Upstream cue parser/storage, deterministic mural renderer, vision-model gate and m[0] image injection are used. |
| `review-user-memories` | Full | Recurring historian observations are reviewed/promoted/consumed by the upstream task. |
| `retrospective` | Full | Durable Hermes user-message history supplies the upstream friction gate/deepening flow; accepted learnings and the retrospective watermark persist and reruns are idempotent. |
| `promote-primers` | Full | Upstream recurrence count and seven-day span gates are preserved. |
| `refresh-primers` | Full | Durable Hermes raw providers supply upstream primer orientation; refreshed answers require recorded investigation tool use before commit. |
| `evaluate-smart-notes` | Full | Upstream compiler, manifest validation, QuickJS sandbox and ready-state transition are used. Compiled sweeps also run in maintenance. |
| Dreamer cancellation | Full | Runtime host callbacks are concurrent, so an upstream abort callback can cancel an in-flight Hermes child before the original prompt callback returns. |
| Memory embeddings | Full | Upstream embedding providers/backfill write real vectors. Model changes switch active identities; upstream retains prior generations for its 14-day GC grace. |
| Compartment embeddings | Full | Historian publication and embedding drain use upstream chunk embedding/storage. |
| Git commit indexing | Full | Upstream Git sweep owns commit/FTS indexing; semantic commit vectors follow the configured embedding provider. |
| Stale embedding GC | Full | Upstream identity-generation sweep runs during maintenance; its grace-period semantics are preserved. |
| `compaction.enabled=false` | Full | Upstream knowledge-only behavior: no MC context-window mutation/historian/reduction/temporal/auto-search work, while docs/profile/project-memory injection and searchable durable knowledge remain available. |
| Pi synthetic `todowrite` | N/A | Upstream 0.38 documents this as Pi-only and says to disable it when the host has its own todo extension. Hermes uses its native todo rail; magic-hermes does not create a competing todo state machine. |
| Pi/OpenCode status line / command UI | N/A | Presentation-only host UI. Functional state is available through normal Hermes tooling/logging and the runtime doctor. |

## Lifecycle and scheduling differences

Hermes has one selected ContextEngine. With `context.engine: magic-context`, the
built-in `ContextCompressor` is replaced; `compression.enabled` therefore remains
the host permission gate and must not be turned off as an OpenCode-style way of
disabling competing compaction. OpenCode disables its separate built-in compactor;
Hermes does not run a second context engine alongside Magic Context.

`on_turn_complete()` is the normal observation/scheduling rail. The adapter indexes
the canonical transcript, asks upstream MC whether historian work is due, and
schedules historian/Dreamer/maintenance work. Request rendering is owned by
`select_context()` through upstream m[0]/m[1]. `/compress` or an emergency
preflight uses the same upstream historian and renderer synchronously.

Hermes does not expose an always-running plugin cron daemon with an active parent
agent context. MC due times, retries, backlogs and leases remain durable in its
SQLite state; agentic Dreamer work that becomes due while the host is completely
idle is picked up on the next active Hermes lifecycle turn rather than having its
schedule state advanced prematurely.

## Failure, concurrency and recovery

- Runtime requests are local stdio calls. Mutating calls are never replayed after
  a timeout or transport failure.
- Historian failures fail open to the current transcript and clear/release the
  upstream transaction state.
- Long historian passes renew the upstream compartment lease every 60 seconds.
- Dreamer uses upstream domain leases/backlog state; Hermes child cancellation is
  addressable by the upstream virtual-session ID even while the launch callback is
  still running.
- Host callbacks are concurrent while top-level sidecar requests remain serialized.
- A live-tail rewrite removes stale FTS/source/tag/pending state. A rewind through
  compacted history invalidates session-derived compartments/facts/cues/index state
  and rebuilds from Hermes's authoritative branch instead of keeping stale summaries.

## Configuration boundary

There is one Magic Context policy configuration system:

1. `~/.config/cortexkit/magic-context.jsonc` (or
   `$XDG_CONFIG_HOME/cortexkit/magic-context.jsonc`) supplies user defaults.
2. `<project>/.cortexkit/magic-context.jsonc` supplies project overrides where the
   upstream security policy permits them.
3. The official upstream resolver performs merge, validation, defaults, migrations
   and security filtering.

Hermes YAML contains only host integration and provider/credential concerns. A
minimal integration is:

```yaml
context:
  engine: magic-context
memory:
  provider: magic_context
platform_toolsets:
  cli:
    - context_engine
```

The existing platform toolsets should be preserved; `context_engine` is added to
that list rather than replacing it.

## Compatibility boundary

magic-hermes intentionally accepts only the reviewed 0.38.x upstream series. The
published Magic Context package does not expose a stable connector API, so the
Node loader exposes the minimum reviewed private symbols required by the adapter.
Python preflight and the runtime doctor reject missing or unreviewed surfaces before
normal use.
