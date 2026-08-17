"""U6 tests: historian/dreamer auxiliary runners."""

from __future__ import annotations

import pytest

from magic_hermes.auxiliary import (
    CompactionSignal,
    DreamerRunner,
    HistorianRunner,
    SignalQueue,
    auxiliary_defaults_from_config,
    register,
)


@pytest.fixture
def queue():
    return SignalQueue()


class TestSignalQueue:
    def test_publish_drain_roundtrip(self, queue):
        queue.publish(CompactionSignal("s1", (10, 245)))
        queue.publish(CompactionSignal("s2", (1, 9)))
        assert queue.pending() == 2
        drained = queue.drain()
        assert len(drained) == 2 and queue.pending() == 0

    def test_requeue_keeps_signal_with_error(self, queue):
        sig = CompactionSignal("s1", (10, 245))
        queue.publish(sig)
        [drained] = queue.drain()
        queue.requeue(drained, "boom")
        [again] = queue.drain()
        assert again.attempts == 1 and again.last_error == "boom"


class TestHistorianRunner:
    def _published(self):
        box = []
        return box, lambda sid, rng, summary: box.append((sid, rng, summary))

    def test_happy_path_publishes_summary(self, queue):
        box, publish = self._published()
        runner = HistorianRunner(
            queue,
            call_model=lambda p, s, t: f"SUM[{s}]",
            publish_summary=publish,
            sleep=lambda _: None,
        )
        queue.publish(CompactionSignal("sess-1", (10, 245)))
        assert runner.run_pass() == 1
        assert box == [("sess-1", (10, 245), "SUM[sess-1]")]

    def test_model_failure_retries_then_drops(self, queue):
        box, publish = self._published()
        calls = {"n": 0}

        def failing(prompt, sid, task):
            calls["n"] += 1
            raise RuntimeError("model down")

        runner = HistorianRunner(
            queue, call_model=failing, publish_summary=publish, sleep=lambda _: None
        )
        queue.publish(CompactionSignal("sess-1", (10, 245)))
        assert runner.run_pass() == 0
        assert queue.pending() == 1  # requeued
        queue.drain() and queue.requeue(
            CompactionSignal("sess-1", (10, 245), attempts=2), "x"
        )
        runner.run_pass()
        assert calls["n"] == 2
        assert queue.pending() == 0  # dropped after max attempts
        assert box == []

    def test_publish_failure_requeues(self, queue):
        def bad_publish(sid, rng, summary):
            raise RuntimeError("daemon down")

        runner = HistorianRunner(
            queue,
            call_model=lambda p, s, t: "S",
            publish_summary=bad_publish,
            sleep=lambda _: None,
        )
        queue.publish(CompactionSignal("sess-1", (1, 9)))
        assert runner.run_pass() == 0
        assert queue.pending() == 1


class TestDreamerRunner:
    def test_runs_when_due_and_publishes_lines(self):
        memories = []
        runner = DreamerRunner(
            call_model=lambda p, s, t: "rule A\nrule B",
            publish_memory=lambda sid, mem: memories.append((sid, mem)),
            clock=lambda: 100.0,
        )
        assert runner.due() is True
        assert runner.run_pass("transcript", "sess-1") is True
        assert memories == [("sess-1", "rule A"), ("sess-1", "rule B")]

    def test_not_due_before_interval(self):
        runner = DreamerRunner(
            call_model=lambda p, s, t: "",
            publish_memory=lambda sid, mem: None,
            interval_s=900,
            clock=lambda: 100.0,
        )
        runner.run_pass("t", "sess-1")
        assert runner.due() is False
        assert runner.run_pass("t2", "sess-1") is False

    def test_model_failure_does_not_raise(self):
        runner = DreamerRunner(
            call_model=lambda p, s, t: (_ for _ in ()).throw(RuntimeError("x")),
            publish_memory=lambda sid, mem: None,
            clock=lambda: 0.0,
        )
        assert runner.run_pass("t", "sess-1") is True  # pass ran, failed silently


class TestRegistration:
    def test_defaults_from_shared_config(self, tmp_path):
        cfg = {
            "historian": {"model": "zai/glm-4.7"},
            "dreamer": {"model": "zai/glm-4.7"},
        }
        defaults = auxiliary_defaults_from_config(cfg)
        assert defaults["mc_historian"]["model"] == "zai/glm-4.7"
        assert defaults["mc_dreamer"]["model"] == "zai/glm-4.7"

    def test_defaults_fall_back_when_config_empty(self):
        defaults = auxiliary_defaults_from_config({})
        assert defaults["mc_historian"]["model"]  # non-empty default

    def test_register_calls_auxiliary_task_api(self):
        recorded = []

        class FakeCtx:
            def register_auxiliary_task(self, key, **kw):
                recorded.append((key, kw))

        queues = {}
        result = register(FakeCtx(), {})
        assert [k for k, _ in recorded] == ["mc_historian", "mc_dreamer"]
        assert "mc_historian" in result
