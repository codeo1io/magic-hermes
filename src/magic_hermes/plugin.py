"""Hermes plugin entry point for magic-hermes.

Registers Magic Context surfaces on hermes' native plugin API
(hermes_cli/plugins.py). Implementation pending planning; the registration
map below is the requirement contract from
docs/plans/2026-08-17-001-feature-hermes-connector-plan.md.

    register_context_engine(engine)   -> compaction / session-history
    register_tool(...)                -> ctx_search/expand/reduce/note
    register_hook / register_middleware -> session lifecycle, reduction
    hermes memory config surface      -> persistent memories

Hermes base classes (e.g. agent.context_engine.ContextEngine) are imported
late, inside load(), mirroring how hermes itself avoids circular imports.
"""
