"""Exclusive Hermes memory provider backed by upstream Magic Context."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .runtime import (
    RuntimeClient,
    RuntimeErrorBase,
    runtime_available,
    runtime_unavailable_reason,
)

log = logging.getLogger(__name__)

try:
    from agent.memory_provider import (
        MemoryProvider as _MemoryProviderBase,
    )
    from agent.memory_provider import (
        RecallStatus,
    )
except ImportError:  # pragma: no cover - Hermes is absent in isolated unit tests.
    _MemoryProviderBase = object
    RecallStatus = None  # type: ignore[assignment,misc]


class MagicContextMemoryProvider(_MemoryProviderBase):
    """Expose Magic Context as Hermes' one selected external memory backend."""

    def __init__(
        self,
        *,
        client: RuntimeClient | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._client = client or RuntimeClient()
        self._project_root = str(Path(project_root or Path.cwd()).resolve())
        self._session_id = "magic-hermes-memory-bootstrap"
        self._bound_identity: tuple[str, str] | None = None
        self._tool_schemas: list[dict[str, Any]] = []
        self._cache_lock = threading.Lock()
        self._cached_context: dict[str, tuple[str, int]] = {}
        self._last_recall_count = 0
        self._background: set[threading.Thread] = set()
        self._shutdown = threading.Event()

    @property
    def name(self) -> str:
        return "magic_context"

    def is_available(self) -> bool:
        """Perform local dependency checks only, as required by Hermes."""

        return runtime_available()

    def unavailable_reason(self) -> str:
        return runtime_unavailable_reason()

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._shutdown.clear()
        self._session_id = str(session_id)
        root = kwargs.get("project_root") or kwargs.get("cwd")
        if root:
            self._project_root = str(Path(root).resolve())
        else:
            self._project_root = str(Path.cwd().resolve())
        self._bound_identity = None
        if not self._bind():
            reason = runtime_unavailable_reason() or "Magic Context bind failed"
            raise RuntimeError(reason)
        self._refresh_context(self._session_id)

    def _bind(self) -> bool:
        if self._shutdown.is_set():
            return False
        identity = (self._session_id, self._project_root)
        if self._bound_identity == identity:
            return True
        try:
            self._client.call(
                "bind",
                {
                    "session_id": self._session_id,
                    "project_root": self._project_root,
                },
                timeout=60,
            )
        except RuntimeErrorBase:
            log.warning("Magic Context memory provider bind failed", exc_info=True)
            return False
        self._bound_identity = identity
        # The context engine owns every ctx_* tool, including ctx_memory.
        # This provider supplies Hermes' standard recall/injection lifecycle.
        self._tool_schemas = []
        return True

    def system_prompt_block(self) -> str:
        """Return no independent guidance; upstream MC owns prompt policy.

        The ContextEngine injects `buildMagicContextBlock()` from the shared
        CortexKit config on each request. A second Hermes-memory-provider block
        would drift from `prompt_surface`, language, compaction and smart-note
        settings and would break prompt-cache prefix parity.
        """

        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Report recall status without duplicating MC's request-time m[0].

        The ContextEngine is the sole Magic Context render owner and injects the
        upstream m[0]/m[1] payload (memory, docs/profile, history, mural). Hermes'
        MemoryManager would otherwise wrap this same project memory into the live
        user message before select_context(), producing two independent copies.
        """

        del query
        key = session_id or self._session_id
        with self._cache_lock:
            _text, count = self._cached_context.get(key, ("", 0))
            self._last_recall_count = count
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        del query
        self._spawn(self._refresh_context, session_id or self._session_id)

    def recall_status(self):
        if not self._last_recall_count or RecallStatus is None:
            return None
        return RecallStatus(
            provider_label="Magic Context",
            count=self._last_recall_count,
        )

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        del user_content, assistant_content
        if not messages:
            return
        target = session_id or self._session_id
        self._spawn(self._observe, target, list(messages))

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        self._bind()
        return list(self._tool_schemas)

    def handle_tool_call(
        self, tool_name: str, args: dict[str, Any], **kwargs: Any
    ) -> str:
        if not self._bind():
            return json.dumps({"error": "Magic Context runtime is unavailable"})
        try:
            result = self._client.call(
                "tool",
                {
                    "session_id": self._session_id,
                    "name": tool_name,
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
        self._spawn(self._refresh_context, self._session_id)
        return json.dumps(payload)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        if self._bind() and messages:
            self._observe(self._session_id, messages)
            self._refresh_context(self._session_id)
        return self.prefetch("", session_id=self._session_id)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if self._bind() and messages:
            self._observe(self._session_id, messages)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        del parent_session_id, reset, rewound
        self._session_id = str(new_session_id)
        root = kwargs.get("project_root") or kwargs.get("cwd")
        if root:
            self._project_root = str(Path(root).resolve())
        self._bound_identity = None
        if self._bind():
            self._refresh_context(self._session_id)

    def shutdown(self) -> None:
        self._shutdown.set()
        self._client.close()

    def _observe(
        self, session_id: str, messages: list[dict[str, Any]]
    ) -> None:
        if self._shutdown.is_set() or session_id != self._session_id:
            return
        try:
            self._client.call(
                "observe",
                {"session_id": session_id, "messages": messages},
                timeout=30,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context memory observation failed", exc_info=True)

    def _refresh_context(self, session_id: str) -> None:
        if (
            self._shutdown.is_set()
            or session_id != self._session_id
            or not self._bind()
        ):
            return
        try:
            result = self._client.call(
                "memory_context",
                {"session_id": session_id},
                timeout=30,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context memory prefetch failed", exc_info=True)
            return
        with self._cache_lock:
            self._cached_context[session_id] = (
                str(result.get("text") or ""),
                int(result.get("count") or 0),
            )

    def _spawn(self, target, *args: Any) -> None:
        if self._shutdown.is_set():
            return

        def run() -> None:
            try:
                if not self._shutdown.is_set():
                    target(*args)
            finally:
                with self._cache_lock:
                    self._background.discard(thread)

        thread = threading.Thread(
            target=run,
            name="magic-context-memory",
            daemon=True,
        )
        with self._cache_lock:
            self._background.add(thread)
        thread.start()


def register(ctx) -> MagicContextMemoryProvider:
    """Entry-point callback supported by Hermes' exclusive provider loader."""

    provider = MagicContextMemoryProvider()
    register_provider = getattr(ctx, "register_memory_provider", None)
    if register_provider is None:
        raise RuntimeError("Hermes memory provider context is missing registration")
    register_provider(provider)
    return provider


__all__ = ["MagicContextMemoryProvider", "register"]
