"""Recall/context tools exposed to hermes agents (U4).

Mirrors the pi/opencode plugin's tool surface: ctx_search, ctx_expand,
ctx_reduce, ctx_memory, ctx_note. Handlers bridge hermes tool invocations
to magic-context module calls over the subc session.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .session import MagicContextSession, SessionUnavailable
from .subc.client import SubcError

logger = logging.getLogger(__name__)

SEARCH_TOOL_SCHEMA = {
    "name": "ctx_search",
    "description": (
        "Search everything that ever happened on this project — memories, "
        "raw conversation history (including compacted parts), git commits, "
        "and notes — from one ranked query."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict to specific sources",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
    },
}

EXPAND_TOOL_SCHEMA = {
    "name": "ctx_expand",
    "description": (
        "Recover the full raw content of a message or range by ordinal "
        "(e.g. from ctx_search hits or session-history compartments)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "message": {"type": "integer"},
        },
        "required": [],
    },
}

REDUCE_TOOL_SCHEMA = {
    "name": "ctx_reduce",
    "description": (
        "Mark spent tagged conversation content as discardable to reclaim "
        "context space. Accepts ranges like '3-5' or lists like '1,2,9'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"drop": {"type": "string"}},
        "required": ["drop"],
    },
}

MEMORY_TOOL_SCHEMA = {
    "name": "ctx_memory",
    "description": (
        "Long-term recall: write/update/archive durable project memories, or list them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "update", "archive", "merge", "get", "list"],
            },
            "content": {"type": "string"},
            "category": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "number"}},
            "reason": {"type": "string"},
        },
        "required": ["action"],
    },
}

NOTE_TOOL_SCHEMA = {
    "name": "ctx_note",
    "description": (
        "Working notes for this session's future — reminders and "
        "follow-ups that resurface at natural work boundaries."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "read", "update", "dismiss"],
            },
            "content": {"type": "string"},
            "note_id": {"type": "number"},
            "filter": {"type": "string"},
        },
        "required": ["action"],
    },
}

TOOL_SPECS: list[dict[str, Any]] = [
    SEARCH_TOOL_SCHEMA,
    EXPAND_TOOL_SCHEMA,
    REDUCE_TOOL_SCHEMA,
    MEMORY_TOOL_SCHEMA,
    NOTE_TOOL_SCHEMA,
]


def _safe(method: str, session: MagicContextSession):
    """Invoke a module call, converting failures into tool-visible errors."""

    def handler(payload: dict[str, Any]) -> str:
        try:
            result = session.call(method, payload)
        except (SessionUnavailable, SubcError, OSError) as exc:
            logger.warning("ctx tool %s failed: %s", method, exc)
            return json.dumps({"error": str(exc)})
        return json.dumps(result, default=str)

    return handler


def make_handlers(session: MagicContextSession) -> dict[str, Callable[[dict], str]]:
    """Build hermes tool handlers bound to a connected session."""
    return {
        "ctx_search": _safe("memory.search", session),
        "ctx_expand": _safe("memory.expand", session),
        "ctx_reduce": _safe("memory.reduce", session),
        "ctx_memory": _safe("memory.manage", session),
        "ctx_note": _safe("notes.manage", session),
    }


def register_tools(ctx, session: MagicContextSession) -> list[str]:
    """Register all ctx tools on a hermes PluginContext.

    Returns the tool names registered. `ctx` must expose register_tool()
    with the standard signature (name, toolset, schema, handler, ...).
    """
    handlers = make_handlers(session)
    names: list[str] = []
    for schema in TOOL_SPECS:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="magic-context",
            schema=schema,
            handler=handlers[name],
            description=schema["description"],
        )
        names.append(name)
    return names
