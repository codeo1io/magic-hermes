"""Hermes ContextEngine backed by the official Magic Context runtime."""

from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .runtime import RuntimeClient, RuntimeErrorBase

log = logging.getLogger(__name__)

try:
    from agent.context_engine import ContextEngine as _ContextEngineBase
except ImportError:  # pragma: no cover - Hermes is absent in isolated unit tests.
    _ContextEngineBase = object

Completion = Callable[..., str]


def _resolve_host_project_root() -> str:
    """Resolve Hermes' logical working directory, falling back to process cwd."""

    try:
        from agent.runtime_cwd import resolve_agent_cwd

        return str(resolve_agent_cwd().resolve())
    except (ImportError, OSError, RuntimeError):
        return str(Path.cwd().resolve())


class MagicContextEngine(_ContextEngineBase):
    """Use upstream Magic Context for indexing, tools, and compartment history."""

    name = "magic-context"
    emit_automatic_compaction_status = False
    protect_first_n = 0
    protect_last_n = 6
    threshold_percent = 0.75

    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_total_tokens = 0
    threshold_tokens = 0
    context_length = 0
    compression_count = 0

    def __init__(
        self,
        *,
        client: RuntimeClient | None = None,
        complete: Completion | None = None,
        project_root: str | os.PathLike[str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._client = client or RuntimeClient()
        self._complete = complete
        self._project_root_pinned = project_root is not None
        self._project_root = (
            str(Path(project_root).resolve())
            if project_root is not None
            else _resolve_host_project_root()
        )
        self._session_id = session_id or "magic-hermes-bootstrap"
        self._bound_identity: tuple[str, str] | None = None
        self._tool_schemas: list[dict[str, Any]] = []
        self._config: dict[str, Any] = {}
        self._compaction_enabled = True

        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

    def __deepcopy__(self, memo: dict[int, Any]) -> MagicContextEngine:
        """Copy configuration, never process handles, locks, or mutable state."""

        project_root = (
            self._project_root
            if self._project_root_pinned
            else _resolve_host_project_root()
        )
        copied = type(self)(
            client=copy.deepcopy(self._client, memo),
            complete=self._complete,
            project_root=project_root,
            session_id=self._session_id,
        )
        copied._project_root_pinned = self._project_root_pinned
        memo[id(self)] = copied
        copied.context_length = self.context_length
        copied.threshold_percent = self.threshold_percent
        copied.threshold_tokens = self.threshold_tokens
        copied.protect_last_n = self.protect_last_n
        copied._compaction_enabled = self._compaction_enabled
        return copied

    def _bind(self) -> bool:
        identity = (self._session_id, self._project_root)
        if self._bound_identity == identity:
            return True
        try:
            result = self._client.call(
                "bind",
                {
                    "session_id": self._session_id,
                    "project_root": self._project_root,
                },
                timeout=60,
            )
        except RuntimeErrorBase:
            log.warning("Magic Context runtime bind failed", exc_info=True)
            return False

        self._bound_identity = identity
        self._config = dict(result.get("config") or {})
        threshold = float(self._config.get("execute_threshold_percentage", 75))
        self.threshold_percent = threshold / 100 if threshold > 1 else threshold
        self._config_threshold_percent = self.threshold_percent
        self._base_threshold_percent = self.threshold_percent
        self._compaction_enabled = bool(
            self._config.get("compaction_enabled", True)
        )
        self._tool_schemas = list(result.get("tool_schemas") or [])
        if self.context_length:
            self.threshold_tokens = int(self.context_length * self.threshold_percent)
        return True

    def _history_budget_tokens(self, budget_tokens: int | None = None) -> int:
        context_limit = int(budget_tokens or self.context_length or 0)
        percentage = float(self._config.get("history_budget_percentage", 0.15))
        if percentage > 1:
            percentage /= 100
        if context_limit <= 0:
            return 16_000
        return max(1_000, int(context_limit * percentage))

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        """Update the active context window without overriding shared policy."""

        del model, base_url, api_key, provider, api_mode
        self.context_length = max(0, int(context_length or 0))
        self.threshold_tokens = int(self.context_length * self.threshold_percent)

    def update_from_response(self, usage: dict[str, Any]) -> None:
        prompt = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        completion = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        self.last_prompt_tokens = int(prompt)
        self.last_completion_tokens = int(completion)
        self.last_total_tokens = int(
            usage.get("total_tokens")
            or self.last_prompt_tokens + self.last_completion_tokens
        )
        if self.context_length:
            self.threshold_tokens = int(self.context_length * self.threshold_percent)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        if not self._bind() or not self._compaction_enabled:
            return False
        tokens = int(
            self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        )
        if self.context_length and not self.threshold_tokens:
            self.threshold_tokens = int(self.context_length * self.threshold_percent)
        return bool(
            tokens > 0
            and self.threshold_tokens > 0
            and tokens >= self.threshold_tokens
        )

    def should_compress_info(
        self, prompt_tokens: int | None = None
    ) -> tuple[bool, str | None]:
        return self.should_compress(prompt_tokens), None

    def has_content_to_compress(self, messages: list[dict[str, Any]]) -> bool:
        meaningful = sum(
            message.get("role") in {"user", "assistant", "tool"}
            for message in messages
        )
        return meaningful > self.protect_last_n + 1

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        del current_tokens, focus_topic, force, memory_context
        if not messages or self._complete is None or not self._bind():
            return messages

        try:
            prepared = self._client.call(
                "historian_prepare",
                {
                    "session_id": self._session_id,
                    "messages": messages,
                    "protect_last_n": self.protect_last_n,
                    "history_budget_tokens": self._history_budget_tokens(),
                },
                timeout=60,
            )
            if not prepared.get("ready"):
                return messages

            output = self._complete(
                system_prompt=prepared["system_prompt"],
                prompt=prepared["prompt"],
                task="mc_historian",
                model=prepared.get("model", ""),
                max_tokens=8192,
                timeout=max(
                    1.0, float(prepared.get("timeout_ms", 120_000)) / 1000
                ),
            )
            published = self._client.call(
                "historian_publish",
                {"session_id": self._session_id, "output": output},
                timeout=60,
            )

            if not published.get("ok") and published.get("repair_prompt"):
                repaired = self._complete(
                    system_prompt=published["system_prompt"],
                    prompt=published["repair_prompt"],
                    task="mc_historian",
                    model=prepared.get("model", ""),
                    max_tokens=8192,
                    timeout=max(
                        1.0,
                        float(prepared.get("timeout_ms", 120_000)) / 1000,
                    ),
                )
                published = self._client.call(
                    "historian_publish",
                    {"session_id": self._session_id, "output": repaired},
                    timeout=60,
                )

            if published.get("needs_editor"):
                edited = self._complete(
                    system_prompt=published["editor_system_prompt"],
                    prompt=published["editor_prompt"],
                    task="mc_historian",
                    model=prepared.get("model", ""),
                    max_tokens=8192,
                    timeout=max(
                        1.0,
                        float(prepared.get("timeout_ms", 120_000)) / 1000,
                    ),
                )
                published = self._client.call(
                    "historian_publish",
                    {
                        "session_id": self._session_id,
                        "output": edited,
                        "editor_pass": True,
                    },
                    timeout=60,
                )

            compacted = published.get("messages")
            if published.get("ok") and isinstance(compacted, list) and compacted:
                self.compression_count += 1
                return compacted
            if not published.get("ok"):
                log.warning(
                    "Magic Context historian output was rejected: %s",
                    published.get("error", "unknown validation error"),
                )
        except Exception:  # The host LLM route may raise provider-specific errors.
            log.warning(
                "Magic Context compaction failed open; transcript is unchanged",
                exc_info=True,
            )
        return messages

    def select_context(
        self,
        request_messages: list[dict[str, Any]],
        *,
        conversation_messages: list[dict[str, Any]] | None = None,
        incoming_message: dict[str, Any] | None = None,
        budget_tokens: int = 0,
    ) -> list[dict[str, Any]] | None:
        del incoming_message
        if not request_messages or not self._bind():
            return None
        del conversation_messages
        try:
            result = self._client.call(
                "render_context",
                {
                    "session_id": self._session_id,
                    "messages": request_messages,
                    "history_budget_tokens": self._history_budget_tokens(
                        budget_tokens
                    ),
                },
                timeout=30,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context per-turn rendering failed open", exc_info=True)
            return None
        selected = result.get("messages")
        if not result.get("history") or not isinstance(selected, list):
            return None
        return selected

    def on_turn_complete(
        self,
        messages: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del usage, kwargs
        if not messages or not self._bind():
            return
        try:
            self._client.call(
                "observe",
                {"session_id": self._session_id, "messages": messages},
                timeout=30,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context turn observation failed", exc_info=True)

    def on_session_start(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = str(session_id)
        root = kwargs.get("project_root") or kwargs.get("cwd")
        self._project_root = (
            str(Path(root).resolve()) if root else _resolve_host_project_root()
        )
        self._bound_identity = None
        if self._bind():
            log.info(
                "Magic Context engine active for Hermes session %s",
                self._session_id,
            )

    def on_session_end(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> None:
        if session_id:
            self._session_id = str(session_id)
        if messages and self._bind():
            try:
                self._client.call(
                    "observe",
                    {"session_id": self._session_id, "messages": messages},
                    timeout=30,
                )
                self._run_dreamer()
            except Exception:
                log.debug("Magic Context session finalization failed", exc_info=True)
        self._client.close()
        self._bound_identity = None

    def _run_dreamer(self) -> None:
        if self._complete is None:
            return
        prepared = self._client.call(
            "dreamer_prepare",
            {"session_id": self._session_id},
            timeout=30,
        )
        if not prepared.get("ready"):
            return
        text = self._complete(
            system_prompt=prepared["system_prompt"],
            prompt=prepared["prompt"],
            task="mc_dreamer",
            model=prepared.get("model", ""),
            max_tokens=4096,
            timeout=120,
        )
        operations = _parse_dreamer_operations(text)
        self._client.call(
            "dreamer_apply",
            {"session_id": self._session_id, "operations": operations},
            timeout=60,
        )

    def on_session_reset(self) -> None:
        try:
            super().on_session_reset()
        except AttributeError:
            self.last_prompt_tokens = 0
            self.last_completion_tokens = 0
            self.last_total_tokens = 0
            self.compression_count = 0
        self._client.close()
        self._session_id = "magic-hermes-bootstrap"
        self._bound_identity = None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        self._bind()
        return list(self._tool_schemas)

    def handle_tool_call(
        self, name: str, args: dict[str, Any], **kwargs: Any
    ) -> str:
        if not self._bind():
            return json.dumps({"error": "Magic Context runtime is unavailable"})
        try:
            result = self._client.call(
                "tool",
                {
                    "session_id": self._session_id,
                    "name": name,
                    "arguments": args,
                    "messages": kwargs.get("messages") or [],
                },
                timeout=60,
            )
        except RuntimeErrorBase as exc:
            return json.dumps({"error": str(exc)})
        payload: dict[str, Any] = {"content": result.get("text", "")}
        if result.get("is_error"):
            payload["error"] = True
        return json.dumps(payload)


def _parse_dreamer_operations(text: str) -> list[dict[str, Any]]:
    """Parse the dreamer's bounded JSON plan without accepting prose."""

    stripped = text.strip()
    if stripped.startswith(chr(96) * 3):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        log.warning("Magic Context dreamer returned invalid JSON; no actions applied")
        return []
    operations = parsed.get("operations") if isinstance(parsed, dict) else None
    return [item for item in operations or [] if isinstance(item, dict)]


__all__ = ["MagicContextEngine"]
