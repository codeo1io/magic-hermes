---
title: CI test battery fails on fresh runners — historian readiness needs node>=23 and a user-level CortexKit config
date: 2026-09-29
category: docs/solutions/test-failures
module: CI test battery (.github/workflows/ci.yml) + tests/test_runtime_integration.py
problem_type: test_failure
component: testing_framework
symptoms:
  - 'On a fresh GitHub runner, the 4 historian integration tests fail with historian_prepare returning ready:false'
  - 'Failure reason string is "historian-disabled" — not a missing-file or download error'
  - 'The identical tree passes the full battery locally (109 collected: 108 passed, 1 skipped)'
  - 'Same 4 tests fail identically on node 22 and node 24 before the config fix — misleading (two stacked causes share one symptom)'
root_cause: test_isolation
resolution_type: environment_setup
severity: medium
tags: [ci, github-actions, historian, node-sqlite, cortexkit-config, test-isolation]
---

# CI test battery fails on fresh runners — historian readiness needs node>=23 and a user-level CortexKit config

## Problem

The PR/push CI workflow added in rm-018 ran the full pytest battery green on the
development host but red on GitHub-hosted runners: four
`tests/test_runtime_integration.py` cases (all asserting
`historian_prepare` → `ready: true`) failed on every fresh machine while
everything else passed. The battery is not hermetic — historian readiness
depends on two machine-global conditions that developer machines happen to
satisfy and fresh runners do not.

## Symptoms

- `historian_prepare` responses contain `ready: false, reason: "historian-disabled"`.
- Only the historian-integration tests fail; the other ~105 tests pass.
- Failure is 100% reproducible per-machine: same commit, same command,
  green locally / red on GitHub Actions.

## What Didn't Work

Diagnosed in run 230b8e0beab8's full_tests phase, two failed CI cycles before the
real causes landed:

- **Pinning node 24 first (after a `node:sqlite` hypothesis).** Necessary but
  not sufficient: the same 4 tests kept failing. Node version was one of two
  stacked causes, so fixing it alone changed nothing observable.
- **`npm ci --ignore-scripts` suspicion.** Disproven: the failing tests pass
  locally against an `--ignore-scripts` install (native optional deps
  `onnxruntime-node`/`sharp` are not on the readiness path).
- **Seeding the Xenova/all-MiniLM-L6-v2 embeddings model (~87 MB) into a fresh
  HOME.** Disproven: readiness stayed `false` with the model present — the
  historian gate never asked for embeddings.
- **Copying `~/.pi/agent` (models.json + settings.json) into a fresh HOME.**
  Insufficient on its own: still `historian-disabled`.

The decisive method was local emulation, not CI thrashing: reproduce the
symptom on the dev host by removing one machine-global condition at a time
(fake `$HOME` probe; `NODE_OPTIONS=--no-experimental-sqlite`), then instrument
the exact call (`historian_prepare` payload) until the refusal reason named the
real gate.

## Solution

Two fixes in `.github/workflows/ci.yml`:

1. **node 24** (`.github/workflows/ci.yml:30`) — the sidecar's historian needs
   unflagged `node:sqlite`, available without `--experimental-sqlite` only on
   node >= 23. Verified locally: disabling node:sqlite on the dev host
   reproduces the exact same 4 failures.

2. **Seed a minimal user-level CortexKit config** before the battery
   (`.github/workflows/ci.yml:50-59`):

   ```yaml
   - name: Seed historian model config (user-level CortexKit config)
     run: |
       mkdir -p "$HOME/.config/cortexkit"
       cat > "$HOME/.config/cortexkit/magic-context.jsonc" <<'EOF'
       {
         "historian": {
           "pi": { "model": "anthropic/claude-sonnet-4-5" }
         }
       }
       EOF
   ```

   Probe for future diagnosis (run against the repo's bridge with
   `MAGIC_CONTEXT_PACKAGE_ROOT` set, fake `$HOME` to prove the fresh-machine
   path):

   ```bash
   HOME=/tmp/fresh-home node --input-type=module -e '
     import { runtimeProbe } from "./src/magic_hermes/bridge/runtime.mjs";
     // any session harness that calls the sidecar historian_prepare
     console.log(await runtimeProbe("historian_prepare", session));'
   # ready:true  ⇔  user config carried historian.<harness>.model
   ```

## Why This Works

Historian readiness is decided in the repo's bridge at
`src/magic_hermes/bridge/runtime.mjs:967` via the package's
`resolveHistorianFromConfig(session.config)` (package dist index.js:35688),
which calls `resolveHistorianModel(config, harness)` (dist index.js:754). No
resolved historian LLM model ⇒ `historian-disabled`, regardless of models,
store, or endpoints on disk. Session config merges the package's
`loadPiConfig` user layer, whose base path is
`$HOME/.config/cortexkit/magic-context.jsonc` — a file that exists on
configured developer machines and never on a fresh runner. The config only has
to NAME a model; readiness never calls the endpoint (no API key, no network,
no `~/.pi`, no embeddings model needed — each proven by a bare-fresh-HOME
probe returning `ready: true` with just this file). A neutral public model id
is used deliberately: this repo is public and host configs name internal
endpoints.

## Prevention

- **Make the tests seed their own config** (roadmap rm-022): a fixture that
  points the config lookup at a temp dir with a minimal historian block would
  make the battery hermetic and delete the CI seed step's raison d'être.
- **Pin node >= 23 anywhere the sidecar runs** — `node:sqlite` is load-bearing
  for the historian; document it with rm-023.
- **When a test suite is green locally and red in CI, enumerate machine-global
  deltas (HOME config, node version, caches) and bisect them locally** — the
  two stacked causes here shared one symptom, so single-cause fixes looked
  like no-ops.
- **Trust the refusal reason string.** `historian-disabled` pointed at config,
  not at downloads; two of the failed hypotheses chased artifacts instead.

## Related Issues

- ROADMAP.md rm-018 (this CI workflow), rm-022/rm-023 (hermeticity +
  documentation follow-ups born from this incident).
- Evidence: GitHub Actions battery job
  https://github.com/codeo1io/magic-hermes/actions/runs/36524749398/job/109265200023
  (green after both fixes; the two red cycles that isolated the causes are in
  the conductor delegate spool, run 230b8e0beab8 full_tests attempts
  f622740d and aedd5736).
