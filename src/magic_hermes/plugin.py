"""Hermes plugin entry point for magic-hermes.

Composes the U1-U6 adapters onto hermes' native plugin surfaces:

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


def register(ctx):
    """Entry point for hermes plugin discovery.

    hermes calls ``register(ctx)`` for every discovered plugin (directory
    ``plugin.yaml`` manifests and pip entry points in the
    ``hermes_agent.plugins`` group both land here). ``load`` stays
    keyword-friendly for tests and direct embedding.
    """
    return load(ctx)


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

    from .auxiliary import (
        register as register_auxiliary,
    )
    from .engine import engine_from_session
    from .jsonc import load_jsonc
    from .memory_provider import MagicContextMemoryProvider
    from .tools import register_tools

    config = load_jsonc() or {}
    if getattr(ctx, "register_auxiliary_task", None) is not None:
        queues = register_auxiliary(ctx, config)
    else:
        queues = {}
    registered["auxiliary"] = queues
    # Wire the historian signal queue into the engine so successful
    # compaction passes reach the mc_historian auxiliary task (U6).
    signal_queue = queues.get("mc_historian")

    _register(
        ctx, "register_context_engine", engine_from_session(session, signal_queue)
    )
    registered["tools"] = register_tools(ctx, session)
    registered["memory"] = _register(
        ctx,
        "register_memory_provider",
        MagicContextMemoryProvider(session_factory=lambda: session),
    )

    return registered


def _register(ctx, api_name: str, value):
    fn = getattr(ctx, api_name, None)
    if fn is None:
        log.debug("magic-hermes: context lacks %s; skipping", api_name)
        return False
    fn(value)
    return True
