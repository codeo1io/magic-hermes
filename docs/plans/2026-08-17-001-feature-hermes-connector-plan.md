# Hermes Magic Context Connector - Plan

- artifact_contract: ce-unified-plan/v1
- artifact_readiness: requirements-only
- product_contract_source: ce-brainstorm
- date: 2026-08-17

## Goal Capsule

**Objective:** Give hermes-agent feature parity with the Magic Context
pi/opencode plugins via a connector plugin (`magic-hermes`) that reuses the
upstream magic-context monorepo's logic and daemon, adding only a Python
client + hermes adapter.

**Product authority:** User directive (2026-08-17 session): "feature parity…
use existing hermes integration surfaces", "create a new repo… call it
magic-hermes", "re-use as much as possible from upstream magic-context github
repo".

**Open blockers:** None blocking requirements. Protocol reference for the
Python subc client must be sourced from upstream `@cortexkit/subc-client` /
`subc-transport` before implementation (planning input, not a product
decision).

## Product Contract

### Actors

- **Primary:** hermes-agent sessions (CLI, gateway/api_server, cron) on a
  machine where the subc daemon and Magic Context are already installed.
- **Secondary:** the developer maintaining magic-hermes alongside the
  upstream monorepo plugins.

### Problem

Magic Context exists only as TypeScript plugins for pi and OpenCode.
Hermes-agent (Python) sessions get none of its benefits: no compartmented
history, no background compaction, no cross-session recall, no persistent
project memory.

### Outcome

A hermes plugin that delivers the same effective behavior as the pi/opencode
plugins: compartmented `<session-history>`, historian-driven compaction,
cross-session search/expand/reduce tools, persistent memories, and notes —
all backed by the same daemon, store, and config the other harnesses share
on the machine.

### Scope — in

- Python subc client (loopback TCP, HMAC-SHA256, byte-compatible wire with
  TS/Rust consumers).
- Hermes adapter over native surfaces: `register_context_engine`,
  `register_tool`, hermes memory config surface, `register_hook` /
  `register_middleware`.
- Reading the shared `~/.config/cortexkit/magic-context.jsonc` for engine
  settings (historian model, embeddings); hermes-native config only for
  hermes-facing toggles.
- PARITY.md documenting deliberate hermes↔pi↔opencode divergences.
- Plugin installable into `~/.hermes/plugins/` (or hermes plugin install
  path) from this repo.

### Scope — out

- Any reimplementation of Magic Context logic in Python (transform, decay,
  store, tokenizer, dreamer/historian prompts stay upstream).
- Changes to the upstream monorepo beyond what a later upstreaming PR would
  carry (e.g., a `packages/cli` hermes adapter is future work, optional).
- Dashboard, CLI wizard, e2e harness integrations (follow only if parity
  requires them).

### Success criteria

1. A hermes session with the plugin active shows compartmented history and
   survives long conversations without context-wall failures, matching the
   pi plugin's effective behavior.
2. `ctx_*`-equivalent tools registered in hermes resolve against the same
   store a pi/opencode session on the machine reads — cross-harness recall.
3. Compaction runs through hermes' context-engine surface, not a parallel
   pipeline; hermes' built-in compressor is the only thing replaced.
4. Config is single-source: changing the historian model in the shared
   cortexkit config changes hermes behavior without editing hermes config.
5. PARITY.md explains every observable divergence from pi/opencode.

### Assumptions

- The subc daemon and its HMAC handshake are stable enough to implement
  against byte-for-byte; wire docs exist in the upstream subc client README
  (verified 2026-08-17) — full protocol details to be confirmed at planning.
- hermes `ContextEngine` contract (`agent.context_engine.ContextEngine`,
  single registered engine) is the correct sole compaction surface.
- hermes plugin API (`hermes_cli/plugins.py`) is stable for the surfaces
  listed; no hermes-agent fork required.

### Outstanding questions

1. Distribution: local repo only for now — does it later publish (e.g., as a
   hermes plugin registry entry or an upstream monorepo sibling package)?
2. Historian/dreamer subagents: pi spawns them as `pi --print` processes; in
   hermes, run them via hermes' own agent runtime (`register_auxiliary_task`)
   or as lean subprocesses? Deferred to planning.
3. Do hermes gateway (api_server) sessions route through the same
   context-engine path as CLI sessions? Verify at planning.
