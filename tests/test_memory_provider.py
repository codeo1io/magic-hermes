"""Tests for the MagicContextMemoryProvider (U5)."""

import json
import threading

import pytest

from tests.subc.fake_daemon import FakeSubcDaemon as FakeDaemon
from magic_hermes.memory_provider import MagicContextMemoryProvider
from magic_hermes.session import MagicContextSession


@pytest.fixture
def daemon():
    d = FakeDaemon(modules=[{"module_id": "mc.core", "ops": ["memory"]}])
    d.script(
        "memory.list",
        {
            "memories": [
                {
                    "id": 1,
                    "content": "always use ruff",
                    "category": "PROJECT_RULES",
                },
                {"id": 2, "content": "port 8317", "category": "CONFIG_VALUES"},
            ]
        },
    )
    d.script("memory.search", {"hits": [{"id": 2, "text": "port 8317"}]})
    d.script("memory.write", {"ok": True, "id": 3})
    d.script("memory.archive", {"ok": True})
    yield d
    d.stop()


@pytest.fixture
def provider(daemon, tmp_path, monkeypatch):
    monkeypatch.setenv("SUBC_CONNECTION_FILE", daemon.connection_file(tmp_path))

    def _factory():
        s = MagicContextSession(project_root=str(tmp_path), session_id="sess-1")
        s.connect()
        return s

    p = MagicContextMemoryProvider(session_factory=_factory)
    yield p
    p.shutdown()


class TestLifecycle:
    def test_available_when_daemon_up(self, provider):
        assert provider.is_available() is True

    def test_unavailable_when_daemon_down(self):
        p = MagicContextMemoryProvider(
            session_factory=lambda: MagicContextSession(
                "127.0.0.1", 1, secret=b"x" * 32
            )
        )
        assert p.is_available() is False

    def test_initialize_loads_memories(self, provider, daemon):
        provider.initialize("sess-1")
        block = provider.system_prompt_block()
        assert "## Project Memory" in block
        assert "#1 (PROJECT_RULES): always use ruff" in block

    def test_empty_block_when_daemon_has_no_memories(self, provider):
        provider.initialize("s")
        provider._memories = []
        assert provider.system_prompt_block() == ""


class TestPrefetch:
    def test_prefetch_formats_hits(self, provider):
        block = provider.prefetch("what port")
        assert "port 8317" in block

    def test_prefetch_swallows_errors(self):
        p = MagicContextMemoryProvider(
            session_factory=lambda: (_ for _ in ()).throw(ConnectionError("down"))
        )
        assert p.prefetch("q") == ""


class TestTools:
    def test_write_action(self, provider, daemon):
        provider.initialize("s")
        out = json.loads(
            provider.handle_tool_call(
                "ctx_memory", {"action": "write", "content": "new fact"}
            )
        )
        assert out["ok"] is True
        seen = [r for r in daemon.requests_seen if r.get("method") == "memory.write"]
        assert seen and seen[0]["params"]["content"] == "new fact"

    def test_search_action(self, provider):
        out = json.loads(
            provider.handle_tool_call(
                "ctx_memory", {"action": "search", "query": "port"}
            )
        )
        assert out == {"hits": [{"id": 2, "text": "port 8317"}]}

    def test_unknown_tool(self, provider):
        out = json.loads(provider.handle_tool_call("other", {}))
        assert "error" in out

    def test_daemon_error_returns_json_error(self):
        def boom():
            raise ConnectionError("down")

        p = MagicContextMemoryProvider(session_factory=boom)
        out = json.loads(
            p.handle_tool_call("ctx_memory", {"action": "search", "query": "x"})
        )
        assert "unavailable" in out["error"]

    def test_schemas_present(self, provider):
        schemas = provider.get_tool_schemas()
        assert schemas[0]["name"] == "ctx_memory"
