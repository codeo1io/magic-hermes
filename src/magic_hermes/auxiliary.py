"""U6: Historian and dreamer as hermes auxiliary tasks.

Mirrors pi-plugin's historian-runner/publish-signal behavior:

- The historian drains compaction signals published by the context
  engine (U3) and publishes compartment summaries back to the store.
- The dreamer runs on a cadence to mine persistent memories.
- Model routing is declared through hermes' ``register_auxiliary_task``
  (configurable via ``auxiliary.mc_historian`` / ``auxiliary.mc_dreamer``
  in config.yaml); the *default* model comes from the shared magic-context
  config (historian.model).
- Failure of a background pass never blocks a foreground turn: errors are
  logged, signals retained, and the next tick retries with backoff.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .jsonc import load_jsonc

log = logging.getLogger(__name__)

TASK_HISTORIAN = "mc_historian"
TASK_DREAMER = "mc_dreamer"

# Built-in auxiliary task keys in hermes we must not shadow (informational).
BUILTIN_AUXILIARY_TASKS = {
    "vision",
    "compression",
    "web_extract",
    "approval",
    "mcp",
    "title_generation",
    "skills_hub",
    "curator",
}

DEFAULT_HISTORIAN_MODEL = "zai/glm-4.7"
DEFAULT_DREAMER_MODEL = "zai/glm-4.7"
DREAMER_DEFAULT_INTERVAL_S = 15 * 60
HISTORIAN_MAX_ATTEMPTS = 3
HISTORIAN_BACKOFF_S = 5.0


@dataclass
class CompactionSignal:
    """A pending compaction event emitted by the context engine."""

    session_id: str
    ordinal_range: tuple[int, int]
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    last_error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.session_id}:{self.ordinal_range[0]}:{self.ordinal_range[1]}"


class SignalQueue:
    """In-memory queue of compaction signals awaiting historian passes."""

    def __init__(self) -> None:
        self._signals: dict[str, CompactionSignal] = {}
        self._lock = threading.Lock()

    def publish(self, signal: CompactionSignal) -> None:
        with self._lock:
            self._signals[signal.key] = signal

    def drain(self) -> list[CompactionSignal]:
        with self._lock:
            signals = list(self._signals.values())
            self._signals.clear()
        return signals

    def requeue(self, signal: CompactionSignal, error: str) -> None:
        signal.attempts += 1
        signal.last_error = error
        with self._lock:
            self._signals[signal.key] = signal

    def pending(self) -> int:
        with self._lock:
            return len(self._signals)


class HistorianRunner:
    """Drains compaction signals and publishes summaries to the store."""

    def __init__(
        self,
        queue: SignalQueue,
        call_model: Callable[[str, str, str], str],
        publish_summary: Callable[[str, tuple[int, int], str], Any],
        max_attempts: int = HISTORIAN_MAX_ATTEMPTS,
        backoff_s: float = HISTORIAN_BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._queue = queue
        self._call_model = call_model
        self._publish_summary = publish_summary
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._sleep = sleep

    def run_pass(self) -> int:
        """One pass: drain signals, summarize, publish. Returns published count."""
        published = 0
        for signal in self._queue.drain():
            prompt = (
                "Summarize the conversation segment "
                f"[{signal.ordinal_range[0]}, {signal.ordinal_range[1]}] "
                f"of session {signal.session_id} for later recall."
            )
            try:
                summary = self._call_model(prompt, signal.session_id, "historian")
            except Exception as err:  # noqa: BLE001 - background task must not raise
                log.warning("historian pass failed for %s: %s", signal.key, err)
                if signal.attempts + 1 < self._max_attempts:
                    self._sleep(self._backoff_s * (signal.attempts + 1))
                    self._queue.requeue(signal, str(err))
                else:
                    log.error(
                        "dropping compaction signal %s after %d attempts: %s",
                        signal.key,
                        self._max_attempts,
                        signal.last_error or err,
                    )
                continue
            try:
                self._publish_summary(signal.session_id, signal.ordinal_range, summary)
                published += 1
            except Exception as err:  # noqa: BLE001
                log.warning(
                    "historian publish failed for %s: %s (will retry)", signal.key, err
                )
                self._queue.requeue(signal, f"publish: {err}")
        return published


class DreamerRunner:
    """Periodic memory-mining pass over recent conversation history."""

    def __init__(
        self,
        call_model: Callable[[str, str, str], str],
        publish_memory: Callable[[str, str], Any],
        interval_s: int = DREAMER_DEFAULT_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._call_model = call_model
        self._publish_memory = publish_memory
        self._interval_s = interval_s
        self._clock = clock
        self._last_run: float | None = None
        self._lock = threading.Lock()

    def due(self) -> bool:
        with self._lock:
            if self._last_run is None:
                return True
            return (self._clock() - self._last_run) >= self._interval_s

    def run_pass(self, transcript: str, session_id: str) -> bool:
        """Run one dream pass if due. Returns True when a pass ran."""
        with self._lock:
            last = self._last_run
        if last is not None and (self._clock() - last) < self._interval_s:
            return False
        with self._lock:
            self._last_run = self._clock()
        prompt = (
            "Mine the following session for durable project knowledge "
            "(rules, constraints, config values). Return one memory per line, "
            "or nothing if none.\n\n" + transcript
        )
        try:
            result = self._call_model(prompt, session_id, "dreamer")
        except Exception as err:  # noqa: BLE001
            log.warning("dreamer pass failed: %s (retry next cadence)", err)
            return True  # a pass ran; failure is contained
        for line in (ln.strip() for ln in result.splitlines()):
            if line:
                try:
                    self._publish_memory(session_id, line)
                except Exception as err:  # noqa: BLE001
                    log.warning("dreamer memory publish failed: %s", err)
        return True


def auxiliary_defaults_from_config(
    config: MagicContextConfig,
) -> dict[str, dict[str, Any]]:
    """Build hermes auxiliary-task ``defaults`` from the shared config."""
    historian_model = str(config.get("historian", {}).get("model", "")).strip()
    dreamer_model = str(config.get("dreamer", {}).get("model", "")).strip()
    return {
        TASK_HISTORIAN: {
            "provider": "auto",
            "model": historian_model or DEFAULT_HISTORIAN_MODEL,
            "timeout": 120,
        },
        TASK_DREAMER: {
            "provider": "auto",
            "model": dreamer_model or DEFAULT_DREAMER_MODEL,
            "timeout": 120,
        },
    }


def register(ctx: Any, config: dict[str, Any]) -> dict[str, SignalQueue]:
    """Register auxiliary tasks on a hermes PluginContext.

    Returns the live signal queue so the context engine can publish
    compaction signals into it.
    """
    defaults = auxiliary_defaults_from_config(config)
    ctx.register_auxiliary_task(
        key=TASK_HISTORIAN,
        display_name="Magic Context Historian",
        description="Background compartment summarization for magic-context",
        defaults=defaults[TASK_HISTORIAN],
    )
    ctx.register_auxiliary_task(
        key=TASK_DREAMER,
        display_name="Magic Context Dreamer",
        description="Periodic persistent-memory mining for magic-context",
        defaults=defaults[TASK_DREAMER],
    )
    queue = SignalQueue()
    return {TASK_HISTORIAN: queue}
