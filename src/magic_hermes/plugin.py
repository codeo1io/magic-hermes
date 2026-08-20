"""Hermes plugin entry point for the Magic Context connector."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .engine import MagicContextEngine
from .runtime import RuntimeClient, runtime_available, runtime_unavailable_reason

log = logging.getLogger(__name__)

PLUGIN_API_VERSION = "0.2"


class _DreamerHostBridge:
    """Translate upstream Dreamer child-session requests to public Hermes children.

    Magic Context retains task scheduling, leases, prompts, validation, retries,
    and persistence. This bridge supplies only the host execution primitive.
    """

    def __init__(self, ctx) -> None:
        lifecycle = getattr(ctx, "subagent_lifecycle", None)
        if lifecycle is None:
            raise RuntimeError("Hermes plugin context has no subagent lifecycle API")
        self._lifecycle = lifecycle
        self._lock = threading.RLock()
        self._trace_ready = threading.Condition(self._lock)
        self._stop_events: list[dict[str, Any]] = []
        self._handles: dict[str, Any] = {}
        self._launch_local = threading.local()
        self._child_projects: dict[str, str] = {}
        self._child_capabilities: dict[str, str] = {}
        self._tool_clients: dict[str, RuntimeClient] = {}

        register_hook = getattr(ctx, "register_hook", None)
        if not callable(register_hook):
            raise RuntimeError("Hermes plugin context has no lifecycle hook API")
        self._hook_registrations = [
            register_hook("subagent_start", self._on_subagent_start),
            register_hook("subagent_stop", self._on_subagent_stop),
            register_hook("pre_tool_call", self._on_pre_tool_call),
        ]

    def route_session(self, session_id: str, project_root: str | None) -> None:
        client = None
        with self._lock:
            if project_root is None:
                self._child_projects.pop(session_id, None)
                client = self._tool_clients.pop(session_id, None)
            else:
                self._child_projects[session_id] = str(project_root)
        if client is not None:
            client.close()

    def _on_subagent_start(self, **payload: Any) -> None:
        project_root = getattr(self._launch_local, "project_root", None)
        capability = getattr(self._launch_local, "capability", "memory")
        child_session_id = str(payload.get("child_session_id") or "")
        if not project_root or not child_session_id:
            return
        with self._lock:
            self._child_projects[child_session_id] = str(project_root)
            self._child_capabilities[child_session_id] = str(capability)

    def _on_pre_tool_call(self, **payload: Any) -> dict[str, str] | None:
        session_id = str(payload.get("session_id") or "")
        tool_name = str(payload.get("tool_name") or "")
        with self._lock:
            capability = self._child_capabilities.get(session_id)
        if not capability:
            return None

        if capability == "docs":
            return None
        if capability == "read_only":
            if tool_name in {"write_file", "patch", "terminal", "process"}:
                return {
                    "action": "block",
                    "message": "This Magic Context Dreamer task is read-only.",
                }
            return None
        if capability == "model_only":
            return {
                "action": "block",
                "message": (
                    "This Magic Context Dreamer task is model-only and has no tools."
                ),
            }
        if tool_name in {
            "read_file",
            "write_file",
            "patch",
            "search_files",
            "terminal",
            "process",
        }:
            return {
                "action": "block",
                "message": (
                    "Magic Context memory Dreamer tasks may use only their "
                    "ctx_* tools through Hermes tool_search/tool_call."
                ),
            }
        return None

    def _on_subagent_stop(self, **payload: Any) -> None:
        child_session_id = str(payload.get("child_session_id") or "")
        client = None
        with self._lock:
            if child_session_id:
                self._child_projects.pop(child_session_id, None)
                self._child_capabilities.pop(child_session_id, None)
                client = self._tool_clients.pop(child_session_id, None)
        if client is not None:
            client.close()
        with self._trace_ready:
            self._stop_events.append(
                {
                    "at": time.monotonic(),
                    "parent_session_id": payload.get("parent_session_id"),
                    "child_session_id": payload.get("child_session_id"),
                    "summary": str(payload.get("child_summary") or ""),
                    "status": str(payload.get("child_status") or ""),
                    "tool_call_history": list(payload.get("tool_call_history") or []),
                }
            )
            # The list is correlation scratch only; keep it bounded for gateways.
            if len(self._stop_events) > 128:
                del self._stop_events[:-128]
            self._trace_ready.notify_all()

    @staticmethod
    def _request_text(system_prompt: str, prompt: str) -> tuple[str, str]:
        """Fit canonical MC instructions into Hermes' public child contract.

        Hermes may defer plugin tools behind its public progressive-disclosure
        bridge. This host-only shim explains how to execute an MC-named tool;
        it does not alter Magic Context task policy, prompts, or persistence.
        """

        system_prompt = str(system_prompt or "")
        prompt = str(prompt or "")
        host_tool_bridge = (
            "\n\n<Hermes-host-tool-bridge>\n"
            "Magic Context tools such as ctx_memory and ctx_search may be "
            "deferred behind Hermes tool_search/tool_describe/tool_call. When "
            "the Magic Context instructions require one of those tools, use "
            "tool_search to locate it, tool_describe if you need its schema, "
            "then tool_call with name=the ctx_* tool and its arguments. A prose "
            "or code-block imitation of a ctx_* call does NOT execute it and is "
            "not an acceptable substitute.\n"
            "</Hermes-host-tool-bridge>"
        )
        system_prompt += host_tool_bridge
        if len(prompt) <= 16_000 and len(system_prompt) <= 32_000:
            return prompt, system_prompt

        combined = (
            "Magic Context system instructions:\n"
            + system_prompt
            + "\n\nMagic Context task prompt:\n"
            + prompt
        )
        if len(combined) > 48_000:
            raise RuntimeError(
                "Magic Context Dreamer prompt exceeds Hermes' public subagent "
                "goal/context contract (48,000 characters combined)"
            )
        context = combined[:32_000]
        remainder = combined[32_000:]
        goal = remainder or "Execute the Magic Context task in the supplied context."
        return goal, context

    def _matching_trace(
        self,
        *,
        started_at: float,
        parent_session_id: str,
        summary: str,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + 1.0
        with self._trace_ready:
            while True:
                for index, event in enumerate(self._stop_events):
                    if event["at"] < started_at:
                        continue
                    if event["parent_session_id"] != parent_session_id:
                        continue
                    if summary and event["summary"] != summary:
                        continue
                    matched = self._stop_events.pop(index)
                    return list(matched["tool_call_history"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._trace_ready.wait(remaining)

    def tool_handler(self, name: str):
        """Return a registry handler for a Dreamer-scoped upstream ctx_* tool."""

        def run(args: dict[str, Any], **kwargs: Any) -> str:
            child_session_id = str(kwargs.get("session_id") or "")
            if not child_session_id:
                raise RuntimeError("Magic Context Dreamer tool call has no session id")
            with self._lock:
                capability = self._child_capabilities.get(child_session_id)
                project_root = self._child_projects.get(child_session_id)
            if capability is None:
                raise RuntimeError(
                    "Magic Context registry bridge tools are reserved for "
                    "Magic Context-owned Dreamer children; root ctx_* tools "
                    "require the context_engine rail"
                )
            with self._lock:
                client = self._tool_clients.get(child_session_id)
                if project_root and client is None:
                    client = RuntimeClient(timeout=60)
                    self._tool_clients[child_session_id] = client
            if not project_root or client is None:
                raise RuntimeError(
                    "Magic Context Dreamer tool is not bound to an active child project"
                )
            client.call(
                "bind",
                {
                    "session_id": child_session_id,
                    "project_root": project_root,
                },
                timeout=60,
            )
            result = client.call(
                "tool",
                {
                    "session_id": child_session_id,
                    "name": name,
                    "arguments": args,
                    "messages": [],
                    "call_id": f"magic-hermes-dreamer-{name}",
                },
                timeout=60,
            )
            if result.get("is_error"):
                raise RuntimeError(str(result.get("text") or f"{name} failed"))
            return str(result.get("text") or "")

        return run

    @staticmethod
    def _capability_for_task(params: dict[str, Any]) -> str:
        title = str(params.get("title") or "").lower()
        agent = str(params.get("agent") or "").lower()
        if "maintain-docs" in title or "docs" in agent:
            return "docs"
        if any(
            marker in title
            for marker in ("map-memories", "dream-verify", "refresh-primers")
        ) or "primer-investigator" in agent:
            return "read_only"
        if any(
            marker in title
            for marker in (
                "dream-classify",
                "compress-cues",
                "dream-user-memories",
                "smart-note-compile",
                "smart-note-confirm",
            )
        ) or agent in {"smart-note-compiler"}:
            return "model_only"
        # Curate and retrospective may use the MC-owned ctx_* surface but must
        # not bypass memory policy by reading/writing project files directly.
        return "memory"

    @classmethod
    def _toolsets_for_task(cls, params: dict[str, Any]) -> tuple[str, ...]:
        capability = cls._capability_for_task(params)
        if capability == "docs":
            return ("file", "terminal")
        # `file` is a non-empty static Hermes toolset that survives delegated
        # child safety filtering. For memory tasks its direct file tools are
        # vetoed by pre_tool_call; the only usable additions are the deferred
        # upstream ctx_* registry tools exposed via tool_search/tool_call.
        return ("file",)

    def _run_child(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            from agent.subagent_lifecycle import SubagentLaunchRequest, SubagentState
        except ImportError as exc:  # pragma: no cover - Hermes runtime required.
            raise RuntimeError(
                "Hermes subagent lifecycle contracts are unavailable"
            ) from exc

        virtual_session_id = str(params.get("virtual_session_id") or uuid.uuid4())
        goal, context = self._request_text(
            str(params.get("system") or ""),
            str(params.get("prompt") or ""),
        )
        model = _model_for_hermes(str(params.get("model") or "")) or None
        started_at = time.monotonic()
        self._launch_local.project_root = str(params.get("directory") or "")
        self._launch_local.capability = self._capability_for_task(params)
        try:
            handle = self._lifecycle.launch(
                SubagentLaunchRequest(
                    goal=goal,
                    context=context or None,
                    role="leaf",
                    model=model,
                    allowed_toolsets=self._toolsets_for_task(params),
                    correlation_id=virtual_session_id,
                    metadata={
                        "owner": "magic-context",
                        "task": str(params.get("agent") or "dreamer"),
                        "virtual_session_id": virtual_session_id,
                    },
                )
            )
        finally:
            self._launch_local.project_root = None
            self._launch_local.capability = None
        # Key the live child by the upstream virtual-session id immediately.
        # This makes cancellation addressable before the long prompt callback
        # returns its final result.
        token = virtual_session_id
        with self._lock:
            self._handles[token] = handle

        try:
            terminal = self._lifecycle.wait(handle, timeout_seconds=20 * 60)
            if not terminal.completed:
                self._lifecycle.cancel(handle, reason="Magic Context Dreamer timeout")
                raise RuntimeError("Hermes Dreamer child timed out")
            result = self._lifecycle.result(handle)
        finally:
            with self._lock:
                self._handles.pop(token, None)

        if result.terminal_state != SubagentState.SUCCEEDED or not result.ready:
            detail = (
                result.error_message
                or result.error_classification
                or result.terminal_state
            )
            raise RuntimeError(f"Hermes Dreamer child failed: {detail}")
        summary = str(result.summary or "")
        if not summary.strip():
            raise RuntimeError("Hermes Dreamer child returned no final response")
        tool_history = self._matching_trace(
            started_at=started_at,
            parent_session_id=str(result.handle.parent_session_id or ""),
            summary=summary,
        )
        return {
            "text": summary,
            "handle": token,
            "tool_history": tool_history,
            "model": result.handle.model,
        }

    def _abort_child(self, params: dict[str, Any]) -> dict[str, Any]:
        token = str(
            params.get("handle")
            or params.get("virtual_session_id")
            or ""
        )
        with self._lock:
            handle = self._handles.get(token)
        if handle is None:
            return {"accepted": False, "reason": "unknown-or-terminal"}
        cancelled = self._lifecycle.cancel(
            handle,
            reason=str(params.get("reason") or "Magic Context Dreamer abort"),
        )
        return {"accepted": bool(cancelled.accepted)}

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "dreamer_child_prompt":
            return self._run_child(params)
        if method == "dreamer_child_abort":
            return self._abort_child(params)
        raise RuntimeError(f"Unknown Magic Context host callback: {method}")


def register(ctx):
    """Register the context engine and its host-routed auxiliary LLM tasks."""

    return load(ctx)


def load(
    ctx,
    *,
    project_root: str | os.PathLike[str] | None = None,
    session_id: str | None = None,
):
    """Compose Magic Context onto Hermes' native plugin surfaces."""

    if not runtime_available():
        reason = runtime_unavailable_reason()
        log.warning("magic-hermes is disabled: %s", reason)
        return {"enabled": False, "reason": reason}

    root = str(Path(project_root).resolve()) if project_root is not None else None

    # Magic Context configuration is resolved only by the upstream runtime.
    # These task registrations merely declare Hermes-owned execution slots;
    # the effective model/provider is supplied per call from the project-aware
    # upstream Magic Context resolver.
    _register_auxiliary(
        ctx,
        "mc_historian",
        display_name="Magic Context historian",
        description="Compartmentalize older session history.",
        defaults={"provider": "auto", "model": "", "timeout": 300},
    )
    completion = _completion_callback(ctx)
    dreamer_bridge = _DreamerHostBridge(ctx)
    _register_dreamer_tools(ctx, dreamer_bridge)
    engine = MagicContextEngine(
        client=RuntimeClient(callback_handler=dreamer_bridge.handle),
        complete=completion,
        project_root=root,
        session_id=session_id,
        session_route=dreamer_bridge.route_session,
    )
    register_engine = getattr(ctx, "register_context_engine", None)
    if not callable(register_engine):
        raise RuntimeError("Hermes plugin context has no register_context_engine API")
    handle = register_engine(engine)
    return {
        "enabled": True,
        "engine": engine,
        "registration": handle,
        "auxiliary": ["mc_historian"],
    }


def _register_dreamer_tools(ctx, bridge: _DreamerHostBridge) -> None:
    register_tool = getattr(ctx, "register_tool", None)
    if not callable(register_tool):
        raise RuntimeError("Hermes plugin context has no register_tool API")
    with RuntimeClient(timeout=30) as client:
        schemas = client.call("dreamer_tool_schemas", timeout=30)
    if not isinstance(schemas, list):
        raise RuntimeError("Magic Context returned invalid Dreamer tool schemas")
    for schema in schemas:
        if not isinstance(schema, dict) or not schema.get("name"):
            raise RuntimeError("Magic Context returned a malformed Dreamer tool schema")
        name = str(schema["name"])
        register_tool(
            name=name,
            toolset="file",
            schema=schema,
            handler=bridge.tool_handler(name),
            description=str(schema.get("description") or ""),
        )


def _register_auxiliary(
    ctx,
    key: str,
    *,
    display_name: str,
    description: str,
    defaults: dict[str, Any],
) -> None:
    register_task = getattr(ctx, "register_auxiliary_task", None)
    if not callable(register_task):
        raise RuntimeError("Hermes plugin context has no auxiliary task API")
    register_task(
        key,
        display_name=display_name,
        description=description,
        defaults=defaults,
    )


def _model_for_hermes(model: str) -> str:
    """Adapt CortexKit's Z.AI-qualified ref to Hermes' active-provider route."""

    if model.startswith("zai/"):
        return model.split("/", 1)[1]
    if model.startswith("openai/"):
        return model.split("/", 1)[1]
    return model


def _route_for_hermes(model: str) -> tuple[str, str]:
    """Return Hermes auxiliary provider/model defaults for a CortexKit ref."""

    model = model.strip()
    normalized = _model_for_hermes(model)
    if not normalized:
        return "auto", ""
    if model.startswith("openai/") or normalized.startswith("gpt-"):
        return "openai", normalized
    return "auto", normalized


def _completion_callback(ctx):
    llm = getattr(ctx, "llm", None)
    complete = getattr(llm, "complete", None)
    if not callable(complete):
        raise RuntimeError("Hermes plugin context has no ctx.llm.complete API")

    def run(
        *,
        system_prompt: str,
        prompt: str,
        task: str,
        model: str = "",
        max_tokens: int = 8192,
        timeout: float = 120,
    ) -> str:
        provider, routed_model = _route_for_hermes(model)
        routing: dict[str, str] = {}
        if provider and provider != "auto":
            routing["provider"] = provider
        if routed_model:
            routing["model"] = routed_model
        try:
            result = complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                task=task,
                max_tokens=max_tokens,
                timeout=timeout,
                purpose=f"magic-context.{task}",
                **routing,
            )
        except PermissionError as exc:
            if routing:
                raise RuntimeError(
                    "Hermes blocked the model/provider selected by the shared Magic "
                    "Context config. Allow model/provider overrides for the "
                    "magic-hermes plugin in plugins.entries.magic-hermes.llm."
                ) from exc
            raise
        text = str(getattr(result, "text", "") or "")
        if not text.strip():
            raise RuntimeError(f"Hermes auxiliary task {task} returned no text")
        return text

    return run


__all__ = ["PLUGIN_API_VERSION", "load", "register"]
