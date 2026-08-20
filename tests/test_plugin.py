from __future__ import annotations

from types import SimpleNamespace

import pytest

from magic_hermes import plugin


class FakeLlm:
    def __init__(self):
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="historian output")


class FakeContext:
    def __init__(self):
        self.llm = FakeLlm()
        self.tasks = {}
        self.engine = None
        self.subagent_lifecycle = SimpleNamespace()
        self.hooks = {}
        self.tools = {}

    def register_auxiliary_task(
        self, key, *, display_name, description, defaults=None
    ):
        self.tasks[key] = {
            "display_name": display_name,
            "description": description,
            "defaults": defaults,
        }

    def register_context_engine(self, engine):
        self.engine = engine
        return "registered"

    def register_hook(self, name, callback):
        self.hooks[name] = callback
        return "hook-registered"

    def register_tool(self, *, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            **kwargs,
        }
        return "tool-registered"


def test_zai_model_ref_uses_hermes_active_provider_shape():
    assert plugin._model_for_hermes("zai/glm-4.7") == "glm-4.7"
    assert plugin._model_for_hermes("vendor/model") == "vendor/model"


def test_plugin_registers_project_agnostic_auxiliary_slots(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path, session_id="plugin-test")

    assert result["enabled"] is True
    assert context.engine.name == "magic-context"
    assert set(context.tasks) == {"mc_historian"}
    assert "subagent_stop" in context.hooks
    # Plugin discovery is global; project-resolved MC models must not be frozen
    # into these defaults. They are supplied on each actual auxiliary call.
    assert context.tasks["mc_historian"]["defaults"] == {
        "provider": "auto",
        "model": "",
        "timeout": 300,
    }

    output = context.engine._complete(
        system_prompt="system",
        prompt="input",
        task="mc_historian",
        model="openai/gpt-5.1",
    )
    assert output == "historian output"
    assert context.llm.calls[0]["task"] == "mc_historian"
    assert context.llm.calls[0]["provider"] == "openai"
    assert context.llm.calls[0]["model"] == "gpt-5.1"


def test_dreamer_capabilities_follow_upstream_task_contracts():
    classify = plugin._DreamerHostBridge._capability_for_task
    toolsets = plugin._DreamerHostBridge._toolsets_for_task

    assert classify({"title": "magic-context-dream-curate"}) == "memory"
    assert classify({"title": "magic-context-dream-retrospective"}) == "memory"
    assert classify({"title": "magic-context-dream-maintain-docs"}) == "docs"
    assert classify({"title": "magic-context-dream-map-memories"}) == "read_only"
    assert classify({"title": "magic-context-dream-verify"}) == "read_only"
    assert classify({"title": "magic-context-dream-refresh-primers"}) == "read_only"
    assert classify({"title": "magic-context-dream-classify"}) == "model_only"
    assert classify({"title": "magic-context-dream-compress-cues"}) == "model_only"
    assert classify({"title": "magic-context-dream-user-memories"}) == "model_only"
    assert classify({"title": "magic-context-smart-note-compile-7"}) == "model_only"
    assert classify({"title": "magic-context-smart-note-confirm-7"}) == "model_only"

    assert toolsets({"title": "magic-context-dream-maintain-docs"}) == (
        "file",
        "terminal",
    )
    # file is the non-empty delegated-child anchor for all restricted tasks;
    # pre_tool_call enforces the actual read-only/model-only/MC-only boundary.
    assert toolsets({"title": "magic-context-dream-map-memories"}) == ("file",)
    assert toolsets({"title": "magic-context-dream-classify"}) == ("file",)


def test_registry_ctx_bridge_rejects_non_dreamer_root_session(tmp_path):
    bridge = plugin._DreamerHostBridge(FakeContext())
    bridge.route_session("root-session", str(tmp_path))

    with pytest.raises(RuntimeError, match="reserved for Magic Context-owned Dreamer"):
        bridge.tool_handler("ctx_memory")(
            {"action": "list"}, session_id="root-session"
        )


def test_plugin_does_not_read_magic_context_config_in_python(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    assert result["enabled"] is True
    assert not hasattr(plugin, "load_jsonc")
    assert context.tasks["mc_historian"]["defaults"]["model"] == ""
    assert "mc_dreamer" not in context.tasks
