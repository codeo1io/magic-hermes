"""Hermes ContextEngine backed by the official Magic Context runtime."""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import os
import threading
import uuid
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
SessionRoute = Callable[[str, str | None], None]


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
    # Manual /compress fallback only; automatic boundaries are MC-owned.
    protect_last_n = 6
    # Hermes host gate only; normal MC maintenance runs asynchronously.
    threshold_percent = 0.95

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
        session_route: SessionRoute | None = None,
    ) -> None:
        self._client = client or RuntimeClient()
        self._complete = complete
        self._session_route = session_route
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
        self._model = ""
        self._provider = ""
        self._historian_lock = threading.Lock()
        self._historian_thread: threading.Thread | None = None
        self._maintenance_lock = threading.Lock()
        self._maintenance_thread: threading.Thread | None = None
        self._dreamer_lock = threading.Lock()
        self._dreamer_thread: threading.Thread | None = None

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
            session_route=self._session_route,
        )
        copied._project_root_pinned = self._project_root_pinned
        memo[id(self)] = copied
        copied.context_length = self.context_length
        copied.threshold_percent = self.threshold_percent
        copied.threshold_tokens = self.threshold_tokens
        copied.protect_last_n = self.protect_last_n
        copied._compaction_enabled = self._compaction_enabled
        copied._model = self._model
        copied._provider = self._provider
        return copied

    def close(self) -> None:
        """Release this session engine's private Magic Context runtime.

        Context engines are deep-copied by Hermes for individual sessions, so each
        copy owns a distinct RuntimeClient/Node sidecar. Teardown must release both
        the host routing entry and that sidecar rather than leaving it attached to
        the long-lived gateway service.
        """

        route = self._session_route
        if route is not None:
            try:
                route(self._session_id, None)
            except Exception:
                log.debug("Magic Context session-route cleanup failed", exc_info=True)
        self._client.close()
        self._bound_identity = None

    def __del__(self) -> None:
        """Best-effort cleanup when a host lifecycle omits explicit ``close()``."""

        try:
            self.close()
        except Exception:  # pragma: no cover - destructor must never escape
            pass

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
        self._compaction_enabled = bool(
            self._config.get("compaction_enabled", True)
        )
        self._tool_schemas = list(result.get("tool_schemas") or [])
        # Hermes' threshold fields represent only the synchronous safety gate.
        # Normal scheduling, including model-specific/absolute thresholds and
        # cache TTL, is owned by the upstream Magic Context runtime.
        self.threshold_percent = 0.95
        self._config_threshold_percent = self.threshold_percent
        self._base_threshold_percent = self.threshold_percent
        if self.context_length:
            self.threshold_tokens = int(self.context_length * self.threshold_percent)
        if self._model or self.context_length:
            try:
                self._client.call(
                    "model_update",
                    {
                        "session_id": self._session_id,
                        "model": self._model,
                        "provider": self._provider,
                        "context_length": self.context_length,
                    },
                    timeout=30,
                )
            except RuntimeErrorBase:
                log.debug("Magic Context model-state sync failed", exc_info=True)
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
        """Report host model geometry to the upstream Magic Context runtime."""

        del base_url, api_key, api_mode
        self._model = str(model or "")
        self._provider = str(provider or "")
        self.context_length = max(0, int(context_length or 0))
        self.threshold_tokens = int(self.context_length * self.threshold_percent)
        if self._bind():
            try:
                self._client.call(
                    "model_update",
                    {
                        "session_id": self._session_id,
                        "model": self._model,
                        "provider": self._provider,
                        "context_length": self.context_length,
                    },
                    timeout=30,
                )
            except RuntimeErrorBase:
                log.debug("Magic Context model-state update failed", exc_info=True)

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
        if self._bind():
            try:
                self._client.call(
                    "usage_update",
                    {
                        "session_id": self._session_id,
                        "input_tokens": self.last_prompt_tokens,
                        "context_length": self.context_length,
                    },
                    timeout=30,
                )
            except RuntimeErrorBase:
                log.debug("Magic Context usage-state update failed", exc_info=True)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """Block only at Magic Context's upstream emergency pressure band."""

        if not self._bind() or not self._compaction_enabled:
            return False
        tokens = int(
            self.last_prompt_tokens if prompt_tokens is None else prompt_tokens
        )
        try:
            pressure = self._client.call(
                "pressure_state",
                {
                    "session_id": self._session_id,
                    "input_tokens": tokens,
                    "context_length": self.context_length,
                },
                timeout=30,
            )
        except RuntimeErrorBase:
            return False
        emergency = float(pressure.get("emergency_percentage", 95)) / 100
        self.threshold_percent = emergency
        if self.context_length:
            self.threshold_tokens = int(self.context_length * emergency)
        return bool(pressure.get("should_block"))

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

    def _run_historian_pass(
        self,
        client: RuntimeClient,
        *,
        session_id: str,
        project_root: str,
        messages: list[dict[str, Any]],
        boundary_snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Run one upstream historian transaction on an isolated runtime client."""

        if self._complete is None:
            return None
        prepared_started = False
        published_ok = False
        lease_stop = threading.Event()
        lease_thread: threading.Thread | None = None
        try:
            client.call(
                "bind",
                {"session_id": session_id, "project_root": project_root},
                timeout=60,
            )
            if self._model or self.context_length:
                client.call(
                    "model_update",
                    {
                        "session_id": session_id,
                        "model": self._model,
                        "provider": self._provider,
                        "context_length": self.context_length,
                    },
                    timeout=30,
                )
            payload: dict[str, Any] = {
                "session_id": session_id,
                "messages": messages,
                "protect_last_n": self.protect_last_n,
                "history_budget_tokens": self._history_budget_tokens(),
                "holder_id": f"magic-hermes:{uuid.uuid4()}",
            }
            if boundary_snapshot:
                payload["boundary_snapshot"] = boundary_snapshot
            prepared = client.call("historian_prepare", payload, timeout=60)
            if not prepared.get("ready"):
                return None
            prepared_started = True

            def renew_lease() -> None:
                # Upstream renews compartment leases every 60 seconds against a
                # five-minute TTL. Keep the same cadence while Hermes owns the
                # slow model call so another harness cannot duplicate the pass.
                while not lease_stop.wait(60.0):
                    try:
                        renewed = client.call(
                            "historian_renew",
                            {"session_id": session_id},
                            timeout=30,
                        )
                        if not renewed.get("renewed"):
                            log.warning(
                                "Magic Context historian lease was lost for %s",
                                session_id,
                            )
                            return
                    except Exception:
                        log.warning(
                            "Magic Context historian lease renewal failed for %s",
                            session_id,
                            exc_info=True,
                        )
                        return

            lease_thread = threading.Thread(
                target=renew_lease,
                name=f"magic-context-historian-lease-{session_id[:12]}",
                daemon=True,
            )
            lease_thread.start()

            historian_timeout = max(
                1.0, float(prepared.get("timeout_ms", 120_000)) / 1000
            )
            output = self._complete(
                system_prompt=prepared["system_prompt"],
                prompt=prepared["prompt"],
                task="mc_historian",
                model=prepared.get("model", ""),
                max_tokens=8192,
                timeout=historian_timeout,
            )
            published = client.call(
                "historian_publish",
                {"session_id": session_id, "output": output},
                timeout=60,
            )

            if not published.get("ok") and published.get("repair_prompt"):
                repaired = self._complete(
                    system_prompt=published["system_prompt"],
                    prompt=published["repair_prompt"],
                    task="mc_historian",
                    model=prepared.get("model", ""),
                    max_tokens=8192,
                    timeout=historian_timeout,
                )
                published = client.call(
                    "historian_publish",
                    {"session_id": session_id, "output": repaired},
                    timeout=60,
                )

            if published.get("needs_editor"):
                edited = self._complete(
                    system_prompt=published["editor_system_prompt"],
                    prompt=published["editor_prompt"],
                    task="mc_historian",
                    model=prepared.get("model", ""),
                    max_tokens=8192,
                    timeout=historian_timeout,
                )
                published = client.call(
                    "historian_publish",
                    {
                        "session_id": session_id,
                        "output": edited,
                        "editor_pass": True,
                    },
                    timeout=60,
                )

            compacted = published.get("messages")
            if published.get("ok"):
                published_ok = True
                with self._historian_lock:
                    self.compression_count += 1
                if isinstance(compacted, list) and compacted:
                    return compacted
                return None
            log.warning(
                "Magic Context historian output was rejected: %s",
                published.get("error", "unknown validation error"),
            )
        except Exception:
            log.warning(
                "Magic Context historian pass failed open; transcript is unchanged",
                exc_info=True,
            )
        finally:
            # Stop lease heartbeats before publishing/abort cleanup tears down
            # the transaction.  Event.wait() makes the normal path wake
            # immediately instead of leaving a daemon thread behind for up to
            # the full 60-second renewal interval.
            lease_stop.set()
            if (
                lease_thread is not None
                and lease_thread.is_alive()
                and lease_thread is not threading.current_thread()
            ):
                lease_thread.join(timeout=1.0)
            if prepared_started and not published_ok:
                try:
                    client.call(
                        "historian_abort",
                        {"session_id": session_id},
                        timeout=30,
                    )
                except Exception:
                    log.debug("Magic Context historian abort failed", exc_info=True)
        return None

    def _background_historian(
        self,
        client: RuntimeClient,
        *,
        session_id: str,
        project_root: str,
        messages: list[dict[str, Any]],
        boundary_snapshot: dict[str, Any] | None,
    ) -> None:
        try:
            self._run_historian_pass(
                client,
                session_id=session_id,
                project_root=project_root,
                messages=messages,
                boundary_snapshot=boundary_snapshot,
            )
            try:
                client.call(
                    "maintenance_run",
                    {"session_id": session_id},
                    timeout=120,
                )
            except RuntimeErrorBase:
                log.debug(
                    "Magic Context post-historian maintenance failed",
                    exc_info=True,
                )
        finally:
            client.close()
            current = threading.current_thread()
            with self._historian_lock:
                if self._historian_thread is current:
                    self._historian_thread = None

    def _schedule_historian(
        self,
        messages: list[dict[str, Any]],
        boundary_snapshot: dict[str, Any] | None,
    ) -> bool:
        if self._complete is None:
            return False
        with self._historian_lock:
            if self._historian_thread is not None and self._historian_thread.is_alive():
                return False
            client = copy.deepcopy(self._client)
            worker = threading.Thread(
                target=self._background_historian,
                kwargs={
                    "client": client,
                    "session_id": self._session_id,
                    "project_root": self._project_root,
                    "messages": copy.deepcopy(messages),
                    "boundary_snapshot": copy.deepcopy(boundary_snapshot),
                },
                name=f"magic-context-historian-{self._session_id[:12]}",
                daemon=True,
            )
            self._historian_thread = worker
            worker.start()
        return True

    def _background_maintenance(
        self,
        client: RuntimeClient,
        *,
        session_id: str,
        project_root: str,
    ) -> None:
        try:
            client.call(
                "bind",
                {"session_id": session_id, "project_root": project_root},
                timeout=60,
            )
            client.call(
                "maintenance_run",
                {"session_id": session_id},
                timeout=120,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context background maintenance failed", exc_info=True)
        finally:
            client.close()
            current = threading.current_thread()
            with self._maintenance_lock:
                if self._maintenance_thread is current:
                    self._maintenance_thread = None

    def _schedule_maintenance(self) -> bool:
        with self._maintenance_lock:
            if (
                self._maintenance_thread is not None
                and self._maintenance_thread.is_alive()
            ):
                return False
            client = copy.deepcopy(self._client)
            worker = threading.Thread(
                target=self._background_maintenance,
                kwargs={
                    "client": client,
                    "session_id": self._session_id,
                    "project_root": self._project_root,
                },
                name=f"magic-context-maintenance-{self._session_id[:12]}",
                daemon=True,
            )
            self._maintenance_thread = worker
            worker.start()
        return True

    def _background_dreamer(
        self,
        client: RuntimeClient,
        *,
        session_id: str,
        project_root: str,
    ) -> None:
        try:
            client.call(
                "bind",
                {"session_id": session_id, "project_root": project_root},
                timeout=60,
            )
            result = client.call(
                "dreamer_run_due",
                {"session_id": session_id},
                timeout=60 * 60,
            )
            if result.get("ran"):
                log.debug(
                    "Magic Context Dreamer ran %s due task(s) for %s",
                    result.get("ran"),
                    session_id,
                )
        except RuntimeErrorBase:
            log.debug("Magic Context background Dreamer failed", exc_info=True)
        finally:
            client.close()
            current = threading.current_thread()
            with self._dreamer_lock:
                if self._dreamer_thread is current:
                    self._dreamer_thread = None

    def _schedule_dreamer(self) -> bool:
        with self._dreamer_lock:
            if self._dreamer_thread is not None and self._dreamer_thread.is_alive():
                return False
            client = copy.deepcopy(self._client)
            copied_context = contextvars.copy_context()
            worker = threading.Thread(
                target=lambda: copied_context.run(
                    self._background_dreamer,
                    client,
                    session_id=self._session_id,
                    project_root=self._project_root,
                ),
                name=f"magic-context-dreamer-{self._session_id[:12]}",
                daemon=True,
            )
            self._dreamer_thread = worker
            worker.start()
        return True

    def _render_current_context(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        try:
            result = self._client.call(
                "render_context",
                {
                    "session_id": self._session_id,
                    "messages": messages,
                    "history_budget_tokens": self._history_budget_tokens(),
                },
                timeout=60,
            )
        except RuntimeErrorBase:
            return messages
        selected = result.get("messages")
        return selected if isinstance(selected, list) and selected else messages

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        """Hermes compatibility seam for manual or emergency synchronous work."""

        del focus_topic, memory_context
        if not messages or self._complete is None or not self._bind():
            return messages
        if current_tokens is not None and current_tokens > 0:
            self.last_prompt_tokens = int(current_tokens)

        with self._historian_lock:
            running = self._historian_thread
        if running is not None and running.is_alive():
            running.join()
            rendered = self._render_current_context(messages)
            if rendered != messages:
                return rendered

        boundary: dict[str, Any] | None = None
        try:
            decision = self._client.call(
                "historian_decide",
                {"session_id": self._session_id, "messages": messages},
                timeout=60,
            )
            if decision.get("should_fire"):
                candidate = decision.get("boundary_snapshot")
                if isinstance(candidate, dict):
                    boundary = candidate
        except RuntimeErrorBase:
            if not force:
                return messages

        worker_client = copy.deepcopy(self._client)
        try:
            self._run_historian_pass(
                worker_client,
                session_id=self._session_id,
                project_root=self._project_root,
                messages=copy.deepcopy(messages),
                boundary_snapshot=boundary,
            )
        finally:
            worker_client.close()
        return self._render_current_context(messages)

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
        if not isinstance(selected, list):
            return None
        # Magic Context may have tagged or reduced the live tail even when no
        # historian compartments exist yet. Preserve Hermes' no-op/cache path
        # only when the upstream renderer returned an identical request.
        return None if selected == request_messages else selected

    def on_turn_complete(
        self,
        messages: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del kwargs
        if not messages or not self._bind():
            return
        # update_from_response() is the normal Hermes rail, but preserve correct
        # scheduling when a host surface supplies usage only to this hook.
        if usage and not self.last_prompt_tokens:
            self.update_from_response(usage)
        try:
            self._client.call(
                "observe",
                {"session_id": self._session_id, "messages": messages},
                timeout=30,
            )
            decision = self._client.call(
                "historian_decide",
                {"session_id": self._session_id, "messages": messages},
                timeout=60,
            )
        except RuntimeErrorBase:
            log.debug("Magic Context turn observation/scheduling failed", exc_info=True)
            return

        historian_scheduled = False
        if decision.get("should_fire"):
            boundary = decision.get("boundary_snapshot")
            if boundary is not None and not isinstance(boundary, dict):
                boundary = None
            historian_scheduled = self._schedule_historian(messages, boundary)
            if historian_scheduled:
                log.debug(
                    "Magic Context historian scheduled for session %s (%s)",
                    self._session_id,
                    decision.get("reason", "upstream-trigger"),
                )

        # A historian worker runs maintenance after publish so new compartments
        # are eligible for embedding immediately. Otherwise run the same
        # upstream Git/embedding maintenance in its own background worker.
        if not historian_scheduled:
            self._schedule_maintenance()

        # Dreamer due-times/gates/leases/retries remain entirely upstream. The
        # copied ContextVar preserves Hermes' public active-parent capability for
        # any due task that needs a real tool-using child agent.
        self._schedule_dreamer()

    def on_session_start(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = str(session_id)
        root = kwargs.get("project_root") or kwargs.get("cwd")
        self._project_root = (
            str(Path(root).resolve()) if root else _resolve_host_project_root()
        )
        self._bound_identity = None
        if self._session_route is not None:
            self._session_route(self._session_id, self._project_root)
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

        # Upstream Pi gives in-flight historian/dreamer work a bounded 5-second
        # shutdown drain. Match that behavior so short-lived Hermes CLI
        # processes do not instantly kill a just-scheduled daemon worker while
        # also never wedging session shutdown on a slow auxiliary model.
        with self._historian_lock:
            historian = self._historian_thread
        if (
            historian is not None
            and historian.is_alive()
            and historian is not threading.current_thread()
        ):
            historian.join(timeout=5.0)

        with self._maintenance_lock:
            maintenance = self._maintenance_thread
        if (
            maintenance is not None
            and maintenance.is_alive()
            and maintenance is not threading.current_thread()
        ):
            maintenance.join(timeout=5.0)

        with self._dreamer_lock:
            dreamer = self._dreamer_thread
        if (
            dreamer is not None
            and dreamer.is_alive()
            and dreamer is not threading.current_thread()
        ):
            dreamer.join(timeout=5.0)

        if messages and self._bind():
            try:
                self._client.call(
                    "observe",
                    {"session_id": self._session_id, "messages": messages},
                    timeout=30,
                )
            except Exception:
                log.debug("Magic Context session finalization failed", exc_info=True)
        self._client.close()
        self._bound_identity = None
        if self._session_route is not None:
            self._session_route(self._session_id, None)

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


__all__ = ["MagicContextEngine"]
