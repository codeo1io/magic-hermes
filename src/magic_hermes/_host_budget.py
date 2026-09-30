"""Cooperative host-budget awareness for Hermes-owned synchronous compression.

Hermes runs a plugin context engine's ``compress()`` on a pooled worker thread
under a progress-aware timeout: an inactivity budget that only resets when the
attempt reports forward progress, an absolute wall-clock deadline, and a
cancellation signal. All three reach the engine through thread-local seams in
``agent.auxiliary_client`` that the host installs around the ``compress()``
dispatch (``aux_progress_hook``, ``aux_stream_deadline``,
``aux_interrupt_protection``).

Without cooperation, a connector-side compression pass whose legs are Node
round-trips and auxiliary model calls looks like a stalled summary to the host:
no progress is reported while the pass is legitimately working, the host times
out, poisons the commit fence, and the still-oversized request fails closed
("Context compression timed out without reducing this conversation").

This adapter reads those seams defensively. Outside a Hermes compression
attempt — unit tests, background workers, non-Hermes hosts — every function
is inert, so the connector's standalone behavior is unchanged.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

__all__ = [
    "HostBudgetExceeded",
    "checkpoint",
    "clamp_call_timeout",
    "remaining_seconds",
    "tick",
    "wait_for_worker",
]


class HostBudgetExceeded(RuntimeError):
    """The waiting Hermes host stopped waiting (cancelled or budget spent)."""


#: Never start another leg when less than this remains: every leg needs at
#: least one Node round-trip (or a model call) to be worth beginning.
_MIN_LEG_SECONDS = 10.0
#: Floor for a clamped call timeout so a tiny remaining budget still yields a
#: well-formed round-trip instead of a zero-second wait.
_MIN_CALL_SECONDS = 1.0
#: Poll granularity for bounded background-worker joins.
_JOIN_POLL_SECONDS = 1.0

# Lazily-resolved host module; ``None`` on hosts without hermes-agent.
_auxiliary: Any = None
_auxiliary_resolved = False


def _module() -> Any:
    global _auxiliary, _auxiliary_resolved
    if _auxiliary is None and not _auxiliary_resolved:
        _auxiliary_resolved = True
        with contextlib.suppress(Exception):
            from agent import (
                auxiliary_client as module,  # type: ignore[import-not-found]
            )

            _auxiliary = module
    return _auxiliary


def _deadline() -> float | None:
    """Absolute ``time.monotonic()`` deadline installed by the waiting host."""
    module = _module()
    getter = getattr(module, "_current_aux_stream_deadline", None)
    if not callable(getter):
        return None
    with contextlib.suppress(Exception):
        value = getter()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def remaining_seconds() -> float | None:
    """Seconds until the host deadline; ``None`` when no deadline applies."""
    deadline = _deadline()
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _cancel_requested() -> bool:
    """Whether the waiting host explicitly cancelled this attempt."""
    module = _module()
    capture = getattr(module, "_capture_aux_cancel_check", None)
    if not callable(capture):
        return False
    with contextlib.suppress(Exception):
        check = capture()
        if callable(check):
            return bool(check())
    return False


def tick() -> None:
    """Report forward progress (a completed leg) to the waiting host."""
    module = _module()
    notify = getattr(module, "_notify_aux_progress", None)
    if callable(notify):
        with contextlib.suppress(Exception):
            notify()


def clamp_call_timeout(nominal: float) -> float:
    """Clamp a call timeout to the host's remaining budget (identity without one)."""
    remaining = remaining_seconds()
    if remaining is None:
        return float(nominal)
    return max(_MIN_CALL_SECONDS, min(float(nominal), remaining))


def checkpoint() -> None:
    """Raise ``HostBudgetExceeded`` unless the host is still waiting with budget.

    Call before starting each leg of a synchronous compression pass. Cancellation
    and an expired deadline always stop the pass; a remaining budget under
    :data:`_MIN_LEG_SECONDS` does too, so a leg is never started just to be
    torn down mid-flight.
    """
    if _cancel_requested():
        raise HostBudgetExceeded("host compression was cancelled")
    remaining = remaining_seconds()
    if remaining is None:
        return
    if remaining <= 0:
        raise HostBudgetExceeded("host compression deadline reached")
    if remaining < _MIN_LEG_SECONDS:
        raise HostBudgetExceeded(
            f"host compression budget nearly exhausted ({remaining:.1f}s left)"
        )


def wait_for_worker(worker: threading.Thread) -> bool:
    """Wait for a background worker within the host budget.

    ``True`` when the worker finished; ``False`` when the host budget ran out
    or the host cancelled while it was still running. Without a host deadline
    this keeps the historical unbounded join.
    """
    deadline = _deadline()
    if deadline is None:
        worker.join()
        return True
    while worker.is_alive():
        if _cancel_requested():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        worker.join(timeout=min(_JOIN_POLL_SECONDS, remaining))
    return True
