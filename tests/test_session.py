"""Tests for daemon discovery and the session client (fake daemon)."""

from __future__ import annotations

import json

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
