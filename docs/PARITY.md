# Magic Hermes parity map

This document compares magic-hermes with
`@cortexkit/pi-magic-context` 0.38.x. “Full” means the Hermes path delegates to
the same upstream implementation for that behavior. “Hermes-shaped” means the
same product outcome is provided at a different host lifecycle seam. “Partial”
is an explicit boundary, not a claim of full Pi/OpenCode parity.

| Surface | Hermes status | Evidence and boundary |
|---|---|---|
| Shared SQLite store and project identity | Full | Uses upstream database, registration, and session/project identity functions with harness `hermes`. |
| Raw-message conversion and FTS indexing | Full | Uses upstream Pi conversion and indexing functions after each normal completed Hermes turn and before render/tool/compaction work. |
| `ctx_search` | Full | Exact upstream schema and executor; searches the shared store. |
| `ctx_expand` | Hermes-shaped | Exact upstream tool. After a process restart, canonical FTS text is used when the original in-memory raw transcript is unavailable; rich tool-call reconstruction can therefore be less detailed. |
| `ctx_reduce` | Partial | Exact upstream schema/executor is present, but Hermes does not create Pi's § tag/drop lifecycle, so a normal Hermes transcript usually has no reducible tagged payloads. |
| `ctx_note` | Full for ordinary notes | Upstream write/read/update/dismiss behavior and shared persistence. The connector suppresses the unsupported `surface_condition` field because scheduled smart-note evaluation is not run. |
| `ctx_memory` | Full | Exact upstream schema/executor for intentional writes, reads, updates, merges, and archival. |
| `<project-memory>` injection | Hermes-shaped | The exclusive Hermes memory provider injects upstream-rendered active/permanent project memory trimmed to the upstream token budget. It does not semantic-query a subset for each turn. |
| Historian compaction | Hermes-shaped | Hermes invokes `mc_historian` synchronously at its context-engine compaction seam. Prompts, chunking, parser, validation, one repair pass, optional editor pass, storage, and decay are upstream. |
| Historian facts and events | Full on published compartments | Optional durable fact promotion and compartment-event storage use upstream functions. User-observation candidates are stored when the upstream review-user-memories schedule enables collection. |
| Historian primer candidates and compartment embeddings | Partial | Primer promotion, embedding generation, and the Pi historian-run telemetry pipeline are not scheduled by magic-hermes. Existing shared-store data remains searchable. |
| Two-pass historian | Full | The official editor prompt is used. A valid editor replaces the draft; invalid editor output falls back to the already validated draft. |
| Dreamer | Partial | A bounded `mc_dreamer` curate pass can run at a real Hermes session boundary only when the upstream `curate` task has a non-empty schedule. The connector uses the session boundary as its trigger, not the configured cron cadence; the full task set, leases, backlogs, and per-task promotion rules remain Pi/OpenCode-owned. |
| Auxiliary model routing | Hermes-shaped | Hermes owns credentials and the `mc_historian`/`mc_dreamer` routes. A `zai/` prefix is normalized for Hermes; other model references are left intact. |
| Semantic embedding production | Partial | The upstream search tool can consume embeddings already present in the shared store. Hermes does not reproduce every Pi/OpenCode background embedding and git-indexing scheduler. |
| Status UI and commands | Not available | Hermes has no Pi status-line or command-palette contract for Magic Context. Tool responses and standard engine status remain available. |
| Temporal markers, mural, todos, and automatic search | Not available | These depend on Pi/OpenCode prompt and UI lifecycle surfaces and are not injected into Hermes. |

## Lifecycle differences

Hermes' `on_turn_complete` hook is best-effort by host contract. Normal completed
turns are indexed; certain abnormal early exits and terminal provider failures may
not emit the hook. Later render, tool, compaction, and session-end paths ingest the
transcript again, which recovers most missed observations without replaying
transport mutations.

The adapter preserves an append-oriented session view. Hermes currently has no
context-engine rewind callback carrying the exact branch boundary, so rewinding a
session under the same session ID can leave already indexed pre-rewind messages in
the shared search store. A new session ID has clean isolation.

Historian work is synchronous because Hermes' context-engine API expects
`compress()` to return the replacement message list. Unlike Pi's background
historian, this can pause the triggering turn for the configured auxiliary-LLM
duration. Provider failures, invalid output after repair, adapter errors, and
database contention fail open to the original transcript.

Pi's provisional-last-compartment boundary healing, emergency recovery state,
leases, queued native-message drops, compaction markers, and detailed historian
telemetry are not recreated. Hermes protects its live tail and publishes only
upstream-validated compartments in one local SQLite transaction.

## Compatibility boundary

magic-hermes reviews and accepts only the 0.38.x upstream series. The adapter
loads private bundled symbols because the published package does not expose a
stable connector API. Both Python preflight and the Node adapter reject other or
malformed versions.

The adapter is a local stdio child process, not a service. It has no listening
port, authentication protocol, daemon discovery, or separate storage. Mutation
requests are serialized and never automatically replayed after timeout or
transport failure.

## Configuration boundary

The official runtime merges shared and project-local Magic Context configuration.
Hermes auxiliary routes are registered at process/plugin load, so host-level model
routing defaults come from the shared configuration available at that time. The
runtime still uses the merged per-project configuration for thresholds, budgets,
two-pass behavior, memory policy, language, and dreamer readiness.
