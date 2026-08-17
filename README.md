# magic-hermes

Magic Context connector for [hermes-agent](https://github.com/) — a Python
plugin that gives hermes **feature parity** with the
[Magic Context](https://github.com/cortexkit/magic-context) plugins for Pi
(`@cortexkit/pi-magic-context`) and OpenCode (`@cortexkit/opencode-magic-context`).

## What this is — and is not

magic-hermes is a **connector, not a reimplementation**. All Magic Context
logic lives in the upstream monorepo:

- Transform / classifier / decay: `crates/mc-core`
- Durable cache-state store: `crates/mc-store` (shared SQLite DB)
- Token estimation: `crates/mc-tokenizer`
- Daemon protocol handler: `crates/mc-module` (CK-in/CK-out over the
  **subc** daemon, loopback TCP + HMAC-SHA256)

This repo adds only:

1. A **Python subc client** — wire-compatible with `@cortexkit/subc-client`
   and the Rust `subc-probe` consumers (loopback TCP, HMAC-SHA256 framing).
2. A **hermes adapter** mapping Magic Context features onto hermes' native
   integration surfaces.

## Hermes integration surfaces

| Magic Context feature | Hermes surface |
|---|---|
| Compaction / `<session-history>` / historian | `register_context_engine` (replaces built-in `ContextCompressor`) |
| `ctx_search` / `ctx_expand` / `ctx_reduce` / `ctx_note` | `register_tool` |
| Persistent memories / `<project-memory>` | hermes standard memory config surface + hooks |
| Session lifecycle / reduction triggers | `register_hook` / `register_middleware` |

## Shared substrate

- One SQLite store across harnesses: `~/.local/share/cortexkit/magic-context/`
- One config: `~/.config/cortexkit/magic-context.jsonc`
  (historian model, embeddings backend)
- Same daemon for pi / opencode / hermes sessions on a machine.

## Parity

Deliberate divergences from the pi/opencode plugins are recorded in
[docs/PARITY.md](docs/PARITY.md) — "same effective behavior, different
mechanism where the host runtimes differ", per upstream's rule.

## Status

Requirements captured in
[docs/plans/2026-08-17-001-feature-hermes-connector-plan.md](docs/plans/2026-08-17-001-feature-hermes-connector-plan.md).
Implementation not yet started.

## License

MIT (matching upstream).
