"""Tests for daemon discovery and the session client (fake daemon)."""

from __future__ import annotations

import pytest

from magic_hermes.discovery import ENV_VAR, candidate_paths, discover_connection_file
from magic_hermes.session import MagicContextSession, SessionUnavailable
from tests.subc.fake_daemon import FakeSubcDaemon


@pytest.fixture()
def env_override(monkeypatch, tmp_path):
    def setup(daemon: FakeSubcDaemon):
        cf = daemon.connection_file(tmp_path)
        monkeypatch.setenv(ENV_VAR, cf)
        return cf

    return setup


def test_env_var_short_circuits_candidates(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "/explicit/path.json")
    assert candidate_paths() == [__import__("pathlib").Path("/explicit/path.json")]


def test_discover_finds_env_override(monkeypatch, tmp_path):
    d = FakeSubcDaemon()
    cf = d.connection_file(tmp_path)
    monkeypatch.setenv(ENV_VAR, cf)
    try:
        path, info = discover_connection_file()
        assert str(path) == cf
        assert info.endpoints[0].port == d.port
    finally:
        d.stop()


def test_no_connection_file_anywhere(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "missing.json"))
    assert discover_connection_file() is None
    assert not MagicContextSession(project_root="/tmp/p", session_id="s1").connected


def test_session_connect_and_call(env_override):
    daemon = FakeSubcDaemon(
        modules=[{"module_id": "mc.core", "ops": ["compact", "search"]}]
    )
    daemon.start()
    try:
        env_override(daemon)
        session = MagicContextSession(project_root="/tmp/p", session_id="s1")
        session.connect(retries=1)
        assert session.connected
        result = session.call("compact", {"messages": []})
        assert result == {"echo": {"method": "compact", "params": {"messages": []}}}
        session.close()
        assert not session.connected
    finally:
        daemon.stop()


def test_session_unavailable_without_daemon(env_override, monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "absent.json"))
    session = MagicContextSession(project_root="/tmp/p", session_id="s1")
    with pytest.raises(SessionUnavailable):
        session.connect(retries=1)


def test_call_connects_lazily(env_override):
    daemon = FakeSubcDaemon(modules=[{"module_id": "mc.core", "ops": ["compact"]}])
    daemon.script("compact", {"ok": True})
    daemon.start()
    try:
        env_override(daemon)
        session = MagicContextSession(project_root="/tmp/p", session_id="s1")
        assert not session.connected
        # No explicit connect(): call() must connect on demand.
        assert session.call("compact", {"messages": []}) == {"ok": True}
        assert session.connected
        # And transparently reconnect after a teardown while the daemon lives.
        session._teardown()
        assert session.call("compact", {}) == {"ok": True}
        session.close()
    finally:
        daemon.stop()


def test_session_no_mc_module_in_catalog(env_override):
    daemon = FakeSubcDaemon(modules=[{"module_id": "other.module", "ops": []}])
    daemon.start()
    try:
        env_override(daemon)
        session = MagicContextSession(project_root="/tmp/p", session_id="s1")
        with pytest.raises(SessionUnavailable, match="no magic-context module"):
            session.connect(retries=1)
    finally:
        daemon.stop()


def test_connect_reports_session_unavailable_when_daemon_stalls(env_override):
    # Regression: a daemon that accepts but never replies used to leak
    # SocketTimeout out of connect() instead of the documented
    # SessionUnavailable.
    daemon = FakeSubcDaemon(drop_requests=True)
    daemon.start()
    try:
        env_override(daemon)
        session = MagicContextSession(
            project_root="/tmp/p", session_id="s1", request_timeout_ms=400
        )
        with pytest.raises(SessionUnavailable):
            session.connect(retries=1)
    finally:
        daemon.stop()


def test_call_degrades_and_recovers_on_mid_request_stall(env_override):
    # Regression: a mid-request transport failure (SocketTimeout /
    # SocketClosedError) used to escape session.call() and break the
    # fail-closed contract in the engine and tools.
    daemon = FakeSubcDaemon()
    daemon.script("context.compact", {"messages": [{"role": "user", "content": "s"}]})
    daemon.start()
    try:
        env_override(daemon)
        session = MagicContextSession(
            project_root="/tmp/p", session_id="s1", request_timeout_ms=400
        )
        session.connect(retries=1)
        assert session.call("context.compact", {}) is not None

        daemon._drop_requests = True  # daemon stalls mid-request
        with pytest.raises(SessionUnavailable):
            session.call("context.compact", {})
        assert not session.connected  # torn down, not left half-open

        daemon._drop_requests = False  # daemon recovers
        result = session.call("context.compact", {})
        assert result == {"messages": [{"role": "user", "content": "s"}]}
        assert session.connected
        session.close()
    finally:
        daemon.stop()
