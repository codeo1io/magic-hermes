# magic-hermes — Roadmap

> Autonomously maintained by the roadmap sync (reliability-first). Items cite reproducible codebase signals; acceptance is proven by cited evidence.

**Vision**: A reliable, customer-friendly repository advanced by evidence-cited roadmap cycles owned by the autonomy loop

**Pillars**: reliability work outranks customer-experience work; every roadmap item cites reproducible codebase signals; acceptance is proven by cited evidence, never claimed

## Open items

### Add test coverage for 5 untested module(s)
- id: `rm-003` | track: reliability | priority: 95.0 | status: candidate
- signals: reliability.no_tests:src/magic_hermes/bridge/loader.mjs, reliability.no_tests:src/magic_hermes/engine.py, reliability.no_tests:src/magic_hermes/memory_provider.py, reliability.no_tests:src/magic_hermes/plugin.py, reliability.no_tests:src/magic_hermes/runtime.py
- acceptance: Every module in ['src/magic_hermes/bridge/loader.mjs', 'src/magic_hermes/engine.py', 'src/magic_hermes/memory_provider.py', 'src/magic_hermes/plugin.py', 'src/magic_hermes/runtime.py'] has a corresponding test file with at least one passing test
- evidence: CI: pytest collects the new test files and they pass

### Refactor 20 high-complexity function(s)
- id: `rm-001` | track: reliability | priority: 90.0 | status: candidate
- signals: reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L111, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L160, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L173, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L193, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L211 (+15 more)
- acceptance: Each flagged function is decomposed below the branch threshold with behavior locked by characterization tests
- evidence: ast-based branch-count check passes in CI

### Refresh stale top-level documentation
- id: `rm-002` | track: reliability | priority: 43.0 | status: candidate
- signals: reliability.stale_doc:README.md
- acceptance: Docs regenerated/updated; staleness detector reports 0 signals
- evidence: inference.stale_docs returns [] for the repo

## Closed items

- `rm-004` Adopt upstream Magic Context v0.42.6 — done
- `rm-005` Correct README sync-cadence claim — superseded
- `rm-007` Allowlist-based read_only gating and plugin lifecycle tests — done
- `rm-006` Harden sync-workflow reliability — done
- `rm-008` Runtime reliability hardening (timeouts, shutdown race, unbounded caches) — done
- `rm-010` Hermes slash-command parity (/ctx-status, /ctx-flush, /ctx-recomp, /ctx-wrapup, /ctx-dream) — done
- `rm-011` Docs/DX: storage override, dashboard visibility, 0.42.6 parity notes — done
- `rm-009` Dead code removal — done

<!-- managed by hermes-roadmap render; do not edit by hand -->
