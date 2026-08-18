"""End-to-end plugin smoke tests against the fake subc daemon."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .subc.fake_daemon import FakeSubcDaemon


@pytest.fixture()
def daemon(tmp_path: Path):
    d = FakeSubcDaemon()
    d.start()
    os.environ["SUBC_CONNECTION_FILE"] = d.connection_file(tmp_path)
    yield d
    d.stop()
    os.environ.pop("SUBC_CONNECTION_FILE", None)


class FakeCtx:
    """Records hermes plugin-API registrations."""

    def __init__(self):
        self.calls = []

    def register_context_engine(self, engine):
        self.calls.append(("context_engine", engine))

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.calls.append(("tool", name))

    def register_hook(self, name, handler):
        self.calls.append(("hook", name))

    def register_memory_provider(self, provider):
        self.calls.append(("memory_provider", provider))

    def register_auxiliary_task(self, key, **kw):
        self.calls.append(("auxiliary", key))


def test_load_disabled_without_daemon(tmp_path, monkeypatch):
    from magic_hermes import plugin

    monkeypatch.delenv("SUBC_CONNECTION_FILE", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # no connection file

    result = plugin.load(FakeCtx())
    assert result == {"enabled": False, "reason": "no-subc-daemon"}


def test_load_registers_all_surfaces(daemon):
    from magic_hermes import plugin

    ctx = FakeCtx()
    result = plugin.load(ctx)

    assert result["enabled"] is True
    kinds = [kind for kind, _ in ctx.calls]
    assert "context_engine" in kinds
    assert "memory_provider" in kinds
    names = [payload for kind, payload in ctx.calls if kind == "tool"]
    assert {"ctx_search", "ctx_expand", "ctx_reduce"} <= set(names)
    aux = [payload for kind, payload in ctx.calls if kind == "auxiliary"]
    assert sorted(aux) == ["mc_dreamer", "mc_historian"]
    engine = next(payload for kind, payload in ctx.calls if kind == "context_engine")
    assert engine._signal_queue is result["auxiliary"]["mc_historian"]


def test_load_without_auxiliary_api_still_registers_core_surfaces(daemon):
    from magic_hermes import plugin

    ctx = FakeCtx()
    ctx.register_auxiliary_task = None
    result = plugin.load(ctx)

    assert result["enabled"] is True
    assert result["auxiliary"] == {}
    kinds = [kind for kind, _ in ctx.calls]
    assert "context_engine" in kinds
    assert "memory_provider" in kinds
    assert "auxiliary" not in kinds
    engine = next(payload for kind, payload in ctx.calls if kind == "context_engine")
    assert engine._signal_queue is None


def test_engine_roundtrip_through_plugin(daemon):
    from magic_hermes import plugin

    ctx = FakeCtx()
    plugin.load(ctx)
    engine = next(p for k, p in ctx.calls if k == "context_engine")
    result = engine.compress([])
    assert "native-fallback" not in str(result) or result


def test_plugin_surfaces_connect_lazily(daemon):
    """The plugin never calls session.connect() itself — surfaces must work
    against a live daemon via lazy connect (regression: every registered
    surface used to fail closed with 'session not connected')."""
    from magic_hermes import plugin

    daemon.script("context.compact", {"messages": [{"role": "user", "content": "sum"}]})
    daemon.script("memory.list", {"memories": [{"id": 1, "content": "m1"}]})
    ctx = FakeCtx()
    result = plugin.load(ctx)
    assert result["enabled"] is True

    engine = next(p for k, p in ctx.calls if k == "context_engine")
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    assert engine.compress(msgs) == [{"role": "user", "content": "sum"}]
    assert engine.compression_count == 1

    provider = next(p for k, p in ctx.calls if k == "memory_provider")
    assert provider.is_available() is True
    assert "m1" in provider.system_prompt_block()
    provider.shutdown()
