from __future__ import annotations

import sys
import types
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


class FakeRuntimeClient:
    """Stands in for RuntimeClient so unit tests never spawn the real Node
    sidecar.

    ``plugin.load`` reaches ``_register_dreamer_tools``, which performs a
    real ``dreamer_tool_schemas`` handshake against the Node sidecar with a
    hard 30s deadline. Under CI host load that deadline expires
    (RuntimeProtocolError) and the release gate fails for reasons unrelated
    to the Python behavior under test. These tests assert Python-side
    registration only; the real handshake is covered by
    tests/test_runtime_integration.py."""

    def __init__(self, callback_handler=None, timeout=None):
        self.callback_handler = callback_handler
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def call(self, method, params=None, timeout=None):
        assert method == "dreamer_tool_schemas", method
        return [
            {
                "name": "ctx_memory",
                "description": "Magic Context memory",
            }
        ]


def test_zai_model_ref_uses_hermes_active_provider_shape():
    assert plugin._model_for_hermes("zai/glm-4.7") == "glm-4.7"
    assert plugin._model_for_hermes("vendor/model") == "vendor/model"


def test_plugin_registers_project_agnostic_auxiliary_slots(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(plugin, "RuntimeClient", FakeRuntimeClient)
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
    monkeypatch.setattr(plugin, "RuntimeClient", FakeRuntimeClient)
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    assert result["enabled"] is True
    assert not hasattr(plugin, "load_jsonc")
    assert context.tasks["mc_historian"]["defaults"]["model"] == ""
    assert "mc_dreamer" not in context.tasks


class ExplodingRuntimeClient(FakeRuntimeClient):
    """Simulates the Node sidecar dying during the dreamer_tool_schemas call."""

    def call(self, method, params=None, timeout=None):
        from magic_hermes.runtime import RuntimeProtocolError

        raise RuntimeProtocolError(
            "Runtime exited during dreamer_tool_schemas with status 1; "
            "request was not replayed; stderr: [magic-context] storage fatal: "
            "refusing to open context.db; upstream migration lane v85 is newer "
            "than this binary supports (max v84)"
        )


def test_dreamer_registration_failure_still_registers_context_engine(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(plugin, "RuntimeClient", ExplodingRuntimeClient)
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    # Degraded mode: engine and auxiliary slot registered, no Dreamer tools.
    assert result["enabled"] is True
    assert context.engine.name == "magic-context"
    assert context.tasks == {"mc_historian": context.tasks["mc_historian"]}
    assert context.tools == {}


class RecordingLifecycle:
    """Stands in for Hermes' subagent lifecycle registry.

    Reproduces the deployed dedup contract from subagent_lifecycle.py: a
    launch whose (parent_session_id, correlation_id) pair was already used
    (and not yet reaped) raises immediately — the exact mechanism behind
    issue #52's second bug.
    """

    def __init__(self):
        self.launches = []
        self._correlations = set()

    def launch(self, request):
        key = (getattr(request, "parent_session_id", None), request.correlation_id)
        if request.correlation_id and key in self._correlations:
            raise RuntimeError("Duplicate correlation_id for this parent session")
        self._correlations.add(key)
        self.launches.append(request)
        return SimpleNamespace(handle=str(len(self.launches)))

    def wait(self, handle, timeout_seconds=None):
        return SimpleNamespace(completed=True)

    def result(self, handle):
        return SimpleNamespace(
            terminal_state="succeeded",
            ready=True,
            summary="ok",
            error_message=None,
            error_classification=None,
            handle=SimpleNamespace(
                parent_session_id="parent-1", model="test-model"
            ),
        )


class RecordingLaunchRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.parent_session_id = None


class _StubSubagentState:
    SUCCEEDED = "succeeded"


@pytest.fixture
def hermes_lifecycle_stubs(monkeypatch):
    """Make _run_child's runtime import resolve without the Hermes venv."""
    stubs = types.ModuleType("agent")
    lifecycle_module = types.ModuleType("agent.subagent_lifecycle")
    lifecycle_module.SubagentLaunchRequest = RecordingLaunchRequest
    lifecycle_module.SubagentState = _StubSubagentState
    stubs.subagent_lifecycle = lifecycle_module
    monkeypatch.setitem(sys.modules, "agent", stubs)
    monkeypatch.setitem(sys.modules, "agent.subagent_lifecycle", lifecycle_module)


def _prompt_params(virtual_session_id, directory, prompt="p"):
    return {
        "virtual_session_id": virtual_session_id,
        "system": "s",
        "prompt": prompt,
        "model": "",
        "directory": directory,
        "agent": "dreamer",
    }


def test_second_child_prompt_on_same_virtual_session_launches(
    tmp_path, hermes_lifecycle_stubs, monkeypatch
):
    bridge = plugin._DreamerHostBridge(FakeContext())
    recording = RecordingLifecycle()
    bridge._lifecycle = recording
    # Trace correlation happens out-of-band via lifecycle hooks; skip the
    # 1s ready-wait so the test exercises launch addressing only.
    monkeypatch.setattr(bridge, "_matching_trace", lambda **kwargs: [])

    for prompt_text in ("first prompt", "second prompt"):
        out = bridge.handle(
            "dreamer_child_prompt",
            _prompt_params("mh-dream-x", str(tmp_path), prompt_text),
        )
        assert out["handle"] == "mh-dream-x"
        assert out["text"] == "ok"

    assert [req.correlation_id for req in recording.launches] == [
        "mh-dream-x",
        "mh-dream-x#l2",
    ]


def test_launch_seq_map_is_bounded(tmp_path, hermes_lifecycle_stubs, monkeypatch):
    bridge = plugin._DreamerHostBridge(FakeContext())
    bridge._lifecycle = RecordingLifecycle()
    monkeypatch.setattr(bridge, "_matching_trace", lambda **kwargs: [])

    for index in range(300):
        bridge.handle(
            "dreamer_child_prompt",
            _prompt_params(f"mh-dream-{index}", str(tmp_path)),
        )

    assert len(bridge._launch_seq) <= 256
