# magic-hermes

A thin Hermes connector for the official
[Magic Context](https://github.com/cortexkit/magic-context) Pi runtime.

magic-hermes maps Magic Context onto Hermes' native context-engine, memory-provider,
tool, lifecycle, and auxiliary-LLM surfaces. It reuses the installed
`@cortexkit/pi-magic-context` package for tool schemas, prompts, validation,
decay, indexing, and SQLite storage; this repository does not reimplement those
algorithms.

## Status

Working development preview. The exact upstream release exercised by this repo and
the accepted major/minor series are recorded in
`src/magic_hermes/magic_context_compat.json`. New Magic Context core releases are
synchronized automatically only after the full Magic-Hermes validation gate passes.

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
- `@cortexkit/pi-magic-context` from the supported series declared in
  `src/magic_hermes/magic_context_compat.json`, installed in a standard Pi/OpenCode
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

### 1. Install Magic Context

Magic-Hermes delegates its context-management implementation to the official Magic
Context package, so install Magic Context first. If you already use Pi, the upstream
setup wizard is the simplest path and places the package where Magic-Hermes can
discover it:

```bash
npx @cortexkit/magic-context@latest setup --harness pi
```

If Magic Context is already installed for Pi or OpenCode, you can reuse that same
installation and database. Magic-Hermes automatically searches the normal Pi and
OpenCode package locations. For a custom installation, point directly at the package:

```bash
export MAGIC_CONTEXT_PACKAGE_ROOT=/path/to/node_modules/@cortexkit/pi-magic-context
```

### 2. Install Magic-Hermes into Hermes

Install the latest published wheel into the Python environment used by Hermes. This
repository is currently private, so authenticate GitHub CLI once with `gh auth login`,
then run:

```bash
rm -rf /tmp/magic-hermes-install
mkdir -p /tmp/magic-hermes-install

gh release download --repo codeo1io/magic-hermes \
  --pattern 'magic_hermes-*-py3-none-any.whl' \
  --dir /tmp/magic-hermes-install

uv pip install --python /path/to/hermes/venv/bin/python --no-deps \
  /tmp/magic-hermes-install/magic_hermes-*-py3-none-any.whl
```

If `uv` is not installed, use the Hermes environment's `pip` instead:

```bash
/path/to/hermes/venv/bin/python -m pip install --no-deps \
  /tmp/magic-hermes-install/magic_hermes-*-py3-none-any.whl
```

When this repository becomes public, the release wheel can also be installed directly
from its GitHub Release URL without the authenticated download step.

For development from a local checkout, install the repository directly instead:

```bash
uv pip install --python /path/to/hermes/venv/bin/python --no-deps -e .
```

### 3. Enable Magic-Hermes

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
JSONC file. Magic-Hermes does not introduce a second configuration source.

### 4. Verify the installation

Confirm the package is installed in the Hermes environment and that Magic-Hermes can
find a supported Magic Context runtime:

```bash
/path/to/hermes/venv/bin/python -c \
  'import magic_hermes; print("magic-hermes", magic_hermes.__version__)'

/path/to/hermes/venv/bin/python -c \
  'from magic_hermes.runtime import runtime_available, runtime_unavailable_reason; print("Magic Context runtime: OK" if runtime_available() else runtime_unavailable_reason())'
```

Restart Hermes after changing its plugin configuration. Once loaded, the `ctx_search`,
`ctx_expand`, `ctx_reduce`, `ctx_note`, and `ctx_memory` tools should be available.

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
retries, and the task implementations from the validated upstream release. When a task needs an agent,
the adapter supplies a real Hermes public subagent as the host execution primitive;
Magic Context remains authoritative for task policy and durable state. Agentic work
that becomes due while Hermes is completely idle is picked up on the next active
Hermes lifecycle turn without advancing the upstream due state prematurely. See
[docs/PARITY.md](docs/PARITY.md) for the task-by-task evidence and host-shaped
boundaries.

## Verification

With the repo-pinned official Magic Context package installed:

```bash
python -m pytest -q
ruff check src tests
node --check src/magic_hermes/bridge/loader.mjs
node --check src/magic_hermes/bridge/runtime.mjs
python -m build
```

The suite uses temporary real Magic Context databases and the repo-pinned upstream
runtime. It covers all five tool contracts, upstream m[0]/m[1] rendering,
cache-safe reductions, temporal/auto-search behavior, historian scheduling and
publication, lease renewal, rich `ctx_expand` after restart, branch/rewind
reconciliation, all twelve Dreamer task state machines, smart-note sandboxing,
mural generation, real OpenAI-compatible embedding production/model rotation,
and upstream Git indexing. Separate host E2Es exercise normal `hermes chat`,
optimized `hermes -z`, gateway-style agent construction, background historian
execution, and real Hermes Dreamer child delegation.

## Compatibility and failure behavior

The adapter accepts only the major/minor series recorded in
`src/magic_hermes/magic_context_compat.json` because it uses private symbols from
the official Pi module. The repo-level `package.json`/`package-lock.json` pin the
exact upstream release used for validation. `.github/workflows/sync-magic-context.yml`
checks for new core `vX.Y.Z` releases every 15 minutes (and also supports immediate
`repository_dispatch`), waits for the matching npm publication, and processes the
oldest unseen core release first so intermediate releases are never skipped. Each
validated upstream release updates the dependency pin and compatibility manifest,
increments the Magic-Hermes patch version (for example `0.2.0` to `0.2.1`), runs the
full Python/Node/build gate, commits and tags the release, and publishes the wheel,
sdist, and checksums as a GitHub release. A missing dependency or unvalidated series
is reported before the adapter starts. Runtime and host-LLM failures during
compaction fail open: the current Hermes transcript is returned unchanged.

Detailed supported behavior and deliberate host-shaped differences are recorded
in [docs/PARITY.md](docs/PARITY.md).

## Publishing a release

Maintainers can publish a complete GitHub release with an explicit version or ask
for the next patch version:

```bash
.venv/bin/python scripts/release.py X.Y.Z
.venv/bin/python scripts/release.py --next-patch
```

The release script requires a synchronized default branch and authenticated `gh`.
It accepts only release metadata and Magic Context synchronization changes in the
release transaction, synchronizes the Python package/plugin versions, installs the
repo-pinned Magic Context npm dependency, runs the complete test/lint/Node/build
gate, produces wheel and sdist artifacts plus `SHA256SUMS`, commits `release: vX.Y.Z`,
creates and pushes an annotated tag, and creates the GitHub release with the
artifacts attached. It refuses to publish when validation fails or unrelated working
tree changes are present.

## License

MIT.
