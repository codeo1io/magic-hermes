from __future__ import annotations

from types import SimpleNamespace

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


def test_zai_model_ref_uses_hermes_active_provider_shape():
    assert plugin._model_for_hermes("zai/glm-4.7") == "glm-4.7"
    assert plugin._model_for_hermes("vendor/model") == "vendor/model"


def test_plugin_registers_engine_and_real_auxiliary_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(plugin, "load_jsonc", lambda: {
        "historian": {"model": "vendor/historian"},
        "dreamer": {"model": "vendor/dreamer"},
    })
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path, session_id="plugin-test")

    assert result["enabled"] is True
    assert context.engine.name == "magic-context"
    assert set(context.tasks) == {"mc_historian", "mc_dreamer"}
    assert context.tasks["mc_historian"]["defaults"]["model"] == "vendor/historian"

    output = context.engine._complete(
        system_prompt="system",
        prompt="input",
        task="mc_historian",
    )
    assert output == "historian output"
    assert context.llm.calls[0]["task"] == "mc_historian"


def test_plugin_recovers_from_invalid_untrusted_config(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "runtime_available", lambda: True)
    monkeypatch.setattr(
        plugin,
        "load_jsonc",
        lambda: {
            "historian": "invalid",
            "dreamer": [],
            "historian_timeout_ms": "invalid",
        },
    )
    context = FakeContext()

    result = plugin.load(context, project_root=tmp_path)

    assert result["enabled"] is True
    assert context.tasks["mc_historian"]["defaults"]["model"] == ""
    assert context.tasks["mc_historian"]["defaults"]["timeout"] == 300
