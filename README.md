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

### 1. One-command install (recommended)

```bash
/path/to/hermes/venv/bin/magic-hermes install
```

The installer auto-detects any existing Magic Context installation, installs or
updates `@cortexkit/pi-magic-context` as needed, wires the Hermes config, and
verifies the runtime end to end:

- If Magic Context is already installed anywhere Magic-Hermes looks (Pi home,
  OpenCode config, or the Hermes-managed root), it is reused when its version
  matches the one this build was validated against. Foreign homes (Pi's,
  OpenCode's) are never modified.
- If the discovered copy is older than the validated version, the validated
  version is installed into the Hermes-owned root
  (`~/.local/share/magic-hermes`) — which takes precedence in runtime
  discovery — leaving other tools' installations untouched.
- If a newer Magic Context exists than this build validates (or npm `latest`
  is newer), the installer says so and points at the release pipeline; the
  schema fence follows the newest copy, so an upgrade needs a matching
  magic-hermes release.
- `~/.hermes/config.yaml` gains `context.engine: magic-context`,
  `memory.provider: magic_context`, and `magic-hermes` in `plugins.enabled`,
  with a timestamped backup and comments preserved (requires `ruamel.yaml` or
  PyYAML in the magic-hermes environment).
- The Node sidecar is then booted and must open the shared DB before install
  reports success.

`--version X.Y.Z` targets a specific upstream version, `--dry-run` shows the
plan, `--package-root PATH` binds an arbitrary copy, and `--skip-config`
leaves Hermes config untouched.

### 1b. Check installation health

```bash
/path/to/hermes/venv/bin/magic-hermes doctor
```

Modeled after the upstream `@cortexkit/magic-context doctor`: a sequence of
PASS/WARN/FAIL/INFO checks (Node, magic-hermes distribution, upstream package
discovery + version pin, Hermes config wiring, shared DB presence and schema
lane, live sidecar handshake + DB quick_check + core-symbol check) with a
summary line and non-zero exit on FAIL. `--json` emits machine-readable
output.

### 1b-2. Shared store migration toolkit (`magic-hermes db`)

Upstream bumps the schema fence on series releases, and the shared store
migrates lazily on the next harness boot — which upstream's
migration-on-open guard refuses while any live Pi/OMP/OpenCode harness
still runs the old plugin build. `magic-hermes db` turns the resulting
recovery (lsof archaeology, holder/build attribution, drain-and-retry)
into one command:

```
/path/to/hermes/venv/bin/magic-hermes db status     # fence gap + live holders
/path/to/hermes/venv/bin/magic-hermes db blockers   # holders with builds (--json)
/path/to/hermes/venv/bin/magic-hermes db migrate    # sanctioned openDatabase path
```

`db migrate` drives the same fence + holder-guard + `runMigrations()`
path a real harness boot runs, via a one-shot Node driver. On refusal it
names the blocking PIDs and exits 2; `--kill-blockers` drains them
(SIGTERM, then SIGKILL; never unclassifiable PIDs — hermes bridges
respawn lazily) and retries; `--wait SECONDS` bounds the retry loop.
`db status`/`db blockers` are read-only. Holder classification mirrors
upstream's arc-marker semantics so the utility names the same blockers
upstream's guard would.

### 1c. Shared store classification guard

Upstream `@cortexkit/pi-magic-context` (through 0.46.1) seeds historian run
telemetry with `status='failed'`, and its benign early-return paths (drain
budget exhausted, nothing to process, ...) commit rows with
`status='failed'` and `failure_reason IS NULL`. Consumers that count
unreasoned "failed" historian rows as operational failures (maestro's
`phase1.context.operational` contract, finding d59758598379) then read
routine health as failure.

The guard fixes this at the one boundary every writer shares — the store
itself. `magic-hermes guard apply` installs an idempotent `AFTER INSERT`
trigger (`mh_historian_classification_guard`) on the shared
`historian_runs` table that reclassifies exactly that defect class to
upstream's own benign vocabulary: `status='noop'` plus an explicit marker
reason. Reasoned failures, successes, existing noop rows, and historical
rows are never rewritten, and the trigger never touches `schema_migrations`
(the schema lane).

```bash
/path/to/hermes/venv/bin/magic-hermes guard apply    # install / repair (fail-loud)
/path/to/hermes/venv/bin/magic-hermes guard status   # read-only state
/path/to/hermes/venv/bin/magic-hermes guard remove   # retire
```

- `apply` prints the plan (store path, trigger name), installs or repairs
  the trigger (a drifted body is dropped and re-created from the canonical
  DDL), verifies the classification contract on a throwaway fixture store,
  and exits non-zero when the store is missing or verification fails.
- `status` reports presence, DDL match, and the number of rows reclassified
  in the last 24 hours.
- `doctor` surfaces the guard as PASS (active) or WARN (absent, drifted, or
  unreadable) — never FAIL — because the guard is a workaround applied
  explicitly by the operator, never automatically, and must not hold the
  health gate hostage.

The guard is retired deliberately once an upstream release that classifies
the early-return paths is validated: `RETIRES_AT_UPSTREAM` in
`src/magic_hermes/historian_guard.py` records that release, and
`magic-hermes guard remove` drops the trigger (an idempotent no-op when
absent). `MAGIC_CONTEXT_DB_PATH` overrides the store for testing.

### 1d. Repository provenance guard

Maestro's phase-1 probe pins this repository's canonical origin
(`git@github.com:codeo1io/magic-hermes.git`) and FAILs the estate lane on
any mismatch (finding 4bc6f3a5b0c1: an out-of-band operational action
rewrote the canonical `remote.origin.url` to HTTPS on 2026-09-29 and
mangled `[branch "master"] remote` — the finding then stayed open for
30+ hours). The remote URL is shared mutable state — the canonical
checkout and every conductor worktree share one `.git` — so this
repository now owns the pin itself: `EXPECTED_ORIGIN` in
`src/magic_hermes/provenance.py`. maestro keeps its own independent pin
in its `config/phase1.toml`; the two are deliberate cross-checks, not a
single point of truth.

`magic-hermes provenance` checks the origin against that pin using
maestro-parity normalization — both sides are compared lower-cased with
`.git` stripped, exactly like the probe, so the guard's verdict can never
diverge from the probe's — over four facets: origin match, pushurl
same-repository, branch tracking, and repository readability.

```bash
magic-hermes provenance                        # check-only; exit 0 healthy / 1 drifted or refused
magic-hermes provenance --path /work/projects/magic-hermes
magic-hermes provenance --repair               # restore the pin, carry push intent
magic-hermes provenance --repair --push-url https://github.com/codeo1io/magic-hermes.git
magic-hermes provenance --json                 # machine-readable report + repair log
```

- Check is read-only local git config plumbing; check-only exits 0 when
  healthy, 1 when drifted or refused.
- `--repair` restores a drifted-but-same-repo origin while preserving the
  operator's push intent: the drifted HTTPS fetch URL is carried to
  `remote.origin.pushurl`, so the sanctioned steady state is SSH fetch +
  HTTPS push (the probe reads the fetch URL and PASSes, while pushes ride
  the `gh` credential helper instead of the intermittently flaky SSH
  route). It also restores mangled branch tracking
  (`branch.<b>.remote` / `branch.<b>.merge`). Repair is idempotent
  (re-run on a healthy repo is a no-op) and refuses to touch a checkout
  whose `origin` — or `pushurl` — identifies a different repository:
  repair carries intent, it never guesses one.
- The 2026-09-29 incident, as the guard sees it: fetch URL rewritten to
  `https://github.com/codeo1io/magic-hermes.git`, `pushurl` unset,
  `branch.master.remote` mangled — reported as drift in `origin_match`
  and `branch_tracking`, repaired by one `provenance --repair` that
  restores the SSH fetch URL, carries the HTTPS URL to `pushurl`, and
  restores `branch.master.remote=origin` /
  `branch.master.merge=refs/heads/master`.
- The verdict is **tiered** since finding d59758598379 (2026-10-07):
  the canonical checkout drifted to
  `branch.master.merge=refs/heads/fix/…` — same-origin residue of a
  `git push -u` to a fix branch, every push still aimed at the right
  repository — and the strict doctor row FAILed the estate's
  operational contract over workflow noise. Facets that can retarget
  a push — origin mismatch, foreign `pushurl`, a tracking remote that
  is not `origin` (finding 4bc6f3a5b0c1) — stay **strict**: they FAIL
  the doctor's strict rows (the escalation lane). Same-origin
  merge-residue is **advisory** (`branch_merge_tracking`): it WARNs
  every doctor row — the maestro contract (`rc==0`, `FAIL 0`) stays
  green through benign residue — while the explicit `provenance`
  audit still exits 1 and names the repair.

The check also runs without an installed console script — a bare
checkout, no `pip install`, no `.venv` (the package is stdlib-only).
Both `-m` forms dispatch the same `cli.main`, so exits and output are
identical to the commands above:

```bash
PYTHONPATH=src python3 -m magic_hermes.cli provenance --path /work/projects/magic-hermes
PYTHONPATH=src python3 -m magic_hermes.provenance --repair --push-url https://github.com/codeo1io/magic-hermes.git
```

This is the source-runner vehicle for conductors, worktrees, and CI
scratch clones; it supersedes the `.venv/bin/magic-hermes` shape an
earlier remediation plan assumed. (`python -m magic_hermes.provenance`
used to exit 0 silently; with the `__main__` guard it now behaves
exactly like the subcommand.)

`doctor` surfaces the check standing, scoped by context: strict rows —
an explicit `MAGIC_HERMES_PROVENANCE_REPO` pin and the estate
canonical checkout `/work/projects/magic-hermes` — FAIL on strict
drift and WARN on advisory residue; a same-repo non-canonical checkout
(conductor worktrees, HTTPS dev clones) WARNs advisingly; CI states an
INFO skip (HTTPS clones are the norm there); foreign checkouts stay
silent. In every row the doctor never writes — repair stays an
explicit operator act (`magic-hermes provenance --repair`).

### 2. Manual install (fallback)

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

### 3. Install Magic-Hermes into Hermes

Install the latest published wheel into the Python environment used by Hermes. This
repository is public, so install the release wheel directly from its GitHub Release
URL (shown for v0.3.6; newer versions follow the same URL pattern on the
[Releases](https://github.com/codeo1io/magic-hermes/releases) page):

```bash
uv pip install --python /path/to/hermes/venv/bin/python --no-deps \
  https://github.com/codeo1io/magic-hermes/releases/download/v0.3.6/magic_hermes-0.3.6-py3-none-any.whl
```

If `uv` is not installed, use the Hermes environment's `pip` instead:

```bash
/path/to/hermes/venv/bin/python -m pip install --no-deps \
  https://github.com/codeo1io/magic-hermes/releases/download/v0.3.6/magic_hermes-0.3.6-py3-none-any.whl
```

For development from a local checkout, install the repository directly instead:

```bash
uv pip install --python /path/to/hermes/venv/bin/python --no-deps -e .
```

### 4. Enable Magic-Hermes

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

### 5. Verify the installation (manual path)

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
checks for new core `vX.Y.Z` releases daily at 00:07 UTC (and also supports immediate
`repository_dispatch`), waits for the matching npm publication, and adopts the
newest stable core release above the pinned version, catching up in a single sync
(drafts and prereleases are never adopted). Each validated upstream release
updates the dependency pin and compatibility manifest,
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

### Deployment

Landing a release on `master` does not by itself update the interpreter that
operational probes run: maestro resolves `magic-hermes` through `PATH` to the
Hermes agent venv, which keeps its own installed copy until something
replaces it. The `--deploy` flag closes that delivery gap by making the
deploy part of the release lane itself:

```bash
.venv/bin/python scripts/release.py X.Y.Z --deploy
```

After the release commit is tagged and pushed, and before the GitHub release
object is created, the script installs the freshly built wheel into the deploy
target with `<venv>/bin/python -m pip install --force-reinstall --no-deps
<local wheel>` — a local file (no network) and no dependencies, so a shared
venv never gains or upgrades unrelated packages. It then verifies the
deployment *in the target interpreter*: `importlib.metadata.version`
for `magic-hermes` must equal the release version and the
`bin/magic-hermes` console script must exist. Deploying before publishing is
deliberate: the tag already exists when bits land in the venv, and a deploy
failure aborts the release loudly *before* the GitHub release object is
created — never silently partial. Every step is idempotent, so re-running
the command after a failure picks up where it left off.

The deploy target defaults to `DEFAULT_DEPLOY_VENV`
(`/home/agent/.hermes/hermes-agent/venv`, the interpreter maestro probes);
set `MAGIC_HERMES_DEPLOY_VENV` to deploy elsewhere — tests and other hosts
use this override. A missing venv or interpreter, missing pip, or a failed
verification is a hard release error, never a silent skip.

Setting `MAGIC_HERMES_DEPLOY_SMOKE=1` additionally runs
`<venv>/bin/magic-hermes doctor` after installation and requires a clean
exit, so the exact interpreter that was just deployed proves its own health.

When the GitHub release already exists, `release.py X.Y.Z --deploy` takes a
redeploy path instead of aborting: it requires the tag to exist and point at
current `HEAD`, rebuilds the wheel from the tagged tree, and redeploys
without committing or publishing again.

## License

MIT.
