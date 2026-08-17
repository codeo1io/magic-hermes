"""Unit tests for the ctx tool surface (U4)."""

from __future__ import annotations

import json

import pytest

from magic_hermes.session import MagicContextSession
from magic_hermes.tools import TOOL_SPECS, make_handlers, register_tools
from tests.subc.fake_daemon import FakeSubcDaemon as FakeDaemon


@pytest.fixture()
def daemon(tmp_path):
    d = FakeDaemon()
    d.connection_file(tmp_path)
    yield d
    d.stop()


@pytest.fixture()
def pair(daemon, tmp_path, monkeypatch):
    conn = daemon.connection_file(tmp_path)
    monkeypatch.setenv("SUBC_CONNECTION_FILE", conn)
    s = MagicContextSession(project_root=str(tmp_path), session_id="sess-1")
    s.connect()
    daemon.script("memory.search", {"hits": [{"id": "m1", "text": "match"}]})
    yield daemon, s
    s.close()


class TestSchemas:
    def test_five_tools(self):
        assert [s["name"] for s in TOOL_SPECS] == [
            "ctx_search",
            "ctx_expand",
            "ctx_reduce",
            "ctx_memory",
            "ctx_note",
        ]

    def test_schemas_have_required_fields(self):
        for schema in TOOL_SPECS:
            assert schema["description"]
            assert schema["input_schema"]["type"] == "object"


class TestHandlers:
    def test_search_returns_json_result(self, pair):
        daemon, session = pair
        handler = make_handlers(session)["ctx_search"]
        out = json.loads(handler({"query": "glm"}))
        assert out == {"hits": [{"id": "m1", "text": "match"}]}
        seen = daemon.requests_seen
        assert seen[0]["method"] == "memory.search"
        assert seen[0]["params"]["query"] == "glm"

    def test_tool_error_is_json_not_crash(self, pair):
        daemon, session = pair
        daemon.script_error("memory.search", "boom")
        handler = make_handlers(session)["ctx_search"]
        out = json.loads(handler({"query": "x"}))
        assert "error" in out


class TestRegister:
    def test_register_tools_uses_plugin_context(self, pair):
        daemon, session = pair

        class FakeCtx:
            def __init__(self):
                self.registered = []

            def register_tool(self, name, toolset, schema, handler, **kw):
                assert toolset == "magic-context"
                self.registered.append((name, handler))

        fake = FakeCtx()
        names = register_tools(fake, session)
        assert names == [s["name"] for s in TOOL_SPECS]
        assert len(fake.registered) == 5
        out = json.loads(fake.registered[0][1]({"query": "glm"}))
        assert out["hits"][0]["id"] == "m1"
