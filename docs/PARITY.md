# Magic Hermes — Parity Map

Hermes ↔ pi (`@cortexkit/pi-magic-context`) ↔ opencode (`@cortexkit/opencode-magic-context`).

Legend: ✅ supported · 🟡 partial / hermes-shaped · ❌ not available on this harness · ➖ n/a

| Magic Context feature | pi | opencode | hermes (magic-hermes) | Notes |
|---|---|---|---|---|
| Compartmented history / `<session-history>` | ✅ | ✅ | ✅ U3 engine | `register_context_engine` replaces hermes `ContextCompressor` |
| Background compaction (no pauses) | ✅ | ✅ | ✅ | engine delegate `context.compact` on daemon |
| `<project-memory>` injection | ✅ | ✅ | ✅ | injected at session start via engine metadata |
| ctx_search / ctx_expand / ctx_reduce tools | ✅ | ✅ | ✅ U4 | `register_tool` |
| Persistent memories (write/update/merge/archive) | ✅ | ✅ | ✅ U5 | hermes standard memory config surface backed by mc memory ops |
| Session notes | ✅ | ✅ | ✅ U5 | |
| Historian | ✅ subagent (`pi --print`) | ✅ | 🟡 U6 auxiliary task (`mc_historian`) | hermes-native scheduling; same shared model config |
| Dreamer | ✅ subagent | ✅ | 🟡 U6 auxiliary task (`mc_dreamer`) | cadence-gated signal queue |
| Tokenizer | ✅ in-process (mc-tokenizer) | ✅ | ✅ daemon-side | client requests token counts via session ops |
| Embeddings | ✅ | ✅ | ✅ daemon-side | config in `~/.config/cortexkit/magic-context.jsonc` |
| Shared DB / cross-harness continuity | ✅ | ✅ | ✅ | same `~/.local/share/cortexkit/magic-context` store |
| Status line / UI affordances | ✅ | ✅ | ➖ | hermes has no status-line surface; parity via tool outputs |
| Command palette commands | ✅ | ✅ | 🟡 | `/magic-*` handled as hermes skills/commands where exposed |

## Known divergences

1. **Auxiliary execution model.** pi/opencode spawn subprocess subagents; hermes uses its
   auxiliary-task API (`mc_historian`, `mc_dreamer`). Same models (shared config), different
   scheduling host.
2. **In-process core unavailable.** Hermes is Python; the TS core cannot load in-process, so
   magic-hermes always uses the subc daemon ("rust mode" in the pi plugin's terms). A running
   daemon is a hard requirement — the engine fails closed (falls back to hermes' native
   compressor) when the daemon is absent.
3. **UI surfaces.** No status line; context telemetry surfaces through tool outputs and engine
   metadata only.
