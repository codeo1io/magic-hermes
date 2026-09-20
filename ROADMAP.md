# magic-hermes — Roadmap

> Autonomously maintained by the roadmap sync (reliability-first). Items cite reproducible codebase signals; acceptance is proven by cited evidence.

**Vision**: A reliable, customer-friendly repository advanced by evidence-cited roadmap cycles owned by the autonomy loop

**Pillars**: reliability work outranks customer-experience work; every roadmap item cites reproducible codebase signals; acceptance is proven by cited evidence, never claimed

## Fleet context

- upstreams (this repo builds on): hermes-agent
- dependents (changes here affect): (host)
- graph: evidence-derived (imports/refs/deploy surfaces); advisory

## Open items

### Refactor 21 high-complexity function(s)
- id: `rm-001` | track: reliability | priority: 90.0 | status: candidate
- signals: reliability.complexity_hot:*, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L130, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L143, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L163, reliability.complexity_hot:src/magic_hermes/bridge/runtime.mjs::L181 (+16 more)
- acceptance: Each flagged function is decomposed below the branch threshold with behavior locked by characterization tests
- evidence: ast-based branch-count check passes in CI

### Add test coverage for 1 untested module(s)
- id: `rm-003` | track: reliability | priority: 83.0 | status: candidate
- signals: reliability.no_tests:src/magic_hermes/bridge/loader.mjs
- acceptance: Every module in ['src/magic_hermes/bridge/loader.mjs'] has a corresponding test file with at least one passing test
- evidence: CI: pytest collects the new test files and they pass

### Refresh stale top-level documentation
- id: `rm-002` | track: reliability | priority: 43.0 | status: candidate
- signals: reliability.stale_doc:README.md
- acceptance: Docs regenerated/updated; staleness detector reports 0 signals
- evidence: inference.stale_docs returns [] for the repo

<!-- managed by hermes-roadmap render; do not edit by hand -->
