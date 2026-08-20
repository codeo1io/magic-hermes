# magic-hermes

A thin Hermes connector for the official
[Magic Context](https://github.com/cortexkit/magic-context) Pi runtime.

magic-hermes maps Magic Context onto Hermes' native context-engine, memory-provider,
tool, lifecycle, and auxiliary-LLM surfaces. It reuses the installed
`@cortexkit/pi-magic-context` package for tool schemas, prompts, validation,
decay, indexing, and SQLite storage; this repository does not reimplement those
algorithms.

## Status

Working development preview, reviewed against
`@cortexkit/pi-magic-context` 0.38.x. Other upstream release series are rejected
until their private adapter surface has been reviewed.

## Architecture

- Hermes loads `MagicContextEngine` as the selected context engine.
- Hermes loads `MagicContextMemoryProvider` as the exclusive external memory
  provider.
- Each Python component owns a private, lazy Node adapter process. Top-level
  requests use newline-delimited JSON over local stdio and remain serialized;
  host callbacks may run concurrently so an upstream Dreamer timeout can cancel
  an in-flight Hermes child. Mutation calls are never replayed after a transport
  failure.
- The Node adapter loads the installed official Pi package, changes only the
  harness identity to `hermes`, and delegates context policy, scheduling,
  rendering, tools, Dreamer state, validation, embeddings, and SQLite behavior
  to upstream functions.
- The ContextEngine is the sole request-render owner. The Hermes MemoryProvider
  participates in lifecycle/status integration but does not inject a duplicate
  project-memory block.
- Pi, OpenCode, and Hermes use the same Magic Context SQLite store and JSONC
  configuration.

There is no magic-hermes daemon, socket protocol, alternate database, or copied
Magic Context core.

## Requirements

- Python 3.10 or newer
- Node.js on `PATH`
- `@cortexkit/pi-magic-context` 0.38.x installed in a standard Pi/OpenCode
  location, or `MAGIC_CONTEXT_PACKAGE_ROOT` set to its package directory
- A Hermes build with plugin context-engine, exclusive memory-provider,
  per-turn observation, and auxiliary-task support

The normal shared locations are:

- Config: `~/.config/cortexkit/magic-context.jsonc` (or
  `$XDG_CONFIG_HOME/cortexkit/magic-context.jsonc`)
- Store: `~/.local/share/cortexkit/magic-context/context.db`

Project-local `.cortexkit/magic-context.jsonc` files are merged by the official
runtime.

## Install

Install the repository into the Python environment used by Hermes. For example:

```bash
uv pip install --python /path/to/hermes/venv/bin/python --no-deps -e .
```

Enable the plugin, context engine, and exclusive memory provider in Hermes:

```yaml
plugins:
  enabled:
    - magic-hermes

context:
  engine: magic-context

memory:
  provider: magic_context
```

If `platform_toolsets` is explicitly configured, the active platform must also
include Hermes' `context_engine` toolset or the `ctx_*` tools will be hidden even
though the plugin and memory provider are active. For example, add it to the
existing CLI list rather than replacing your other toolsets:

```yaml
platform_toolsets:
  cli:
    - ...
    - context_engine
```

Keep historian, memory, embedding, and dreamer policy in the shared Magic Context
JSONC file. magic-hermes does not introduce a second configuration source.

## Exposed tools

The context engine exposes the five tools registered by the installed upstream
runtime, including the complete smart-note `surface_condition` contract:

- `ctx_search`
- `ctx_expand`
- `ctx_reduce`
- `ctx_note`
- `ctx_memory`

The MemoryProvider deliberately registers no duplicate tools or policy prompt.
The ContextEngine owns `ctx_*` dispatch and upstream m[0]/m[1] rendering.

## Compaction, historian, and Dreamer

Magic Context—not a second Hermes compressor—owns context policy. Its upstream
scheduler evaluates percentage/absolute pressure, cache TTL, protected tail, and
other supported triggers. Normal completed turns schedule historian work in the
background through the `mc_historian` auxiliary route; manual `/compress` and
emergency preflight use the same upstream historian synchronously. The official
chunking, prompts, parser, validator, repair/editor passes, compartment storage,
queued reductions, facts/events, note triggers, primer candidates, and embedding
side effects are preserved. Invalid output fails open to the current transcript.

Do **not** set Hermes `compression.enabled: false` to imitate OpenCode's setup.
OpenCode disables a separate built-in compactor; Hermes selects exactly one
ContextEngine, so its compression setting remains the host permission gate for the
selected Magic Context engine.

Dreamer uses the upstream task planner, gates, schedules, backlogs, leases,
retries, and all twelve 0.38.x task implementations. When a task needs an agent,
the adapter supplies a real Hermes public subagent as the host execution primitive;
Magic Context remains authoritative for task policy and durable state. Agentic work
that becomes due while Hermes is completely idle is picked up on the next active
Hermes lifecycle turn without advancing the upstream due state prematurely. See
[docs/PARITY.md](docs/PARITY.md) for the task-by-task evidence and host-shaped
boundaries.

## Verification

With the official 0.38.x package installed:

```bash
python -m pytest -q
ruff check src tests
node --check src/magic_hermes/bridge/loader.mjs
node --check src/magic_hermes/bridge/runtime.mjs
python -m build
```

The suite uses temporary real Magic Context databases and the installed 0.38.x
runtime. It covers all five tool contracts, upstream m[0]/m[1] rendering,
cache-safe reductions, temporal/auto-search behavior, historian scheduling and
publication, lease renewal, rich `ctx_expand` after restart, branch/rewind
reconciliation, all twelve Dreamer task state machines, smart-note sandboxing,
mural generation, real OpenAI-compatible embedding production/model rotation,
and upstream Git indexing. Separate host E2Es exercise normal `hermes chat`,
optimized `hermes -z`, gateway-style agent construction, background historian
execution, and real Hermes Dreamer child delegation.

## Compatibility and failure behavior

The adapter intentionally pins the reviewed 0.38.x series because it uses private
symbols from the bundled Pi module. A missing dependency or unreviewed version is
reported before the adapter starts. Runtime and host-LLM failures during
compaction fail open: the current Hermes transcript is returned unchanged.

Detailed supported behavior and deliberate host-shaped differences are recorded
in [docs/PARITY.md](docs/PARITY.md).

## License

MIT.
