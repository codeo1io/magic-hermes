from __future__ import annotations

import json

from magic_hermes.memory_provider import MagicContextMemoryProvider


class FakeClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    def call(self, method, params=None, *, timeout=None):
        self.calls.append((method, params or {}, timeout))
        if method == "bind":
            return {
                "tool_schemas": [
                    {"name": "ctx_search", "description": "search", "parameters": {}},
                    {"name": "ctx_memory", "description": "memory", "parameters": {}},
                ]
            }
        if method == "memory_context":
            return {"text": "<project-memory>#1: rule</project-memory>", "count": 1}
        if method == "tool":
            return {"text": "Saved memory [ID: 2].", "is_error": False}
        return {}

    def close(self):
        self.closed = True


def test_provider_binds_and_leaves_tools_to_context_engine(tmp_path):
    client = FakeClient()
    provider = MagicContextMemoryProvider(client=client, project_root=tmp_path)

    provider.initialize("memory-session", cwd=str(tmp_path))

    assert provider.name == "magic_context"
    assert provider.get_tool_schemas() == []
    # The ContextEngine owns upstream m[0]/m[1] rendering, so the MemoryProvider
    # tracks recall status but must not inject a duplicate memory block.
    assert provider.prefetch("anything") == ""
    assert provider._last_recall_count == 1


def test_provider_dispatches_official_memory_tool(tmp_path):
    client = FakeClient()
    provider = MagicContextMemoryProvider(client=client, project_root=tmp_path)
    provider.initialize("memory-tools", cwd=str(tmp_path))

    result = json.loads(
        provider.handle_tool_call(
            "ctx_memory",
            {
                "action": "write",
                "category": "PROJECT_RULES",
                "content": "Use deterministic ids.",
            },
        )
    )

    assert result == {"content": "Saved memory [ID: 2]."}
    call = next(item for item in client.calls if item[0] == "tool")
    assert call[1]["session_id"] == "memory-tools"


def test_provider_shutdown_owns_only_its_client(tmp_path):
    client = FakeClient()
    provider = MagicContextMemoryProvider(client=client, project_root=tmp_path)

    provider.shutdown()
    call_count = len(client.calls)
    provider.queue_prefetch("ignored after shutdown")

    assert client.closed is True
    assert len(client.calls) == call_count
