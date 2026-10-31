from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from magic_hermes import plugin


@pytest.fixture(autouse=True)
def _never_touch_real_hermes_home(monkeypatch, tmp_path):
    """W5: no test in this module may read or write the real ~/.hermes.

    plugin.load() runs wiring.converge() and the pre_tool_call hook runs
    wiring.recheck(); both resolve HERMES_HOME/XDG_DATA_HOME at call time.
    On a machine where `magic-hermes install` recorded a lease and the
    real config is drifted — the exact production condition this run
    remediates — an un-isolated test would repair the developer's real
    config and write real backups from inside unrelated tests
    (review d-testing#0). The wiring tests' explicit _isolated_home
    calls stay for locality but are covered by this fixture too.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


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


# --- wiring convergence triggers (plan unit U5, finding c7d63424 Mode 2) ----
#
# The wiring machinery itself (lease, classifier, configure_hermes writer)
# is plan unit U4 and lands with the parallel implement lane; these
# contracts activate at the unit join. The containment tests below run
# unconditionally because they monkeypatch plugin.wiring directly.

try:
    from magic_hermes import wiring as _wiring
except ImportError:  # unit U4 lands in the parallel implement lane
    _wiring = None

requires_wiring = pytest.mark.skipif(
    _wiring is None,
    reason="magic_hermes.wiring lands with the parallel implement lane "
    "(plan unit U4); these contracts activate at the unit join",
)


def _drifted_hermes_config(home: Path) -> Path:
    """Write the live 2026-10-30 clobber shape and return its path.

    Factory wiring on both keys (engine: compressor, provider: null) while
    plugins.enabled keeps magic-hermes — the exact state that unplugs the
    plugin while flagging the doctor's unwired FAIL row. A host-managed
    comment exercises comment preservation through the repair."""
    config = home / ".hermes" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "# hermes config (host-managed)\n"
        "context:\n"
        "  engine: compressor\n"
        "memory:\n"
        "  provider: null\n"
        "plugins:\n"
        "  enabled:\n"
        "    - magic-hermes\n",
        encoding="utf-8",
    )
    return config


def _write_wiring_lease(home: Path) -> Path:
    """Write the operator-intent lease per the plan's pinned schema.

    Routed through the real writer so the lease lands at the XDG-resolved
    ``lease_path()`` the convergence reads. The isolated home overrides
    XDG_DATA_HOME to a non-default layout, and a hand-rolled default-layout
    path silently misses it — the targeted join run caught exactly that
    (2026-10-31: three requires_wiring tests failed green-lane code).
    """
    from magic_hermes import wiring

    return wiring.write_lease()


def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@requires_wiring
def test_load_converges_drifted_wiring_under_lease(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    config = _drifted_hermes_config(tmp_path)
    _write_wiring_lease(tmp_path)
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(plugin, "RuntimeClient", FakeRuntimeClient)
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    assert result["enabled"] is True
    assert context.engine.name == "magic-context"
    text = config.read_text(encoding="utf-8")
    assert "engine: magic-context" in text
    assert "provider: magic_context" in text
    assert "- magic-hermes" in text
    # comment preservation through the repair (plan W3)
    assert "# hermes config (host-managed)" in text


def test_load_survives_wiring_convergence_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(plugin, "RuntimeClient", FakeRuntimeClient)

    class ExplodingWiring:
        def converge(self):
            raise RuntimeError("wiring convergence exploded")

    monkeypatch.setattr(plugin, "wiring", ExplodingWiring())
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    # containment: registration proceeds despite the convergence failure
    assert result["enabled"] is True
    assert context.engine.name == "magic-context"
    assert set(context.tasks) == {"mc_historian"}


def test_load_skips_convergence_when_runtime_unavailable(monkeypatch):
    monkeypatch.setattr(plugin, "runtime_available", lambda: False)

    calls = []

    class RecordingWiring:
        def converge(self):
            calls.append("converge")

    monkeypatch.setattr(plugin, "wiring", RecordingWiring())
    result = plugin.load(FakeContext())

    assert result["enabled"] is False
    assert calls == []


@requires_wiring
def test_pre_tool_call_recheck_repairs_when_due(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    config = _drifted_hermes_config(tmp_path)
    _write_wiring_lease(tmp_path)
    monkeypatch.setenv("MAGIC_HERMES_WIRING_RECHECK_S", "0")
    bridge = plugin._DreamerHostBridge(FakeContext())

    verdict = bridge._on_pre_tool_call(
        session_id="root-session", tool_name="read_file"
    )

    # an ordinary (non-child) session: the capability logic is untouched
    assert verdict is None
    text = config.read_text(encoding="utf-8")
    assert "engine: magic-context" in text
    assert "provider: magic_context" in text


@requires_wiring
def test_pre_tool_call_recheck_not_due_leaves_config_untouched(
    monkeypatch, tmp_path
):
    _isolated_home(monkeypatch, tmp_path)
    config = _drifted_hermes_config(tmp_path)
    _write_wiring_lease(tmp_path)
    bridge = plugin._DreamerHostBridge(FakeContext())

    # force the check due once so the recheck's last-check state is NOW
    monkeypatch.setenv("MAGIC_HERMES_WIRING_RECHECK_S", "0")
    bridge._on_pre_tool_call(session_id="root-session", tool_name="read_file")
    assert "engine: magic-context" in config.read_text(encoding="utf-8")

    # re-drift the file; with the interval far from due the recheck must
    # not write (plan W1: the interval bounds the flap rate)
    _drifted_hermes_config(tmp_path)
    monkeypatch.setenv("MAGIC_HERMES_WIRING_RECHECK_S", "86400")
    before = config.read_text(encoding="utf-8")
    bridge._on_pre_tool_call(session_id="root-session", tool_name="read_file")
    assert config.read_text(encoding="utf-8") == before
    assert "engine: compressor" in before


def test_pre_tool_call_recheck_failure_never_escapes(monkeypatch, tmp_path):
    class ExplodingWiring:
        def recheck(self):
            raise RuntimeError("wiring recheck exploded")

    monkeypatch.setattr(plugin, "wiring", ExplodingWiring())
    bridge = plugin._DreamerHostBridge(FakeContext())

    # the failure is contained: the hook returns its ordinary value and
    # never raises into the host tool-call dispatch
    assert bridge._on_pre_tool_call(session_id="s", tool_name="read_file") is None
