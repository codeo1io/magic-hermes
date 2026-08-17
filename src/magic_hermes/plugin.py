"""Hermes plugin entry point for magic-hermes.

Composes the U1–U6 adapters onto hermes' native plugin surfaces:

    register_context_engine(engine)   -> compaction / session-history
    register_tool(...)                -> ctx_search/expand/reduce/note
    register_hook / register_middleware -> session lifecycle, reduction
    hermes memory config surface      -> persistent memories
    auxiliary task API                -> mc_historian / mc_dreamer

Fail-closed: when no subc daemon is discoverable, load() returns a disabled
registration instead of raising — hermes keeps its native compressor.
"""

from __future__ import annotations

import logging

from .discovery import discover_connection_file
from .session import MagicContextSession

log = logging.getLogger(__name__)

PLUGIN_API_VERSION = "0.1"


def load(ctx, *, project_root: str | None = None, session_id: str | None = None):
    """Register Magic Context surfaces on a hermes plugin context.

    ``ctx`` is hermes' plugin registration context exposing
    register_context_engine / register_tool / register_hook /
    register_memory_provider / register_auxiliary_task (exact names are
    resolved defensively — see _register).
    """
    if discover_connection_file() is None:
        log.warning(
            "magic-hermes: no subc daemon connection file found; "
            "plugin disabled, hermes native context engine stays active"
        )
        return {"enabled": False, "reason": "no-subc-daemon"}

    session = MagicContextSession(project_root=project_root, session_id=session_id)
    registered = {"enabled": True}

    from .engine import engine_from_session
    from .tools import register_tools
    from .memory_provider import MagicContextMemoryProvider
    from .auxiliary import (
        register as register_auxiliary,
        auxiliary_defaults_from_config,
    )
    from .jsonc import load_jsonc

    _register(ctx, "register_context_engine", engine_from_session(session))
    registered["tools"] = register_tools(ctx, session)
    registered["memory"] = _register(
        ctx,
        "register_memory_provider",
        MagicContextMemoryProvider(session_factory=lambda: session),
    )
    config = load_jsonc() or {}
    registered["auxiliary"] = register_auxiliary(ctx, config)

    return registered


def _register(ctx, api_name: str, value):
    fn = getattr(ctx, api_name, None)
    if fn is None:
        log.debug("magic-hermes: context lacks %s; skipping", api_name)
        return False
    fn(value)
    return True
