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
- Each Python component owns a private, lazy Node adapter process. Calls use
  newline-delimited JSON over local stdio and are serialized; mutation calls are
  never replayed after a transport failure.
- The Node adapter loads the installed official Pi package, changes only the
  harness identity to `hermes`, and delegates behavior to upstream functions.
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
runtime. The unsupported smart-note condition field is removed from `ctx_note`
because Hermes does not run the upstream condition evaluator:

- `ctx_search`
- `ctx_expand`
- `ctx_reduce`
- `ctx_note`
- `ctx_memory`

The memory provider deliberately registers no duplicate tools. It supplies
Hermes' standard budgeted `<project-memory>` injection while the context engine
owns all `ctx_*` dispatch.

## Compaction and auxiliary LLMs

At the configured upstream threshold, Hermes invokes the Magic Context historian
through the `mc_historian` auxiliary route. The connector uses the official
historian prompt, parser, validator, repair prompt, optional two-pass editor,
compartment decay, fact promotion, event storage, and user-observation candidate
storage. Invalid output fails open to the unchanged transcript.

When the upstream `curate` task has a non-empty schedule, `mc_dreamer`
provides a conservative Hermes-hosted curate pass at a real session boundary.
The complete
Pi/OpenCode scheduled dream-task suite is not reproduced; see
[docs/PARITY.md](docs/PARITY.md).

## Verification

With the official 0.38.x package installed:

```bash
python -m pytest -q
ruff check src tests
node --check src/magic_hermes/bridge/loader.mjs
node --check src/magic_hermes/bridge/runtime.mjs
python -m build
```

The integration test uses a temporary real Magic Context database and proves
indexing, all five tool schemas, memory and note persistence, historian
validation, two-pass fallback, compartment/event publication, decayed rendering,
search, and expansion after a runtime restart.

## Compatibility and failure behavior

The adapter intentionally pins the reviewed 0.38.x series because it uses private
symbols from the bundled Pi module. A missing dependency or unreviewed version is
reported before the adapter starts. Runtime and host-LLM failures during
compaction fail open: the current Hermes transcript is returned unchanged.

Detailed supported behavior and deliberate host-shaped differences are recorded
in [docs/PARITY.md](docs/PARITY.md).

## License

MIT.
