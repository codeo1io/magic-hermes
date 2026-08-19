"""Hermes plugin entry point for the Magic Context connector."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .engine import MagicContextEngine
from .jsonc import load_jsonc
from .runtime import RuntimeClient, runtime_available, runtime_unavailable_reason

log = logging.getLogger(__name__)

PLUGIN_API_VERSION = "0.2"


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

    root = str(Path(project_root or Path.cwd()).resolve())
    shared_config = load_jsonc()
    historian_value = shared_config.get("historian")
    dreamer_value = shared_config.get("dreamer")
    historian = historian_value if isinstance(historian_value, dict) else {}
    dreamer = dreamer_value if isinstance(dreamer_value, dict) else {}
    historian_ref = str(historian.get("model") or "")
    historian_provider, historian_model = _route_for_hermes(historian_ref)
    dreamer_provider, dreamer_model = _route_for_hermes(
        str(dreamer.get("model") or historian_ref)
    )

    _register_auxiliary(
        ctx,
        "mc_historian",
        display_name="Magic Context historian",
        description="Compartmentalize older session history.",
        defaults={
            "provider": historian_provider,
            "model": historian_model,
            "timeout": _seconds_from_ms(
                shared_config.get("historian_timeout_ms"),
                default_ms=300_000,
            ),
        },
    )
    _register_auxiliary(
        ctx,
        "mc_dreamer",
        display_name="Magic Context dreamer",
        description="Curate durable Magic Context memories.",
        defaults={
            "provider": dreamer_provider,
            "model": dreamer_model,
            "timeout": 120,
        },
    )

    completion = _completion_callback(ctx)
    engine = MagicContextEngine(
        client=RuntimeClient(),
        complete=completion,
        project_root=root,
        session_id=session_id,
    )
    register_engine = getattr(ctx, "register_context_engine", None)
    if not callable(register_engine):
        raise RuntimeError("Hermes plugin context has no register_context_engine API")
    handle = register_engine(engine)
    return {
        "enabled": True,
        "engine": engine,
        "registration": handle,
        "auxiliary": ["mc_historian", "mc_dreamer"],
    }


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


def _seconds_from_ms(value: Any, *, default_ms: float) -> float:
    try:
        milliseconds = float(value)
    except (TypeError, ValueError):
        milliseconds = default_ms
    if milliseconds <= 0:
        milliseconds = default_ms
    return milliseconds / 1000


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
        del model
        result = complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            task=task,
            max_tokens=max_tokens,
            timeout=timeout,
            purpose=f"magic-context.{task}",
        )
        text = str(getattr(result, "text", "") or "")
        if not text.strip():
            raise RuntimeError(f"Hermes auxiliary task {task} returned no text")
        return text

    return run


__all__ = ["PLUGIN_API_VERSION", "load", "register"]
