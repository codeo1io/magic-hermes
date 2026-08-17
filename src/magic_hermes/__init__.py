"""magic-hermes: Magic Context connector for hermes-agent.

Connector only — all Magic Context logic lives in the upstream monorepo and
runs under the subc daemon. This package provides:
  - subc client (loopback TCP, HMAC-SHA256, wire-compatible with the TS/Rust
    consumers)
  - the hermes plugin entry point registering context engine, tools, hooks

See docs/plans/2026-08-17-001-feature-hermes-connector-plan.md for the
requirements contract.
"""

__version__ = "0.1.0.dev0"
