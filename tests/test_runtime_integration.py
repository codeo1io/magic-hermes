from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from magic_hermes.runtime import (
    RuntimeClient,
    runtime_available,
    supported_magic_context_series,
)

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
    primer_question = (
        "How does the runtime adapter preserve Magic Context lifecycle parity?"
    )
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
<primer_candidates>
<primer at_compartment="1">{primer_question}</primer>
</primer_candidates>
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
        major, minor = supported_magic_context_series()
        assert hello["package_version"].startswith(f"{major}.{minor}.")
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
        assert "surface_condition" in note_schema["parameters"]["properties"]
        assert "Smart notes:" in note_schema["description"]
        assert bound["config"]["dreamer_enabled"] is False

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
        assert published["primer_candidates_stored"] == 1
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
        rendered_text = json.dumps(rendered["messages"])
        assert "official runtime adapter" in rendered_text
        assert "<session-history>" in rendered_text
        assert rendered["synthetic_leading_count"] == 2

        # Historian completion is an upstream note-nudge trigger. The first
        # render anchors/defer-delivers on the trigger-time user message; the
        # next user turn receives the canonical deferred-note instruction.
        followup = [*messages, {"role": "user", "content": "Continue after historian."}]
        nudged = client.call(
            "render_context",
            {
                "session_id": "runtime-integration",
                "messages": followup,
                "history_budget_tokens": 8_000,
            },
        )
        followup_user = next(
            message
            for message in nudged["messages"]
            if "Continue after historian." in str(message.get("content", ""))
        )
        assert '<instruction name="deferred_notes">' in followup_user["content"]

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


def test_ctx_expand_preserves_rich_tool_history_across_runtime_restart(tmp_path):
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        '{"historian":{"two_pass":false}}', encoding="utf-8"
    )
    db_path = tmp_path / "rich-expand.db"
    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "Inspect the sample file."},
        {
            "role": "assistant",
            "content": "I will inspect it.",
            "tool_calls": [
                {
                    "id": "call-rich-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "sample.txt"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-rich-1",
            "name": "read_file",
            "content": "RICH-TOOL-OUTPUT-XYZ",
        },
        {"role": "assistant", "content": "The sample file was inspected."},
    ]
    for index in range(12):
        messages.append(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"Rich restart tail {index}.",
            }
        )

    with RuntimeClient(db_path=db_path, timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "rich-restart", "project_root": str(tmp_path)},
            timeout=60,
        )
        prepared = client.call(
            "historian_prepare",
            {
                "session_id": "rich-restart",
                "messages": messages,
                "protect_last_n": 2,
                "history_budget_tokens": 8_000,
            },
            timeout=60,
        )
        assert prepared["ready"] is True
        published = client.call(
            "historian_publish",
            {
                "session_id": "rich-restart",
                "output": historian_xml(
                    prepared["chunk"]["start"], prepared["chunk"]["end"]
                ),
            },
            timeout=60,
        )
        assert published["ok"] is True

    # A resumed Hermes session supplies its restored canonical conversation to
    # context-engine tools.  The fresh MC sidecar must reconstruct the rich
    # branch from that host snapshot, not degrade to message_history_fts text.
    with RuntimeClient(db_path=db_path, timeout=60) as restarted:
        restarted.call(
            "bind",
            {"session_id": "rich-restart", "project_root": str(tmp_path)},
            timeout=60,
        )
        assistant = restarted.call(
            "tool",
            {
                "session_id": "rich-restart",
                "name": "ctx_expand",
                "arguments": {"message": 2},
                "messages": messages,
            },
            timeout=60,
        )
        tool_result = restarted.call(
            "tool",
            {
                "session_id": "rich-restart",
                "name": "ctx_expand",
                "arguments": {"message": 3},
                "messages": messages,
            },
            timeout=60,
        )

    assert assistant["is_error"] is False
    assert "full recovery" in assistant["text"]
    assert "read_file" in assistant["text"]
    assert "sample.txt" in assistant["text"]
    assert tool_result["is_error"] is False
    assert "full recovery" in tool_result["text"]
    assert "RICH-TOOL-OUTPUT-XYZ" in tool_result["text"]
    with sqlite3.connect(db_path) as conn:
        watermark = conn.execute(
            "SELECT last_indexed_ordinal FROM message_history_index "
            "WHERE session_id = ?",
            ("rich-restart",),
        ).fetchone()[0]
        max_source = conn.execute(
            "SELECT MAX(message_ordinal) FROM message_history_source "
            "WHERE session_id = ?",
            ("rich-restart",),
        ).fetchone()[0]
    assert watermark == 16
    assert max_source == 16


def test_retrospective_reads_hermes_history_and_persists_learning_watermark(tmp_path):
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "dreamer": {"tasks": {"retrospective": {"schedule": ""}}},
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "retrospective.db"
    calls = []
    learning = (
        "Diagnose scope before mutating state when a request asks for "
        "investigation rather than changes."
    )

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        system = str(params.get("system", ""))
        prompt = str(params.get("prompt", ""))
        calls.append((system, prompt))
        if "conservative friction detector" in system:
            assert "Decide whether these user lines" in prompt
            # The retrospective raw provider intentionally exposes user lines;
            # the corrective user message is therefore ordinal 2 in this scan.
            return {"text": "y: 2", "tool_history": []}
        assert "retrospective learning agent" in system.lower()
        assert "[friction]" in prompt
        return {
            "text": (
                '<learnings><learning route="memory" category="PROJECT_RULES">'
                f"{learning}</learning></learnings>"
            ),
            "tool_history": [],
        }

    with RuntimeClient(
        db_path=db_path, timeout=60, callback_handler=callback
    ) as client:
        client.call(
            "bind",
            {"session_id": "retro-source", "project_root": str(tmp_path)},
            timeout=60,
        )
        base = 1_790_000_000_000
        messages = [
            {"role": "system", "content": "System."},
            {
                "role": "user",
                "content": "Please investigate the failure and report what you find.",
                "timestamp": base,
            },
            {
                "role": "assistant",
                "content": "I changed the implementation while investigating.",
                "timestamp": base + 1_000,
            },
            {
                "role": "user",
                "content": (
                    "You ignored the investigation-only constraint again; "
                    "diagnose before changing anything."
                ),
                "timestamp": base + 2_000,
            },
            {
                "role": "assistant",
                "content": "Understood.",
                "timestamp": base + 3_000,
            },
            {
                "role": "user",
                "content": (
                    "This has happened before; keep investigation and mutation "
                    "separate."
                ),
                "timestamp": base + 4_000,
            },
        ]
        client.call(
            "observe",
            {"session_id": "retro-source", "messages": messages},
            timeout=60,
        )
        result = client.call(
            "dreamer_run_manual",
            {"session_id": "retro-source", "task": "retrospective"},
            timeout=120,
        )
        assert result["failed"] == [], result.get("failureDetails") or result
        assert result["ran"] == ["retrospective"]

        # Running again without any newer Hermes messages must not call the
        # deepening pass or write a duplicate memory; the upstream watermark is
        # authoritative across runs.
        first_call_count = len(calls)
        second = client.call(
            "dreamer_run_manual",
            {"session_id": "retro-source", "task": "retrospective"},
            timeout=120,
        )
        assert second["failed"] == [], second.get("failureDetails") or second

    with sqlite3.connect(db_path) as conn:
        learned = conn.execute(
            "SELECT content, source_type, metadata_json FROM memories "
            "WHERE metadata_json LIKE '%retrospective%'"
        ).fetchall()
        schedule = conn.execute(
            "SELECT retrospective_watermark_ms FROM task_schedule_state "
            "WHERE task = 'retrospective'"
        ).fetchone()
    assert learned == [
        (
            learning,
            "dreamer",
            '{"source":"retrospective"}',
        )
    ]
    assert schedule is not None and schedule[0] >= base + 4_000
    assert len(calls) == first_call_count


def test_maintain_docs_restores_protected_regions_after_host_child_edits(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "core.py").write_text("VALUE = 'current'\n", encoding="utf-8")
    protected = """<!-- mc:protected START invariant -->
HAND-AUTHORED INVARIANT: keep this byte-for-byte.
<!-- mc:protected END -->"""
    architecture = tmp_path / "ARCHITECTURE.md"
    structure = tmp_path / "STRUCTURE.md"
    architecture.write_text(
        f"# Architecture\n\n{protected}\n\nOld stale architecture text.\n",
        encoding="utf-8",
    )
    structure.write_text(
        "# Codebase Structure\n\nOld stale structure text.\n", encoding="utf-8"
    )
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "dreamer": {"tasks": {"maintain-docs": {"schedule": ""}}},
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        assert "maintain-docs" in str(params.get("title", ""))
        # Simulate the supported host child edit primitive. Deliberately damage
        # the protected block: the upstream maintain-docs wrapper must restore
        # it from its pre-task snapshot after the child completes.
        architecture.write_text(
            "# Architecture\n\n<!-- mc:protected START invariant -->\n"
            "MUTATED BY CHILD\n<!-- mc:protected END -->\n\n"
            "Current core lives at `src/core.py`.\n",
            encoding="utf-8",
        )
        structure.write_text(
            "# Codebase Structure\n\nCore implementation: `src/core.py`.\n",
            encoding="utf-8",
        )
        return {"text": "Documentation synchronized.", "tool_history": []}

    with RuntimeClient(
        db_path=tmp_path / "maintain-docs.db",
        timeout=60,
        callback_handler=callback,
    ) as client:
        client.call(
            "bind",
            {"session_id": "maintain-docs", "project_root": str(tmp_path)},
            timeout=60,
        )
        result = client.call(
            "dreamer_run_manual",
            {"session_id": "maintain-docs", "task": "maintain-docs"},
            timeout=120,
        )

    assert result["failed"] == [], result.get("failureDetails") or result
    assert result["ran"] == ["maintain-docs"]
    architecture_text = architecture.read_text(encoding="utf-8")
    structure_text = structure.read_text(encoding="utf-8")
    assert protected in architecture_text
    assert "MUTATED BY CHILD" not in architecture_text
    assert "Current core lives at `src/core.py`." in architecture_text
    assert "Core implementation: `src/core.py`." in structure_text


def test_smart_note_compiles_and_surfaces_through_upstream_sandbox(tmp_path):
    (tmp_path / "READY.txt").write_text("READY\n", encoding="utf-8")
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "dreamer": {
                    "tasks": {"evaluate-smart-notes": {"schedule": ""}}
                },
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "smart-notes.db"

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        assert "smart-note-compile" in str(params.get("title", ""))
        return {
            "text": json.dumps(
                {
                    "compiled_check": (
                        "function check(cap) { const value = "
                        'cap.readFile("READY.txt"); '
                        'return { met: value !== null && value.includes("READY") }; }'
                    ),
                    "manifest": {
                        "capabilities": ["readFile"],
                        "readFiles": ["READY.txt"],
                        "hosts": [],
                        "urls": [],
                        "signals": ["READY.txt contains READY"],
                        "summary": "READY.txt contains READY",
                    },
                    "check_cron": "*/15 * * * *",
                }
            ),
            "tool_history": [],
        }

    with RuntimeClient(
        db_path=db_path, timeout=60, callback_handler=callback
    ) as client:
        client.call(
            "bind",
            {"session_id": "smart-notes", "project_root": str(tmp_path)},
            timeout=60,
        )
        written = client.call(
            "tool",
            {
                "session_id": "smart-notes",
                "name": "ctx_note",
                "arguments": {
                    "action": "write",
                    "content": "The READY marker is now actionable.",
                    "surface_condition": "When READY.txt contains READY",
                },
            },
            timeout=60,
        )
        assert written["is_error"] is False
        result = client.call(
            "dreamer_run_manual",
            {"session_id": "smart-notes", "task": "evaluate-smart-notes"},
            timeout=120,
        )
        assert result["failed"] == [], result.get("failureDetails") or result
        assert result["ran"] == ["evaluate-smart-notes"]
        read = client.call(
            "tool",
            {
                "session_id": "smart-notes",
                "name": "ctx_note",
                "arguments": {"action": "read", "filter": "ready"},
            },
            timeout=60,
        )

    assert read["is_error"] is False
    assert "READY marker is now actionable" in read["text"]
    with sqlite3.connect(db_path) as conn:
        note = conn.execute(
            "SELECT type, status, check_status, compiled_check, ready_reason "
            "FROM notes WHERE id = 1"
        ).fetchone()
    assert note[0] == "smart"
    assert note[1] == "ready"
    assert note[2] == "compiled"
    assert "function check(cap)" in note[3]
    assert "compiled check returned met=true" in note[4]


def test_upstream_dreamer_maps_verifies_and_classifies_memories(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.py").write_text(
        "def current_rule():\n    return 'mapped-and-verified'\n", encoding="utf-8"
    )
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "dreamer": {
                    "tasks": {
                        "map-memories": {"schedule": ""},
                        "verify": {"schedule": ""},
                        "verify-broad": {"schedule": ""},
                        "classify-memories": {"schedule": ""},
                    }
                },
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "dreamer-memory-pipeline.db"

    def ids_from_prompt(prompt):
        return [int(value) for value in re.findall(r"^\[(\d+)\] ", prompt, re.M)]

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        title = str(params.get("title", ""))
        ids = ids_from_prompt(params.get("prompt", ""))
        assert ids
        if "map-memories" in title:
            body = "".join(
                f'<memory id="{memory_id}" files="src/sample.py"/>'
                for memory_id in ids
            )
            text = f"<mappings>{body}</mappings>"
        elif "dream-verify" in title:
            body = "".join(
                f'<verified id="{memory_id}" files="src/sample.py"/>'
                for memory_id in ids
            )
            text = f"<verify>{body}</verify>"
        elif "dream-classify" in title:
            body = "".join(
                (
                    f'<memory id="{memory_id}" importance="82" '
                    'scope="project" shareable="true"/>'
                )
                for memory_id in ids
            )
            text = f"<classify>{body}</classify>"
        else:
            raise AssertionError(title)
        return {"text": text, "tool_history": []}

    with RuntimeClient(
        db_path=db_path, timeout=60, callback_handler=callback
    ) as client:
        client.call(
            "bind",
            {"session_id": "dream-memory", "project_root": str(tmp_path)},
            timeout=60,
        )
        for index in range(10):
            written = client.call(
                "tool",
                {
                    "session_id": "dream-memory",
                    "name": "ctx_memory",
                    "arguments": {
                        "action": "write",
                        "category": "ARCHITECTURE",
                        "content": (
                            "src/sample.py implements current_rule for "
                            f"mapped-and-verified behavior variant {index}."
                        ),
                    },
                },
                timeout=60,
            )
            assert written["is_error"] is False

        for task in (
            "map-memories",
            "verify",
            "verify-broad",
            "classify-memories",
        ):
            result = client.call(
                "dreamer_run_manual",
                {"session_id": "dream-memory", "task": task},
                timeout=120,
            )
            assert result["failed"] == [], result
            assert result["ran"] == [task], result

    with sqlite3.connect(db_path) as conn:
        memories = conn.execute(
            "SELECT id, importance, scope, shareable, classified_at "
            "FROM memories ORDER BY id"
        ).fetchall()
        verifications = conn.execute(
            "SELECT memory_id, file_path, verified_at "
            "FROM memory_verifications ORDER BY memory_id"
        ).fetchall()
        runs = conn.execute("SELECT tasks_json FROM dream_runs").fetchall()
    assert len(memories) == 10
    assert len(verifications) == 10
    assert all(row[1] == "src/sample.py" and row[2] > 0 for row in verifications)
    assert all(
        row[1] == 82 and row[2] == "project" and row[3] == 1 and row[4] is not None
        for row in memories
    )
    assert len(runs) >= 4


def test_historian_candidates_flow_through_user_memory_and_primer_dreamer(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.py").write_text(
        "def durable_answer():\n    return 'primer-refresh-grounding'\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "historian": {"two_pass": False},
                "dreamer": {
                    "tasks": {
                        # Non-empty schedule is the upstream opt-in for historian
                        # user-observation collection; manual execution is still
                        # used below for deterministic test timing.
                        "review-user-memories": {
                            "schedule": "0 3 * * *",
                            "promotion_threshold": 3,
                        },
                        "promote-primers": {
                            "schedule": "",
                            "promotion_threshold": 2,
                        },
                        "refresh-primers": {"schedule": ""},
                    }
                },
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "learning-pipeline.db"
    profile_memory = "The user prefers concise, evidence-backed completion claims."
    primer_question = (
        "How does the runtime adapter preserve Magic Context lifecycle parity?"
    )

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        title = str(params.get("title", ""))
        prompt = str(params.get("prompt", ""))
        if "dream-user-memories" in title:
            candidate_ids = [
                int(value) for value in re.findall(r"Candidate #(\d+)", prompt)
            ]
            assert len(candidate_ids) >= 3
            return {
                "text": json.dumps(
                    {
                        "promote": [
                            {
                                "content": profile_memory,
                                "candidate_ids": candidate_ids,
                            }
                        ],
                        "update_existing": [],
                        "dismiss_existing": [],
                        "consume_candidate_ids": candidate_ids,
                    }
                ),
                "tool_history": [],
            }
        if "refresh-primers" in title:
            assert "runtime adapter preserve Magic Context lifecycle parity" in prompt
            return {
                "text": json.dumps(
                    {
                        "answer": (
                            "src/sample.py defines durable_answer and returns "
                            "primer-refresh-grounding."
                        )
                    }
                ),
                # Upstream requires proof that the investigator actually used a
                # read/search tool before it commits a refreshed answer.
                "tool_history": [
                    {
                        "tool_name": "read_file",
                        "tool_input": {"path": "src/sample.py"},
                        "input_bytes": 24,
                        "output_bytes": 64,
                        "status": "ok",
                    }
                ],
            }
        raise AssertionError(title)

    with RuntimeClient(
        db_path=db_path, timeout=60, callback_handler=callback
    ) as client:
        for session_index in range(3):
            session_id = f"learning-{session_index}"
            client.call(
                "bind",
                {"session_id": session_id, "project_root": str(tmp_path)},
                timeout=60,
            )
            # Primer promotion additionally requires a seven-day observation
            # span, independent of its candidate-count threshold.
            base_ms = 1_780_000_000_000 + session_index * 8 * 86_400_000
            messages = [{"role": "system", "content": "System."}]
            for index in range(12):
                messages.append(
                    {
                        "role": "user" if index % 2 == 0 else "assistant",
                        "content": f"Learning session {session_index} message {index}.",
                        "timestamp": base_ms + index * 1000,
                    }
                )
            prepared = client.call(
                "historian_prepare",
                {
                    "session_id": session_id,
                    "messages": messages,
                    "protect_last_n": 2,
                    "history_budget_tokens": 8_000,
                },
                timeout=60,
            )
            assert prepared["ready"] is True
            start = prepared["chunk"]["start"]
            end = prepared["chunk"]["end"]
            output = f"""<output>
<compartments>
<compartment start="{start}" end="{end}" title="Learning pipeline"
 episode_type="implementation" importance="80">
<p1>Observed durable workflow behavior and the runtime adapter.</p1>
<p2>Workflow evidence captured.</p2><p3>Evidence captured.</p3><p4>Captured.</p4>
</compartment>
</compartments>
<facts></facts><events></events>
<user_observations>
* The user prefers concise, evidence-backed completion claims.
</user_observations>
<primer_candidates>
<primer at_compartment="1">{primer_question}</primer>
</primer_candidates>
<meta>
<messages_processed>{start}-{end}</messages_processed>
<unprocessed_from>{end + 1}</unprocessed_from>
</meta>
</output>"""
            published = client.call(
                "historian_publish",
                {"session_id": session_id, "output": output},
                timeout=60,
            )
            assert published["ok"] is True

        for task in ("review-user-memories", "promote-primers", "refresh-primers"):
            result = client.call(
                "dreamer_run_manual",
                {"session_id": "learning-2", "task": task},
                timeout=120,
            )
            assert result["failed"] == [], result.get("failureDetails") or result
            assert result["ran"] == [task], result

    with sqlite3.connect(db_path) as conn:
        candidates = conn.execute(
            "SELECT COUNT(*) FROM user_memory_candidates"
        ).fetchone()[0]
        user_memories = conn.execute(
            "SELECT content, status FROM user_memories"
        ).fetchall()
        primers = conn.execute(
            "SELECT question, answer, answer_refreshed_at, source_candidate_ids "
            "FROM primers"
        ).fetchall()
    assert candidates == 0
    assert user_memories == [(profile_memory, "active")]
    assert len(primers) == 1
    assert "runtime adapter preserve Magic Context lifecycle parity" in primers[0][0]
    assert "primer-refresh-grounding" in primers[0][1]
    assert primers[0][2] is not None
    assert len(json.loads(primers[0][3])) >= 2


def test_upstream_mural_cues_render_into_m0_for_vision_model(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    model_cache_dir = data_root / "cortexkit" / "magic-context"
    model_cache_dir.mkdir(parents=True)
    (model_cache_dir / "model-context-limits-hermes.json").write_text(
        json.dumps(
            {
                "openai/gpt-4o": {
                    "contextLimit": 128_000,
                    "outputLimit": 16_384,
                    "vision": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAGIC_CONTEXT_TEST_DATA_DIR", str(data_root))
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        json.dumps(
            {
                "mural": {"enabled": True},
                "dreamer": {
                    "model": "openai/gpt-4o",
                    "tasks": {"compress-cues": {"schedule": ""}},
                },
                "memory": {"injection_budget_tokens": 500},
                "embedding": {"provider": "off"},
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "mural.db"

    def callback(method, params):
        if method == "dreamer_child_abort":
            return {"accepted": True}
        assert method == "dreamer_child_prompt"
        assert "compress-cues" in str(params.get("title", ""))
        ids = [
            int(value)
            for value in re.findall(r"^\[(\d+)\] ", params["prompt"], re.M)
        ]
        assert ids
        body = "".join(
            f'<cue id="{memory_id}">rule{memory_id} → anchor</cue>'
            for memory_id in ids
        )
        return {"text": f"<cues>{body}</cues>", "tool_history": []}

    with RuntimeClient(
        db_path=db_path, timeout=60, callback_handler=callback
    ) as client:
        client.call(
            "bind", {"session_id": "mural", "project_root": str(tmp_path)}, timeout=60
        )
        client.call(
            "model_update",
            {
                "session_id": "mural",
                "provider": "openai",
                "model": "gpt-4o",
                "context_length": 128_000,
            },
            timeout=30,
        )
        # Enough large memories to overflow the text memory budget and satisfy
        # upstream's mural coverage gate after compress-cues.
        for index in range(20):
            result = client.call(
                "tool",
                {
                    "session_id": "mural",
                    "name": "ctx_memory",
                    "arguments": {
                        "action": "write",
                        "category": "PROJECT_RULES",
                        "content": (
                            f"Mural memory {index} requires rule-{index}. "
                            + (f"detail-{index} " * 80)
                        ),
                    },
                },
                timeout=60,
            )
            assert result["is_error"] is False

        dreamed = client.call(
            "dreamer_run_manual",
            {"session_id": "mural", "task": "compress-cues"},
            timeout=120,
        )
        assert dreamed["failed"] == []
        assert dreamed["ran"] == ["compress-cues"]

        rendered = client.call(
            "render_context",
            {
                "session_id": "mural",
                "messages": [
                    {"role": "system", "content": "System."},
                    {"role": "user", "content": "Show the project context."},
                ],
                "history_budget_tokens": 8_000,
            },
            timeout=60,
        )

    image_urls = []
    for message in rendered["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "image_url":
                image_urls.append(part.get("image_url", {}).get("url", ""))
    assert any(url.startswith("data:image/png;base64,") for url in image_urls)
    with sqlite3.connect(db_path) as conn:
        manifest = conn.execute(
            "SELECT model, width, height FROM mural_manifest LIMIT 1"
        ).fetchone()
        cued = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE mural_cue IS NOT NULL"
        ).fetchone()[0]
    assert manifest is not None
    assert manifest[0] == "deterministic"
    assert manifest[1] > 0 and manifest[2] > 0
    assert cued == 20


def test_live_tail_branch_rewrite_reconciles_message_index(tmp_path):
    (tmp_path / ".cortexkit").mkdir()
    (tmp_path / ".cortexkit" / "magic-context.jsonc").write_text(
        "{}", encoding="utf-8"
    )
    db_path = tmp_path / "branch-tail.db"
    original = [
        {"role": "system", "content": "System."},
        {"role": "user", "content": "Branch start."},
        {"role": "assistant", "content": "Branch answer one."},
        {"role": "user", "content": "Branch middle."},
        {"role": "assistant", "content": "Branch answer two."},
        {"role": "user", "content": "OBSOLETE-BRANCH-MARKER"},
        {"role": "assistant", "content": "Obsolete branch answer."},
    ]
    replacement = [
        *original[:5],
        {"role": "user", "content": "REPLACEMENT-BRANCH-MARKER"},
        {"role": "assistant", "content": "Replacement branch answer."},
    ]

    with RuntimeClient(db_path=db_path, timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "branch-tail", "project_root": str(tmp_path)},
            timeout=60,
        )
        first = client.call(
            "observe", {"session_id": "branch-tail", "messages": original}
        )
        second = client.call(
            "observe", {"session_id": "branch-tail", "messages": replacement}
        )

    assert first["entry_count"] == 6
    assert second["entry_count"] == 6
    assert second["last_indexed_ordinal"] == 6
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT content FROM message_history_fts WHERE session_id = ?",
            ("branch-tail",),
        ).fetchall()
    corpus = "\n".join(row[0] for row in rows)
    assert "REPLACEMENT-BRANCH-MARKER" in corpus
    assert "OBSOLETE-BRANCH-MARKER" not in corpus


def test_rewind_into_compacted_history_invalidates_session_derived_state(tmp_path):
    config_dir = tmp_path / ".cortexkit"
    config_dir.mkdir()
    (config_dir / "magic-context.jsonc").write_text(
        '{"historian":{"two_pass":false}}', encoding="utf-8"
    )
    db_path = tmp_path / "branch-compacted.db"
    original = conversation(16)

    with RuntimeClient(db_path=db_path, timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "branch-compacted", "project_root": str(tmp_path)},
            timeout=60,
        )
        memory = client.call(
            "tool",
            {
                "session_id": "branch-compacted",
                "name": "ctx_memory",
                "arguments": {
                    "action": "write",
                    "category": "PROJECT_RULES",
                    "content": "Project memory survives a session branch rewind.",
                },
            },
        )
        assert memory["is_error"] is False
        prepared = client.call(
            "historian_prepare",
            {
                "session_id": "branch-compacted",
                "messages": original,
                "protect_last_n": 2,
                "history_budget_tokens": 8_000,
            },
            timeout=60,
        )
        assert prepared["ready"] is True
        published = client.call(
            "historian_publish",
            {
                "session_id": "branch-compacted",
                "output": historian_xml(
                    prepared["chunk"]["start"], prepared["chunk"]["end"]
                ),
            },
            timeout=60,
        )
        assert published["ok"] is True
        assert published["compartments_added"] == 1

        replacement = [dict(message) for message in original]
        replacement[3] = {
            "role": replacement[3]["role"],
            "content": "REWOUND-COMPACTED-BRANCH-MARKER",
        }
        observed = client.call(
            "observe",
            {"session_id": "branch-compacted", "messages": replacement},
            timeout=60,
        )
        memory_after = client.call(
            "memory_context", {"session_id": "branch-compacted"}, timeout=60
        )

    assert observed["entry_count"] == 16
    assert observed["last_indexed_ordinal"] == 16
    assert "Project memory survives" in memory_after["text"]
    with sqlite3.connect(db_path) as conn:
        compartments = conn.execute(
            "SELECT COUNT(*) FROM compartments WHERE session_id = ?",
            ("branch-compacted",),
        ).fetchone()[0]
        corpus = "\n".join(
            row[0]
            for row in conn.execute(
                "SELECT content FROM message_history_fts WHERE session_id = ?",
                ("branch-compacted",),
            ).fetchall()
        )
    assert compartments == 0
    assert "REWOUND-COMPACTED-BRANCH-MARKER" in corpus
    assert "amber-2" not in corpus


def test_upstream_config_resolver_merges_user_and_project_config(monkeypatch, tmp_path):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    user_path = user_dir / "magic-context.jsonc"
    user_path.write_text(
        """{
          "execute_threshold_percentage": 60,
          "cache_ttl": "10m",
          "historian": {"model": "openai/gpt-user", "two_pass": false}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))

    project = tmp_path / "project"
    project_config_dir = project / ".cortexkit"
    project_config_dir.mkdir(parents=True)
    project_path = project_config_dir / "magic-context.jsonc"
    project_path.write_text(
        """{
          "execute_threshold_percentage": 72,
          "historian": {"model": "openai/gpt-project", "two_pass": true}
        }""",
        encoding="utf-8",
    )

    with RuntimeClient(db_path=tmp_path / "merge.db", timeout=60) as client:
        bound = client.call(
            "bind",
            {"session_id": "config-merge", "project_root": str(project)},
            timeout=60,
        )

    assert set(bound["config_loaded_from"]) == {str(user_path), str(project_path)}
    assert bound["config"]["execute_threshold_percentage"] == 72
    assert bound["config"]["cache_ttl"] == "10m"
    # Upstream deliberately keeps historian model selection user-only so a
    # cloned repository cannot route private history to another model/provider.
    assert bound["config"]["historian_model"] == "openai/gpt-user"
    assert bound["config"]["historian_two_pass"] is True
    assert any(
        "Ignoring historian.model from project config" in warning
        for warning in bound["config_warnings"]
    )


def test_ctx_reduce_uses_upstream_tags_and_cache_safe_materialization(
    monkeypatch, tmp_path
):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "execute_threshold_percentage": 65,
          "cache_ttl": "5m",
          "protected_tags": 5
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    messages = conversation(12)

    with RuntimeClient(db_path=tmp_path / "reduce.db", timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "reduce", "project_root": str(project)},
            timeout=60,
        )
        client.call(
            "model_update",
            {
                "session_id": "reduce",
                "model": "gpt-5",
                "provider": "openai",
                "context_length": 10_000,
            },
        )
        client.call(
            "usage_update",
            {"session_id": "reduce", "input_tokens": 1_000, "context_length": 10_000},
        )
        initial = client.call(
            "render_context",
            {
                "session_id": "reduce",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
        )
        assert initial["scheduler_decision"] == "defer"
        first_user = next(
            message
            for message in initial["messages"]
            if "Runtime integration message 0" in str(message.get("content", ""))
        )
        assert first_user["content"].startswith("§1§ ")

        queued = client.call(
            "tool",
            {
                "session_id": "reduce",
                "name": "ctx_reduce",
                "arguments": {"drop": "1"},
                "messages": messages,
            },
        )
        assert queued["is_error"] is False
        assert "Queued" in queued["text"]

        deferred = client.call(
            "render_context",
            {
                "session_id": "reduce",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
        )
        assert deferred["scheduler_decision"] == "defer"
        assert deferred["pending_ops"] == 1
        deferred_user = next(
            message
            for message in deferred["messages"]
            if "Runtime integration message 0" in str(message.get("content", ""))
        )
        assert deferred_user["content"].startswith("§1§ ")

        client.call(
            "usage_update",
            {"session_id": "reduce", "input_tokens": 7_000, "context_length": 10_000},
        )
        executed = client.call(
            "render_context",
            {
                "session_id": "reduce",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
        )
        assert executed["scheduler_decision"] == "execute"
        assert executed["pending_ops"] == 0
        assert executed["transformed"] is True
        assert any(
            message.get("content") == "[dropped §1§]"
            for message in executed["messages"]
        )

        replayed = client.call(
            "render_context",
            {
                "session_id": "reduce",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
        )
        assert any(
            message.get("content") == "[dropped §1§]"
            for message in replayed["messages"]
        )


def test_prompt_guidance_uses_upstream_shared_config_and_replays_once(
    monkeypatch, tmp_path
):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "embedding": {"provider": "off"},
          "temporal_awareness": true
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    messages = [
        {"role": "system", "content": "Base Hermes system prompt."},
        {"role": "user", "content": "Use the shared prompt policy."},
    ]

    with RuntimeClient(db_path=tmp_path / "guidance.db", timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "guidance", "project_root": str(project)},
            timeout=60,
        )
        first = client.call(
            "render_context",
            {
                "session_id": "guidance",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )
        replay = client.call(
            "render_context",
            {
                "session_id": "guidance",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )

    first_system = first["messages"][0]["content"]
    replay_system = replay["messages"][0]["content"]
    assert "Base Hermes system prompt." in first_system
    assert "## Magic Context" in first_system
    assert "Temporal awareness" in first_system
    assert first_system.count("## Magic Context") == 1
    assert replay_system.count("## Magic Context") == 1


def test_prompt_guidance_respects_upstream_skip_signature(monkeypatch, tmp_path):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{"embedding": {"provider": "off"}}""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    messages = [
        {
            "role": "system",
            "content": "Base prompt. <!-- magic-context: skip -->",
        },
        {"role": "user", "content": "Do not inject MC guidance."},
    ]

    with RuntimeClient(db_path=tmp_path / "skip.db", timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "skip-guidance", "project_root": str(project)},
            timeout=60,
        )
        rendered = client.call(
            "render_context",
            {
                "session_id": "skip-guidance",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )

    assert "## Magic Context" not in rendered["messages"][0]["content"]


def test_temporal_awareness_uses_upstream_gap_markers(monkeypatch, tmp_path):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "embedding": {"provider": "off"},
          "temporal_awareness": true
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    messages = [
        {
            "role": "user",
            "content": "First temporal message.",
            "timestamp": "2026-08-19T12:00:00-07:00",
        },
        {
            "role": "assistant",
            "content": "Acknowledged.",
            "timestamp": "2026-08-19T12:00:10-07:00",
        },
        {
            "role": "user",
            "content": "Second temporal message.",
            "timestamp": "2026-08-19T12:17:10-07:00",
        },
    ]

    with RuntimeClient(db_path=tmp_path / "temporal.db", timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "temporal", "project_root": str(project)},
            timeout=60,
        )
        first = client.call(
            "render_context",
            {
                "session_id": "temporal",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )
        replay = client.call(
            "render_context",
            {
                "session_id": "temporal",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )

    temporal = next(
        message
        for message in first["messages"]
        if "Second temporal message." in str(message.get("content", ""))
    )
    assert temporal["content"].startswith("§3§ <!-- +17m -->")
    replayed = next(
        message
        for message in replay["messages"]
        if "Second temporal message." in str(message.get("content", ""))
    )
    assert replayed["content"].count("<!-- +17m -->") == 1


def test_compaction_off_keeps_knowledge_layer_without_context_mutation(
    monkeypatch, tmp_path
):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "compaction": {"enabled": false},
          "embedding": {"provider": "off"},
          "memory": {"enabled": true}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "Compaction-off knowledge-layer request."},
    ]

    with RuntimeClient(db_path=tmp_path / "off.db", timeout=60) as client:
        bound = client.call(
            "bind",
            {"session_id": "compaction-off", "project_root": str(project)},
            timeout=60,
        )
        written = client.call(
            "tool",
            {
                "session_id": "compaction-off",
                "name": "ctx_memory",
                "arguments": {
                    "action": "write",
                    "category": "PROJECT_RULES",
                    "content": "Compaction-off memory must remain injected.",
                },
                "messages": [],
            },
            timeout=60,
        )
        assert written["is_error"] is False
        rendered = client.call(
            "render_context",
            {
                "session_id": "compaction-off",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )
        decision = client.call(
            "historian_decide",
            {"session_id": "compaction-off", "messages": messages},
            timeout=60,
        )
        doctor = client.call(
            "doctor", {"session_id": "compaction-off"}, timeout=60
        )

    tool_names = {schema["name"] for schema in bound["tool_schemas"]}
    assert bound["config"]["compaction_enabled"] is False
    rendered_text = "\n".join(
        str(message.get("content", "")) for message in rendered["messages"]
    )
    assert "Compaction-off memory must remain injected." in rendered_text
    assert "<project-memory>" in rendered_text
    assert all(
        "<session-history>" not in str(message.get("content", ""))
        for message in rendered["messages"]
        if message.get("role") != "system"
    )
    real_user = next(
        message
        for message in rendered["messages"]
        if "Compaction-off knowledge-layer request." in str(message.get("content", ""))
    )
    assert real_user["content"] == "Compaction-off knowledge-layer request."
    assert all(
        "§" not in str(message.get("content", ""))
        for message in rendered["messages"]
        if message.get("role") != "system"
    )
    assert rendered["scheduler_decision"] == "disabled"
    assert "ctx_reduce" not in tool_names
    assert "ctx_search" in tool_names
    assert decision == {"should_fire": False, "reason": "compaction-disabled"}
    assert doctor["database_health"] == "ok"
    assert doctor["core_symbols_ready"] is True
    assert doctor["session_bound"] is True


def test_auto_search_uses_upstream_memory_hint_without_self_matching(
    monkeypatch, tmp_path
):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "embedding": {"provider": "off"},
          "memory": {
            "auto_search": {
              "enabled": true,
              "score_threshold": 0.3,
              "min_prompt_chars": 5
            }
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    db_path = tmp_path / "auto-search.db"

    with RuntimeClient(db_path=db_path, timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "auto-search", "project_root": str(project)},
            timeout=60,
        )
        written = client.call(
            "tool",
            {
                "session_id": "auto-search",
                "name": "ctx_memory",
                "arguments": {
                    "action": "write",
                    "category": "PROJECT_RULES",
                    "content": (
                        "silver narwhal deployment release gate checksum "
                        "verification requires signed release manifest"
                    ),
                },
                "messages": [],
            },
            timeout=60,
        )
        assert written["is_error"] is False

        messages = [
            {
                "role": "user",
                "content": (
                    "silver narwhal deployment release gate checksum verification"
                ),
            }
        ]
        rendered = client.call(
            "render_context",
            {
                "session_id": "auto-search",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )
        content = rendered["messages"][-1]["content"]
        assert "<ctx-search-hint>" in content
        assert "requires signed" in content

        replayed = client.call(
            "render_context",
            {
                "session_id": "auto-search",
                "messages": messages,
                "history_budget_tokens": 2_000,
            },
            timeout=60,
        )
        assert replayed["messages"][-1]["content"].count("<ctx-search-hint>") == 1


def test_embedding_pipeline_produces_and_rotates_upstream_vectors(
    monkeypatch, tmp_path
):
    requests = []

    class EmbeddingHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            requests.append(body)
            inputs = body.get("input") or []
            data = []
            for index, text in enumerate(inputs):
                seed = float((sum(ord(ch) for ch in str(text)) % 97) + 1)
                data.append(
                    {
                        "index": index,
                        "embedding": [seed / 100.0, 0.25, 0.5, 0.75],
                    }
                )
            payload = json.dumps({"model": body["model"], "data": data}).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        user_home = tmp_path / "xdg"
        user_dir = user_home / "cortexkit"
        user_dir.mkdir(parents=True)
        config_path = user_dir / "magic-context.jsonc"
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
        endpoint = f"http://127.0.0.1:{server.server_port}"

        def write_config(model):
            config_path.write_text(
                json.dumps(
                    {
                        "embedding": {
                            "provider": "openai-compatible",
                            "endpoint": endpoint,
                            "model": model,
                        },
                        "historian": {"model": "gpt-5.5", "two_pass": False},
                    }
                ),
                encoding="utf-8",
            )

        db_path = tmp_path / "embeddings.db"
        write_config("test-embedding-a")
        messages = conversation(30)
        for message in messages[1:]:
            message["content"] += " " + ("embedding-history-payload " * 80)
        with RuntimeClient(db_path=db_path, timeout=60) as client:
            client.call(
                "bind",
                {"session_id": "embed", "project_root": str(project)},
                timeout=60,
            )
            written = client.call(
                "tool",
                {
                    "session_id": "embed",
                    "name": "ctx_memory",
                    "arguments": {
                        "action": "write",
                        "category": "ARCHITECTURE",
                        "content": (
                            "Embedding rotation keeps one coherent vector space."
                        ),
                    },
                },
                timeout=60,
            )
            assert written["is_error"] is False
            prepared = client.call(
                "historian_prepare",
                {
                    "session_id": "embed",
                    "messages": messages,
                    "protect_last_n": 2,
                    "history_budget_tokens": 8_000,
                },
                timeout=60,
            )
            assert prepared["ready"] is True, prepared
            published = client.call(
                "historian_publish",
                {
                    "session_id": "embed",
                    "output": historian_xml(
                        prepared["chunk"]["start"], prepared["chunk"]["end"]
                    ),
                },
                timeout=60,
            )
            assert published["ok"] is True
            maintenance = client.call(
                "maintenance_run", {"session_id": "embed"}, timeout=120
            )
            assert maintenance["embedding_drain"] is True

        with sqlite3.connect(db_path) as conn:
            memory_a = conn.execute(
                "SELECT model_id, length(embedding) FROM memory_embeddings"
            ).fetchall()
            chunks_a = conn.execute(
                "SELECT model_id, dims, length(vector) "
                "FROM compartment_chunk_embeddings"
            ).fetchall()
        assert len(memory_a) == 1 and memory_a[0][1] > 0
        assert chunks_a and all(row[1] == 4 and row[2] > 0 for row in chunks_a)
        model_id_a = memory_a[0][0]

        # A shared-config model change must create the new generation and GC
        # stale vectors instead of mixing embedding spaces.
        write_config("test-embedding-b")
        with RuntimeClient(db_path=db_path, timeout=60) as restarted:
            restarted.call(
                "bind",
                {"session_id": "embed", "project_root": str(project)},
                timeout=60,
            )
            restarted.call(
                "maintenance_run", {"session_id": "embed"}, timeout=120
            )

        with sqlite3.connect(db_path) as conn:
            memory_b = conn.execute(
                "SELECT model_id, length(embedding) FROM memory_embeddings"
            ).fetchall()
            chunk_models = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT model_id FROM compartment_chunk_embeddings"
                ).fetchall()
            }
            active = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT scope, model_id FROM embedding_identity_active"
                ).fetchall()
            }
        # MC intentionally retains the previous vector generation for a 14-day
        # grace period. Parity means switching the active identity immediately,
        # not eagerly deleting a potentially useful rollback generation.
        assert len(memory_b) == 2 and all(row[1] > 0 for row in memory_b)
        memory_models = {row[0] for row in memory_b}
        assert model_id_a in memory_models
        assert active["memory"] in memory_models
        assert active["memory"] != model_id_a
        assert active["chunk"] in chunk_models
        assert requests
        assert {request["model"] for request in requests} >= {
            "test-embedding-a",
            "test-embedding-b",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_upstream_git_maintenance_indexes_commits(monkeypatch, tmp_path):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "embedding": {"provider": "off"},
          "memory": {
            "git_commit_indexing": {
              "enabled": true,
              "since_days": 365,
              "max_commits": 100
            }
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "magic-hermes@example.com"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Magic Hermes"],
        cwd=project,
        check=True,
    )
    (project / "README.md").write_text("git maintenance e2e\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=project, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "silver narwhal checksum release gate"],
        cwd=project,
        check=True,
    )
    db_path = tmp_path / "git.db"

    with RuntimeClient(db_path=db_path, timeout=60) as client:
        client.call(
            "bind",
            {"session_id": "git-maintenance", "project_root": str(project)},
            timeout=60,
        )
        maintenance = client.call(
            "maintenance_run",
            {"session_id": "git-maintenance"},
            timeout=120,
        )

    assert maintenance["git_sweep"] is True
    # Provider "off" intentionally disables MC's git search surface, but the
    # upstream sweep still owns durable indexing and FTS population.
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT message FROM git_commits LIMIT 1"
        ).fetchone()
        fts = db.execute(
            "SELECT message FROM git_commits_fts LIMIT 1"
        ).fetchone()
    assert row == ("silver narwhal checksum release gate",)
    assert fts == ("silver narwhal checksum release gate",)


def test_upstream_historian_trigger_and_compartment_lease(monkeypatch, tmp_path):
    user_home = tmp_path / "xdg"
    user_dir = user_home / "cortexkit"
    user_dir.mkdir(parents=True)
    (user_dir / "magic-context.jsonc").write_text(
        """{
          "execute_threshold_percentage": 65,
          "cache_ttl": "5m",
          "historian": {"model": "gpt-5.5"}
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_home))
    project = tmp_path / "project"
    project.mkdir()
    db_path = tmp_path / "lease.db"
    messages = conversation(30)
    for message in messages[1:]:
        message["content"] += " " + ("trigger-payload " * 80)

    with RuntimeClient(db_path=db_path, timeout=60) as first:
        first.call(
            "bind",
            {"session_id": "lease", "project_root": str(project)},
            timeout=60,
        )
        first.call(
            "model_update",
            {
                "session_id": "lease",
                "model": "gpt-5",
                "provider": "openai",
                "context_length": 10_000,
            },
        )
        first.call(
            "usage_update",
            {"session_id": "lease", "input_tokens": 7_000, "context_length": 10_000},
        )
        decision = first.call(
            "historian_decide",
            {"session_id": "lease", "messages": messages},
            timeout=60,
        )
        assert decision["should_fire"] is True
        assert isinstance(decision["boundary_snapshot"], dict)

        prepared = first.call(
            "historian_prepare",
            {
                "session_id": "lease",
                "messages": messages,
                "boundary_snapshot": decision["boundary_snapshot"],
                "history_budget_tokens": 8_000,
                "holder_id": "first-worker",
            },
            timeout=60,
        )
        assert prepared["ready"] is True

        with RuntimeClient(db_path=db_path, timeout=60) as second:
            second.call(
                "bind",
                {"session_id": "lease", "project_root": str(project)},
                timeout=60,
            )
            blocked = second.call(
                "historian_prepare",
                {
                    "session_id": "lease",
                    "messages": messages,
                    "boundary_snapshot": decision["boundary_snapshot"],
                    "history_budget_tokens": 8_000,
                    "holder_id": "second-worker",
                },
                timeout=60,
            )
            assert blocked == {"ready": False, "reason": "lease-held"}

        first.call("historian_abort", {"session_id": "lease"})
