"""Tests for the hermes context-engine adapter."""

from __future__ import annotations

import pytest

from magic_hermes.engine import MagicContextEngine, engine_from_session
from magic_hermes.session import MagicContextSession
from tests.subc.fake_daemon import FakeSubcDaemon as FakeDaemon


@pytest.fixture()
def daemon(tmp_path):
    d = FakeDaemon()
    d.connection_file(tmp_path)
    yield d
    d.stop()


def _make_session(daemon, tmp_path, monkeypatch):
    conn = daemon.connection_file(tmp_path)
    monkeypatch.setenv("SUBC_CONNECTION_FILE", conn)
    s = MagicContextSession(project_root=str(tmp_path), session_id="sess-1")
    s.connect()
    return s


class TestCompress:
    def test_compress_delegates_and_counts(self, daemon, tmp_path, monkeypatch):
        daemon.script(
            "context.compact", {"messages": [{"role": "user", "content": "sum"}]}
        )
        s = _make_session(daemon, tmp_path, monkeypatch)
        eng = MagicContextEngine(s)
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        out = eng.compress(msgs)
        assert out == [{"role": "user", "content": "sum"}]
        assert eng.compression_count == 1
        call = daemon.calls[-1]
        assert call["method"] == "context.compact"
        s.close()

    def test_compress_fail_closed_on_daemon_error(self, daemon, tmp_path, monkeypatch):
        daemon.script_error("context.compact", "boom")
        s = _make_session(daemon, tmp_path, monkeypatch)
        eng = MagicContextEngine(s)
        msgs = [{"role": "user", "content": "m"}]
        assert eng.compress(msgs) == msgs  # unchanged — never break the chat
        assert eng.compression_count == 0
        s.close()

    def test_compress_fail_closed_on_disconnect(self, tmp_path, monkeypatch):
        d = FakeDaemon()
        s = _make_session(d, tmp_path, monkeypatch)
        eng = MagicContextEngine(s)
        d.stop()
        s._teardown()
        msgs = [{"role": "user", "content": "m"}]
        assert eng.compress(msgs) == msgs
        s.close()


class TestTokens:
    def test_update_from_response_tracks_usage(self, daemon, tmp_path, monkeypatch):
        s = _make_session(daemon, tmp_path, monkeypatch)
        eng = MagicContextEngine(s)
        eng.context_length = 1_000_000
        eng.update_from_response(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "input_tokens": 100,
                "output_tokens": 20,
            }
        )
        assert eng.last_prompt_tokens == 100
        assert eng.last_completion_tokens == 20
        assert eng.last_total_tokens == 120
        assert eng.should_compress(1_000_000) is True
        s.close()


class TestLifecycle:
    def test_engine_from_session(self, daemon, tmp_path, monkeypatch):
        s = _make_session(daemon, tmp_path, monkeypatch)
        eng = engine_from_session(s)
        assert isinstance(eng, MagicContextEngine)
        assert eng.name == "magic-context"
        s.close()

    def test_compress_fail_closed_on_mid_request_stall(self, tmp_path, monkeypatch):
        # Regression: a daemon that stalls mid-request raises
        # SocketTimeout, which the fail-closed guards never used to catch.
        d = FakeDaemon()
        d.script("context.compact", {"messages": [{"role": "user", "content": "s"}]})
        conn = d.connection_file(tmp_path)
        monkeypatch.setenv("SUBC_CONNECTION_FILE", conn)
        s = MagicContextSession(
            project_root=str(tmp_path), session_id="sess-1", request_timeout_ms=400
        )
        try:
            s.connect(retries=1)
            eng = MagicContextEngine(s)
            eng.on_session_start("sess-1")
            d._drop_requests = True  # connected, then stalls
            msgs = [{"role": "user", "content": "m"}]
            assert eng.compress(msgs) == msgs  # unchanged — never break the turn
            assert any(call.get("method") == "context.compact" for call in d.calls)
            assert not s.connected
        finally:
            d.stop()
            s.close()

    def test_compress_publishes_historian_signal(self, daemon, tmp_path, monkeypatch):
        # U6: a successful compaction pass must feed the historian's
        # signal queue so the auxiliary task has work to drain.
        from magic_hermes.auxiliary import SignalQueue

        daemon.script(
            "context.compact", {"messages": [{"role": "user", "content": "sum"}]}
        )
        s = _make_session(daemon, tmp_path, monkeypatch)
        queue = SignalQueue()
        eng = MagicContextEngine(s, signal_queue=queue)
        eng.on_session_start("sess-1")
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        assert eng.compress(msgs) == [{"role": "user", "content": "sum"}]
        assert queue.pending() == 1
        (signal,) = queue.drain()
        assert signal.session_id == "sess-1"
        assert signal.ordinal_range == (1, 20)
        s.close()

    def test_compress_without_queue_is_quiet(self, daemon, tmp_path, monkeypatch):
        daemon.script(
            "context.compact", {"messages": [{"role": "user", "content": "sum"}]}
        )
        s = _make_session(daemon, tmp_path, monkeypatch)
        eng = MagicContextEngine(s)  # no queue wired
        assert eng.compress([{"role": "user", "content": "m"}]) == [
            {"role": "user", "content": "sum"}
        ]
        s.close()
