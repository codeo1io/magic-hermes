from __future__ import annotations

import json

import pytest

from magic_hermes.runtime import RuntimeClient, runtime_available

pytestmark = pytest.mark.skipif(
    not runtime_available(),
    reason="official Pi Magic Context runtime is not installed",
)


def conversation(count=12):
    messages = [{"role": "system", "content": "You are a test assistant."}]
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append(
            {
                "role": role,
                "content": f"Runtime integration message {index}: amber-{index}.",
            }
        )
    return messages


def historian_xml(start, end):
    return f"""<output>
<compartments>
<compartment start="{start}" end="{end}" title="Runtime integration"
 episode_type="implementation" importance="80">
<p1>I verified the official runtime adapter over messages {start}-{end}.</p1>
<p2>Official runtime adapter verification.</p2>
<p3>Runtime adapter verified.</p3>
<p4>Adapter verified.</p4>
</compartment>
</compartments>
<facts>
</facts>
<events>
<causal_incident at_compartment="1">
<summary>The host requires its own auxiliary LLM route.</summary>
<affected_surface>host_integration</affected_surface>
<symptom>Upstream Pi spawning cannot run inside Hermes.</symptom>
<cause_summary>Hermes owns provider credentials and task routing.</cause_summary>
<disposition>fixed</disposition>
<evidence>The runtime adapter delegates historian completion to Hermes.</evidence>
<fix_summary>Registered the mc_historian auxiliary route.</fix_summary>
</causal_incident>
</events>
<meta>
<messages_processed>{start}-{end}</messages_processed>
<unprocessed_from>{end + 1}</unprocessed_from>
</meta>
</output>"""


def test_official_runtime_indexes_tools_memories_and_compartments(tmp_path):
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        (
            '{"historian":{"two_pass":true},'
            '"dreamer":{"tasks":{"curate":{"schedule":""},'
            '"review-user-memories":{"schedule":"0 3 * * *"}}}}'
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "context.db"
    with RuntimeClient(db_path=db_path, timeout=60) as client:
        hello = client.call("hello")
        assert hello["package_version"].startswith("0.38.")
        assert hello["harness"] == "hermes"

        bound = client.call(
            "bind",
            {
                "session_id": "runtime-integration",
                "project_root": str(tmp_path),
            },
            timeout=60,
        )
        names = {schema["name"] for schema in bound["tool_schemas"]}
        expected = {
            "ctx_search",
            "ctx_memory",
            "ctx_expand",
            "ctx_reduce",
            "ctx_note",
        }
        assert names == expected
        note_schema = next(
            schema for schema in bound["tool_schemas"] if schema["name"] == "ctx_note"
        )
        assert "surface_condition" not in note_schema["parameters"]["properties"]
        assert "Smart notes:" not in note_schema["description"]
        assert bound["config"]["dreamer_enabled"] is False
        dreamer = client.call(
            "dreamer_prepare",
            {"session_id": "runtime-integration"},
        )
        assert dreamer == {"ready": False, "reason": "curate-disabled"}

        messages = conversation()
        observed = client.call(
            "observe",
            {"session_id": "runtime-integration", "messages": messages},
        )
        assert observed["raw_message_count"] == 12
        assert observed["last_indexed_ordinal"] == 12

        write_result = client.call(
            "tool",
            {
                "session_id": "runtime-integration",
                "name": "ctx_memory",
                "arguments": {
                    "action": "write",
                    "category": "PROJECT_RULES",
                    "content": "Runtime integration uses the official upstream store.",
                },
            },
        )
        assert write_result["is_error"] is False
        assert "Saved memory" in write_result["text"]

        memory = client.call(
            "memory_context",
            {"session_id": "runtime-integration"},
        )
        assert memory["count"] == 1
        assert "official upstream store" in memory["text"]

        note_write = client.call(
            "tool",
            {
                "session_id": "runtime-integration",
                "name": "ctx_note",
                "arguments": {
                    "action": "write",
                    "content": (
                        "Recheck the runtime adapter after the next upstream release."
                    ),
                },
            },
        )
        assert note_write["is_error"] is False
        note_read = client.call(
            "tool",
            {
                "session_id": "runtime-integration",
                "name": "ctx_note",
                "arguments": {"action": "read"},
            },
        )
        assert note_read["is_error"] is False
        assert "next upstream release" in note_read["text"]

        prepared = client.call(
            "historian_prepare",
            {
                "session_id": "runtime-integration",
                "messages": messages,
                "protect_last_n": 2,
                "history_budget_tokens": 8_000,
            },
            timeout=60,
        )
        assert prepared["ready"] is True
        assert prepared["system_prompt"].startswith("# Historian")
        assert "<new_messages>" in prepared["prompt"]

        assert prepared["two_pass"] is True
        draft = historian_xml(
            prepared["chunk"]["start"],
            prepared["chunk"]["end"],
        )
        editor_request = client.call(
            "historian_publish",
            {
                "session_id": "runtime-integration",
                "output": draft,
            },
            timeout=60,
        )
        assert editor_request["needs_editor"] is True
        assert "historian editor" in editor_request["editor_system_prompt"].lower()
        assert draft in editor_request["editor_prompt"]

        published = client.call(
            "historian_publish",
            {
                "session_id": "runtime-integration",
                "output": "invalid editor output",
                "editor_pass": True,
            },
            timeout=60,
        )
        assert published["ok"] is True
        assert published["compartments_added"] == 1
        assert published["events_stored"] == 1
        assert "<session-history>" in json.dumps(published["messages"])
        assert len(published["messages"]) < len(messages)

        search = client.call(
            "tool",
            {
                "session_id": "runtime-integration",
                "name": "ctx_search",
                "arguments": {
                    "query": "amber-0",
                    "sources": ["message"],
                },
            },
        )
        assert search["is_error"] is False
        assert "amber-0" in search["text"]

        rendered = client.call(
            "render_context",
            {
                "session_id": "runtime-integration",
                "messages": messages,
                "history_budget_tokens": 8_000,
            },
        )
        assert "official runtime adapter" in rendered["history"]

    with RuntimeClient(db_path=db_path, timeout=60) as restarted:
        restarted.call(
            "bind",
            {
                "session_id": "runtime-integration",
                "project_root": str(tmp_path),
            },
            timeout=60,
        )
        expanded = restarted.call(
            "tool",
            {
                "session_id": "runtime-integration",
                "name": "ctx_expand",
                "arguments": {"start": 1, "end": 2},
            },
        )
        assert expanded["is_error"] is False
        assert "amber-0" in expanded["text"]
