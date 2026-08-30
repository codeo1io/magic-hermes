from __future__ import annotations

import copy
import json

from magic_hermes.engine import MagicContextEngine


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.closed = False

    def __deepcopy__(self, memo):
        copied = type(self)(self.responses)
        copied.calls = self.calls
        memo[id(self)] = copied
        return copied

    def call(self, method, params=None, *, timeout=None):
        self.calls.append((method, params or {}, timeout))
        value = self.responses.get(method)
        if callable(value):
            return value(params or {})
        return value or {}

    def close(self):
        self.closed = True


def bind_result():
    return {
        "config": {
            "enabled": True,
            "execute_threshold_percentage": 65,
            "history_budget_percentage": 0.15,
        },
        "tool_schemas": [
            {"name": "ctx_search", "description": "search", "parameters": {}},
            {"name": "ctx_memory", "description": "memory", "parameters": {}},
        ],
    }


def test_engine_deepcopy_creates_disconnected_client():
    def complete(**kwargs):
        return "output"

    engine = MagicContextEngine(
        client=FakeClient({"bind": bind_result()}),
        complete=complete,
        project_root="/tmp",
    )

    cloned = copy.deepcopy(engine)

    assert cloned is not engine
    assert cloned._client is not engine._client
    assert cloned._complete is complete
    assert cloned._project_root == engine._project_root


def test_engine_close_releases_private_runtime_and_session_route():
    routed = []
    client = FakeClient()
    engine = MagicContextEngine(
        client=client,
        project_root="/tmp",
        session_id="session-cleanup",
        session_route=lambda session_id, project_root: routed.append(
            (session_id, project_root)
        ),
    )

    engine.close()

    assert client.closed is True
    assert routed == [("session-cleanup", None)]
    assert engine._bound_identity is None


def test_engine_deepcopy_cleanup_is_independent_per_session():
    engine = MagicContextEngine(client=FakeClient(), project_root="/tmp")
    cloned = copy.deepcopy(engine)

    cloned.close()

    assert cloned._client.closed is True
    assert engine._client.closed is False


def test_engine_deepcopy_resolves_live_host_root_before_schema_bind(
    monkeypatch, tmp_path
):
    discovery_root = tmp_path / "discovery"
    live_root = tmp_path / "live-project"
    discovery_root.mkdir()
    live_root.mkdir()

    monkeypatch.chdir(discovery_root)
    engine = MagicContextEngine(client=FakeClient({"bind": bind_result()}))
    assert engine._project_root == str(discovery_root.resolve())

    monkeypatch.chdir(live_root)
    cloned = copy.deepcopy(engine)
    schemas = cloned.get_tool_schemas()

    assert [schema["name"] for schema in schemas] == ["ctx_search", "ctx_memory"]
    method, params, _ = cloned._client.calls[0]
    assert method == "bind"
    assert params["project_root"] == str(live_root.resolve())


def test_engine_binds_real_session_and_excludes_memory_tool(tmp_path):
    client = FakeClient({"bind": bind_result()})
    engine = MagicContextEngine(client=client, project_root=tmp_path)

    engine.on_session_start("session-42", cwd=str(tmp_path))
    schemas = engine.get_tool_schemas()

    assert [schema["name"] for schema in schemas] == ["ctx_search", "ctx_memory"]
    method, params, _ = client.calls[0]
    assert method == "bind"
    assert params["session_id"] == "session-42"
    assert params["project_root"] == str(tmp_path.resolve())


def test_engine_host_gate_uses_upstream_emergency_pressure(tmp_path):
    def pressure(params):
        limit = int(params.get("context_length") or 0)
        tokens = int(params.get("input_tokens") or 0)
        return {
            "should_block": bool(limit and tokens / limit >= 0.95),
            "emergency_percentage": 95,
        }

    client = FakeClient({"bind": bind_result(), "pressure_state": pressure})
    engine = MagicContextEngine(client=client, project_root=tmp_path)
    engine.context_length = 100_000
    engine.on_session_start("threshold", cwd=str(tmp_path))

    # The shared MC execute threshold (65 in bind_result) is no longer copied
    # into Hermes. Hermes only blocks at MC's upstream emergency band.
    assert engine.threshold_tokens == 95_000
    assert engine.should_compress(64_999) is False
    assert engine.should_compress(94_999) is False
    assert engine.should_compress(95_000) is True

    engine.update_model("replacement", 200_000)
    assert engine.threshold_tokens == 190_000


def test_compress_calls_historian_and_returns_validated_view(tmp_path):
    prepared = {
        "ready": True,
        "system_prompt": "# Historian",
        "prompt": "<new_messages>history</new_messages>",
        "model": "zai/glm-4.7",
        "timeout_ms": 1_000,
    }
    compacted = [
        {"role": "system", "content": "base"},
        {"role": "system", "content": "<session-history>summary</session-history>"},
        {"role": "user", "content": "tail"},
    ]
    client = FakeClient(
        {
            "bind": bind_result(),
            "historian_decide": {
                "should_fire": True,
                "reason": "trigger",
                "boundary_snapshot": {"offset": 1, "eligibleEndOrdinal": 8},
            },
            "historian_prepare": prepared,
            "historian_publish": {"ok": True, "messages": compacted},
            "render_context": {"messages": compacted},
        }
    )
    completions = []

    def complete(**kwargs):
        completions.append(kwargs)
        return "<output />"

    engine = MagicContextEngine(
        client=client,
        complete=complete,
        project_root=tmp_path,
        session_id="compress",
    )
    result = engine.compress(
        [
            {"role": "system", "content": "base"},
            *[
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": str(index),
                }
                for index in range(10)
            ],
        ]
    )

    assert result == compacted
    assert engine.compression_count == 1
    assert completions[0]["task"] == "mc_historian"
    assert completions[0]["system_prompt"] == "# Historian"


def test_compress_runs_configured_two_pass_editor(tmp_path):
    prepared = {
        "ready": True,
        "system_prompt": "# Historian",
        "prompt": "<new_messages>history</new_messages>",
        "model": "zai/glm-4.7",
        "timeout_ms": 1_000,
        "two_pass": True,
    }
    compacted = [
        {"role": "system", "content": "<session-history>edited</session-history>"},
        {"role": "user", "content": "tail"},
    ]
    publish_calls = []

    def publish(params):
        publish_calls.append(params)
        if params.get("editor_pass"):
            return {"ok": True, "messages": compacted}
        return {
            "ok": False,
            "needs_editor": True,
            "editor_system_prompt": "# Historian Editor",
            "editor_prompt": "Edit the validated draft",
        }

    client = FakeClient(
        {
            "bind": bind_result(),
            "historian_decide": {
                "should_fire": True,
                "reason": "trigger",
                "boundary_snapshot": {"offset": 1, "eligibleEndOrdinal": 8},
            },
            "historian_prepare": prepared,
            "historian_publish": publish,
            "render_context": {"messages": compacted},
        }
    )
    completions = []

    def complete(**kwargs):
        completions.append(kwargs)
        return "edited" if len(completions) == 2 else "draft"

    engine = MagicContextEngine(
        client=client,
        complete=complete,
        project_root=tmp_path,
        session_id="two-pass",
    )

    result = engine.compress(
        [
            {"role": "system", "content": "base"},
            *[
                {
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": str(index),
                }
                for index in range(10)
            ],
        ]
    )

    assert result == compacted
    assert [call["system_prompt"] for call in completions] == [
        "# Historian",
        "# Historian Editor",
    ]
    assert [call["output"] for call in publish_calls] == ["draft", "edited"]
    assert publish_calls[1]["editor_pass"] is True


def test_compress_fails_open_on_host_completion_error(tmp_path):
    original = [
        {"role": "system", "content": "base"},
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": str(index),
            }
            for index in range(10)
        ],
    ]
    client = FakeClient(
        {
            "bind": bind_result(),
            "historian_decide": {
                "should_fire": True,
                "reason": "trigger",
                "boundary_snapshot": {"offset": 1, "eligibleEndOrdinal": 8},
            },
            "historian_prepare": {
                "ready": True,
                "system_prompt": "# Historian",
                "prompt": "history",
            },
        }
    )

    def complete(**_kwargs):
        raise LookupError("provider failed")

    engine = MagicContextEngine(
        client=client,
        complete=complete,
        project_root=tmp_path,
        session_id="fail-open",
    )

    assert engine.compress(original) is original
    assert engine.compression_count == 0


def test_select_context_keeps_upstream_tag_transform_without_compartments(tmp_path):
    original = [{"role": "user", "content": "hello"}]
    tagged = [{"role": "user", "content": "§1§ hello"}]
    client = FakeClient(
        {
            "bind": bind_result(),
            "render_context": {
                "messages": tagged,
                "history": "",
                "scheduler_decision": "defer",
            },
        }
    )
    engine = MagicContextEngine(
        client=client,
        project_root=tmp_path,
        session_id="render-tags",
    )

    assert engine.select_context(original) == tagged


def test_on_turn_complete_uses_upstream_trigger_to_schedule_background_historian(
    monkeypatch, tmp_path
):
    boundary = {"offset": 1, "eligibleEndOrdinal": 8}
    client = FakeClient(
        {
            "bind": bind_result(),
            "observe": {"raw_message_count": 10},
            "historian_decide": {
                "should_fire": True,
                "reason": "projected_headroom",
                "boundary_snapshot": boundary,
            },
        }
    )
    engine = MagicContextEngine(
        client=client,
        complete=lambda **_kwargs: "unused",
        project_root=tmp_path,
        session_id="async-trigger",
    )
    scheduled = []

    def capture(messages, snapshot):
        scheduled.append((messages, snapshot))
        return True

    monkeypatch.setattr(engine, "_schedule_historian", capture)
    monkeypatch.setattr(engine, "_schedule_dreamer", lambda: False)
    messages = [{"role": "user", "content": "turn"}]

    engine.on_turn_complete(messages)

    assert scheduled == [(messages, boundary)]
    methods = [method for method, _params, _timeout in client.calls]
    assert methods[-2:] == ["observe", "historian_decide"]


def test_tool_result_is_json_and_passes_current_messages(tmp_path):
    client = FakeClient(
        {
            "bind": bind_result(),
            "tool": {"text": "found", "is_error": False},
        }
    )
    engine = MagicContextEngine(
        client=client,
        project_root=tmp_path,
        session_id="tools",
    )
    messages = [{"role": "user", "content": "find it"}]

    result = json.loads(
        engine.handle_tool_call(
            "ctx_search",
            {"query": "it"},
            messages=messages,
        )
    )

    assert result == {"content": "found"}
    tool_call = next(call for call in client.calls if call[0] == "tool")
    assert tool_call[1]["messages"] == messages
