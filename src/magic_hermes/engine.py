"""MagicContextEngine: hermes ContextEngine backed by the subc daemon.

Fail-closed by design: every daemon interaction is wrapped so that if the
subc daemon is unavailable the engine degrades to a no-op (messages pass
through unchanged) instead of breaking the conversation. The built-in
compressor remains responsible when magic-context is disabled.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .subc.client import SubcError
from .session import MagicContextSession, SessionUnavailable

logger = logging.getLogger("magic_hermes.engine")

# Fractions mirror the TS plugins' compartment planning defaults.
_DEFAULT_THRESHOLD_PERCENT = 0.75

try:  # hermes enforces isinstance(engine, ContextEngine) at registration
    from agent.context_engine import ContextEngine as _ContextEngineBase
except Exception:  # pragma: no cover - hermes not on sys.path (unit tests)
    _ContextEngineBase = object


class MagicContextEngine(_ContextEngineBase):
    """ContextEngine implementation delegating to mc-core over subc.

    Subclasses hermes' ``agent.context_engine.ContextEngine`` when hermes is
    importable (register_context_engine enforces isinstance); falls back to
    ``object`` otherwise so unit tests can construct it without hermes on
    sys.path.
    """

    # -- Identity ----------------------------------------------------------
    name = "magic-context"

    # -- Token state (host reads these directly) ---------------------------
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0

    # -- Compaction parameters ----------------------------------------------
    threshold_percent: float = _DEFAULT_THRESHOLD_PERCENT
    protect_first_n: int = 3
    protect_last_n: int = 6

    # Background compaction is routine maintenance; keep successful passes
    # quiet, surface warnings/errors only.
    emit_automatic_compaction_status = False

    def __init__(self, session: MagicContextSession) -> None:
        self._session = session

    # -- Core interface -----------------------------------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        self.last_prompt_tokens = int(prompt)
        self.last_completion_tokens = int(completion)
        self.last_total_tokens = int(usage.get("total_tokens") or (prompt + completion))
        if self.context_length:
            self.threshold_tokens = int(self.context_length * self.threshold_percent)
        try:
            self._session.call(
                "usage.report",
                {
                    "prompt_tokens": self.last_prompt_tokens,
                    "completion_tokens": self.last_completion_tokens,
                    "total_tokens": self.last_total_tokens,
                    "cache_read_tokens": usage.get("cache_read_tokens", 0),
                    "cache_write_tokens": usage.get("cache_write_tokens", 0),
                    "reasoning_tokens": usage.get("reasoning_tokens", 0),
                },
            )
        except (SessionUnavailable, SubcError, OSError):
            # Fail-closed: token accounting is advisory for the daemon.
            logger.debug(
                "usage.report failed; daemon unavailable or errored", exc_info=True
            )

    def should_compress(self, prompt_tokens: Optional[int] = None) -> bool:
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        if self.threshold_tokens <= 0 or tokens <= 0:
            return False
        return tokens >= self.threshold_tokens

    def should_compress_info(self, prompt_tokens: Optional[int] = None):
        return self.should_compress(prompt_tokens), None

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        try:
            result = self._session.call(
                "context.compact",
                {
                    "messages": messages,
                    "current_tokens": current_tokens,
                    "focus_topic": focus_topic,
                    "force": force,
                    "memory_context": memory_context,
                },
            )
        except (SessionUnavailable, SubcError, OSError):
            logger.warning(
                "magic-context compaction unavailable; returning messages unchanged"
            )
            return messages

        if not isinstance(result, dict):
            logger.warning("context.compact returned unexpected shape; unchanged")
            return messages
        compacted = result.get("messages")
        if isinstance(compacted, list) and compacted:
            self.compression_count += 1
            return compacted
        logger.warning("context.compact returned no messages; unchanged")
        return messages

    # -- Optional hooks -------------------------------------------------------

    def prune_tool_results_only(
        self, messages: List[Dict[str, Any]], current_tokens: Optional[int] = None
    ):
        try:
            result = self._session.call(
                "context.prune_tool_results",
                {"messages": messages, "current_tokens": current_tokens},
            )
        except (SessionUnavailable, SubcError, OSError):
            return messages, 0
        if isinstance(result, dict) and isinstance(result.get("messages"), list):
            return result["messages"], int(result.get("pruned", 0))
        return messages, 0

    def on_session_start(self, session_id: str, **kwargs) -> None:
        try:
            self._session.call(
                "session.begin",
                {
                    "session_id": session_id,
                    "platform": kwargs.get("platform", "hermes"),
                    "model": kwargs.get("model", ""),
                },
            )
        except (SessionUnavailable, SubcError, OSError):
            logger.debug("session.begin failed", exc_info=True)

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        try:
            self._session.call(
                "session.end",
                {"session_id": session_id, "message_count": len(messages)},
            )
        except (SessionUnavailable, SubcError, OSError):
            logger.debug("session.end failed", exc_info=True)

    def on_turn_complete(
        self,
        messages: List[Dict[str, Any]],
        usage: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        if not messages:
            return
        try:
            self._session.call(
                "session.observe_turn",
                {
                    "messages": messages,
                    "turn_id": kwargs.get("turn_id", ""),
                    "interrupted": bool(kwargs.get("interrupted", False)),
                },
            )
        except (SessionUnavailable, SubcError, OSError):
            logger.debug("session.observe_turn failed", exc_info=True)

    def has_content_to_compress(self, messages: List[Dict[str, Any]]) -> bool:
        return len(messages) > (self.protect_first_n + self.protect_last_n)


def engine_from_session(session: MagicContextSession) -> MagicContextEngine:
    """Convenience constructor used by the plugin entry point."""
    return MagicContextEngine(session)


__all__ = ["MagicContextEngine", "engine_from_session"]
