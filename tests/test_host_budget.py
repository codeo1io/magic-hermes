"""Host-budget cooperation: compression must honor Hermes' waiting budget.

The host runs engine.compress() on a pooled worker under a progress-aware
timeout and exposes its deadline/cancellation/progress seams as thread-locals
in agent.auxiliary_client. These tests install a fake of that module to
simulate the host seam and verify the engine cooperates instead of stalling.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from magic_hermes import _host_budget
from magic_hermes.engine import MagicContextEngine


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.closed = False

    def __deepcopy__(self, memo):
        copied = type(self)(self.responses)
        copied.calls = self.calls
        memo[id(self)] = copied
        return copied

    def call(self, method, params=None, *, timeout=None):
        self.calls.append((method, params or {}, timeout))
        value = self.responses.get(method)
        if callable(value):
            return value(params or {})
        return value or {}

    def close(self):
        self.closed = True


def bind_result():
    return {
        "config": {
            "enabled": True,
            "execute_threshold_percentage": 65,
            "history_budget_percentage": 0.15,
        },
        "tool_schemas": [],
    }


@pytest.fixture()
def fake_aux_module(monkeypatch):
    """Install a fake agent.auxiliary_client exposing the host seam."""

    module = types.ModuleType("agent.auxiliary_client")
    module.state = {
        "deadline": None,
        "cancelled": False,
        "ticks": 0,
    }

    def _current_aux_stream_deadline():
        return module.state["deadline"]

    def _notify_aux_progress():
        module.state["ticks"] += 1

    def _capture_aux_cancel_check():
        if module.state["cancelled"]:
            return lambda: True
        return None

    module._current_aux_stream_deadline = _current_aux_stream_deadline
    module._notify_aux_progress = _notify_aux_progress
    module._capture_aux_cancel_check = _capture_aux_cancel_check

    agent_module = types.ModuleType("agent")
    agent_module.auxiliary_client = module
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", module)
    # Reset the adapter's lazy import cache so it re-resolves against the fake.
    monkeypatch.setattr(_host_budget, "_auxiliary", None)
    monkeypatch.setattr(_host_budget, "_auxiliary_resolved", False)
    return module


def test_adapter_inert_without_hermes():
    # No `agent` module importable (or absent seam): everything is a no-op.
    _host_budget.tick()
    assert _host_budget.remaining_seconds() is None
    assert _host_budget.clamp_call_timeout(120.0) == 120.0
    _host_budget.checkpoint()  # must not raise


def test_checkpoint_raises_when_budget_spent(fake_aux_module):
    fake_aux_module.state["deadline"] = time.monotonic() - 1.0
    with pytest.raises(_host_budget.HostBudgetExceeded):
        _host_budget.checkpoint()


def test_checkpoint_raises_when_cancelled(fake_aux_module):
    fake_aux_module.state["deadline"] = time.monotonic() + 300.0
    fake_aux_module.state["cancelled"] = True
    with pytest.raises(_host_budget.HostBudgetExceeded):
        _host_budget.checkpoint()


def test_clamp_call_timeout_respects_remaining(fake_aux_module):
    fake_aux_module.state["deadline"] = time.monotonic() + 30.0
    assert _host_budget.clamp_call_timeout(120.0) <= 30.0
    assert _host_budget.clamp_call_timeout(5.0) == 5.0


def test_wait_for_worker_bounds_join(fake_aux_module):
    fake_aux_module.state["deadline"] = time.monotonic() + 1.5
    worker = threading.Thread(target=time.sleep, args=(30,), daemon=True)
    worker.start()
    started = time.monotonic()
    assert _host_budget.wait_for_worker(worker) is False
    assert time.monotonic() - started < 10
    # The thread is left running (daemon) — the host stopped waiting, exactly
    # the semantics the old unbounded join() violated.


def test_wait_for_worker_returns_when_worker_finishes(fake_aux_module):
    fake_aux_module.state["deadline"] = time.monotonic() + 30.0
    worker = threading.Thread(target=lambda: None)
    worker.start()
    assert _host_budget.wait_for_worker(worker) is True


def test_wait_for_worker_unbounded_without_host(fake_aux_module):
    fake_aux_module.state["deadline"] = None
    worker = threading.Thread(target=lambda: None)
    worker.start()
    # With no deadline the join is unbounded (historical behavior) and a
    # finished worker returns True.
    assert _host_budget.wait_for_worker(worker) is True


def historian_responses(compacted):
    """Standard historian call graph returning a successful publish."""
    return {
        "bind": bind_result(),
        "model_update": {},
        "historian_decide": {"should_fire": False},
        "historian_prepare": {
            "ready": True,
            "system_prompt": "# Historian",
            "prompt": "<new_messages>history</new_messages>",
            "model": "zai/glm-4.7",
            "timeout_ms": 1_000,
        },
        "historian_publish": {"ok": True, "messages": compacted},
        "render_context": {"messages": compacted},
    }


def test_historian_pass_ticks_progress_between_legs(fake_aux_module, tmp_path):
    compacted = [{"role": "system", "content": "c"}]
    client = FakeClient(historian_responses(compacted))
    engine = MagicContextEngine(
        client=client,
        complete=lambda **kwargs: "<output />",
        project_root=tmp_path,
        session_id="ticks",
    )
    assert engine._historian_lock is not None  # sanity: engine constructed
    result = engine._run_historian_pass(
        client,
        session_id="ticks",
        project_root=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result == compacted
    # Every completed leg reported forward progress to the waiting host.
    assert fake_aux_module.state["ticks"] >= 4


def test_historian_pass_unwinds_benign_on_budget_exhaustion(
    fake_aux_module, tmp_path
):
    compacted = [{"role": "system", "content": "c"}]
    responses = historian_responses(compacted)

    prepared_payload = responses["historian_prepare"]

    def prepare(params):
        # Host budget expires between checkpointed legs.
        fake_aux_module.state["deadline"] = time.monotonic() - 1.0
        return prepared_payload

    responses["historian_prepare"] = prepare
    client = FakeClient(responses)
    engine = MagicContextEngine(
        client=client,
        complete=lambda **kwargs: pytest.fail("model leg must not start"),
        project_root=tmp_path,
        session_id="budget",
    )
    result = engine._run_historian_pass(
        client,
        session_id="budget",
        project_root=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result is None
    aborts = [p for name, p, _ in client.calls if name == "historian_abort"]
    assert len(aborts) == 1
    # Benign classification: no failed outcome, only the unwind reason.
    assert "outcome" not in aborts[0]
    assert aborts[0].get("reason") == "host budget exceeded"


def test_historian_pass_unwinds_benign_on_cancel(fake_aux_module, tmp_path):
    compacted = [{"role": "system", "content": "c"}]
    responses = historian_responses(compacted)

    prepared_payload = responses["historian_prepare"]

    def prepare(params):
        fake_aux_module.state["cancelled"] = True
        return prepared_payload

    responses["historian_prepare"] = prepare
    client = FakeClient(responses)
    engine = MagicContextEngine(
        client=client,
        complete=lambda **kwargs: pytest.fail("model leg must not start"),
        project_root=tmp_path,
        session_id="cancel",
    )
    result = engine._run_historian_pass(
        client,
        session_id="cancel",
        project_root=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result is None
    aborts = [p for name, p, _ in client.calls if name == "historian_abort"]
    assert len(aborts) == 1
    assert "outcome" not in aborts[0]


def test_model_leg_timeout_clamped_to_host_budget(fake_aux_module, tmp_path):
    fake_aux_module.state["deadline"] = time.monotonic() + 45.0
    compacted = [{"role": "system", "content": "c"}]
    client = FakeClient(historian_responses(compacted))
    timeouts = []

    def complete(**kwargs):
        timeouts.append(kwargs["timeout"])
        return "<output />"

    engine = MagicContextEngine(
        client=client,
        complete=complete,
        project_root=tmp_path,
        session_id="clamp",
    )
    result = engine._run_historian_pass(
        client,
        session_id="clamp",
        project_root=str(tmp_path),
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result == compacted
    # prepare advertises timeout_ms=1000 but the nominal pass timeout floor is
    # derived from it; the clamp may only shrink, never grow, the leg.
    assert all(t <= 45.0 for t in timeouts)


def test_compress_does_not_hang_on_stuck_background_historian(
    fake_aux_module, tmp_path, monkeypatch
):
    compacted = [{"role": "system", "content": "c"}]
    client = FakeClient(historian_responses(compacted))
    engine = MagicContextEngine(
        client=client,
        complete=lambda **kwargs: "<output />",
        project_root=tmp_path,
        session_id="stuck",
    )
    release = threading.Event()

    def stuck_pass(*args, **kwargs):
        release.wait(30)

    stuck = threading.Thread(target=stuck_pass, daemon=True)
    with engine._historian_lock:
        engine._historian_thread = stuck
    stuck.start()
    fake_aux_module.state["deadline"] = time.monotonic() + 1.0
    started = time.monotonic()
    result = engine.compress([{"role": "user", "content": "hi"}])
    assert time.monotonic() - started < 10
    # The stuck background pass is abandoned within the host budget and the
    # engine returns the current rendered view instead of hanging forever.
    assert result == compacted
    release.set()
    with engine._historian_lock:
        engine._historian_thread = None
