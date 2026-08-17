"""Hermes ``MemoryProvider`` adapter backed by the Magic Context daemon.

Implements the hermes-agent ``agent.memory_provider.MemoryProvider``
interface as a thin connector: durable memories and search queries are
forwarded to the shared Magic Context store over the subc session; nothing
is persisted locally.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Method names routed to the shared mc-module runtime.
_METHOD_SEARCH = "memory.search"
_METHOD_WRITE = "memory.write"
_METHOD_LIST = "memory.list"
_METHOD_ARCHIVE = "memory.archive"
_METHOD_NOTES = "notes.status"

_SYSTEM_BLOCK_HEADER = "## Project Memory"


class MagicContextMemoryProvider:
    """Exclusive hermes memory provider served by Magic Context.

    The provider is created with a ``session_factory`` returning a live
    :class:`~magic_hermes.session.MagicContextSession`. When the daemon is
    unavailable (``is_available()`` false or session open fails) every
    surface degrades gracefully — empty prompt block, no-op writes — so a
    down daemon never breaks a hermes session.
    """

    name = "magic-context"

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self._session_id: str = ""
        self._lock = threading.Lock()
        self._memories: Optional[List[Dict[str, Any]]] = None

    # -- lifecycle -----------------------------------------------------

    def is_available(self) -> bool:
        try:
            self._ensure_session()
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        try:
            self._refresh_memories()
        except Exception:
            logger.warning("magic-context: initial memory load failed", exc_info=True)

    def shutdown(self) -> None:
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    # -- system prompt + prefetch --------------------------------------

    def system_prompt_block(self) -> str:
        memories = self._cached_memories() or []
        if not memories:
            return ""
        lines = [_SYSTEM_BLOCK_HEADER]
        for mem in memories:
            mid = mem.get("id", "?")
            text = str(mem.get("content", mem.get("text", ""))).strip()
            cat = mem.get("category")
            line = f"- #{mid}: {text}"
            if cat:
                line = f"- #{mid} ({cat}): {text}"
            lines.append(line)
        return "\n".join(lines)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            self._ensure_session()
            result = self._session.call(_METHOD_SEARCH, {"query": query, "limit": 5})
        except Exception:
            return ""
        return _format_search_block(query, result)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        # Fire-and-forget prefetch is unnecessary: search is a single fast
        # daemon round-trip over loopback.
        pass

    # -- tools ----------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "ctx_memory",
                "description": (
                    "Manage durable project memories. Actions: write (new "
                    "memory), search (ranked recall), list (all), archive "
                    "(retire by id)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["write", "search", "list", "archive"],
                        },
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "query": {"type": "string"},
                        "ids": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["action"],
                },
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name != "ctx_memory":
            return json.dumps({"error": f"unknown tool {tool_name}"})
        action = args.get("action", "")
        try:
            self._ensure_session()
            if action == "write":
                result = self._session.call(
                    _METHOD_WRITE,
                    {
                        "content": args.get("content", ""),
                        "category": args.get("category"),
                    },
                )
                self._refresh_memories()
            elif action == "search":
                result = self._session.call(
                    _METHOD_SEARCH, {"query": args.get("query", ""), "limit": 10}
                )
            elif action == "list":
                result = self._session.call(_METHOD_LIST, {})
            elif action == "archive":
                result = self._session.call(
                    _METHOD_ARCHIVE, {"ids": args.get("ids", [])}
                )
                self._refresh_memories()
            else:
                return json.dumps({"error": f"unknown action {action}"})
        except Exception as exc:
            return json.dumps({"error": f"magic-context daemon unavailable: {exc}"})
        return json.dumps(result, default=str)

    # -- session events --------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        # Persistence is continuous (daemon-side); no end-of-session flush.
        pass

    def on_memory_write(self, text: str, **kwargs) -> None:
        try:
            self._ensure_session()
            self._session.call(_METHOD_WRITE, {"content": text})
            self._refresh_memories()
        except Exception:
            logger.warning("magic-context: memory write failed", exc_info=True)

    # -- helpers ---------------------------------------------------------

    def _ensure_session(self):
        with self._lock:
            if self._session is None:
                self._session = self._session_factory()
            return self._session

    def _refresh_memories(self) -> None:
        self._ensure_session()
        result = self._session.call(_METHOD_LIST, {})
        with self._lock:
            self._memories = (
                result.get("memories", []) if isinstance(result, dict) else []
            )

    def _cached_memories(self) -> Optional[List[Dict[str, Any]]]:
        if self._memories is None:
            try:
                self._refresh_memories()
            except Exception:
                return []
        with self._lock:
            return list(self._memories or [])


def _format_search_block(query: str, result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    hits = result.get("hits") or result.get("results") or []
    if not hits:
        return ""
    lines = [f"Memories relevant to: {query!r}"]
    for hit in hits:
        text = str(hit.get("text", hit.get("content", ""))).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)
