"""End-to-end plugin smoke tests against the fake subc daemon."""

from __future__ import annotations

import os
import tempfile
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
    assert {"mc_historian", "mc_dreamer"} <= set(aux)


def test_engine_roundtrip_through_plugin(daemon):
    from magic_hermes import plugin

    ctx = FakeCtx()
    plugin.load(ctx)
    engine = next(p for k, p in ctx.calls if k == "context_engine")
    result = engine.compress([])
    assert "native-fallback" not in str(result) or result
