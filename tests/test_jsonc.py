import json

from magic_hermes.jsonc import default_config_path, load_jsonc, strip_jsonc


def test_strip_line_and_block_comments():
    text = """
    {
      // historian
      "historian": {"model": "zai/glm-4.7"},
      /* block
         comment */
      "x": 1,
    }
    """
    assert json.loads(strip_jsonc(text)) == {
        "historian": {"model": "zai/glm-4.7"},
        "x": 1,
    }


def test_comment_markers_inside_strings_are_preserved():
    text = '{"url":"https://example.test/a//b","literal":"/* keep */"}'
    assert json.loads(strip_jsonc(text)) == {
        "url": "https://example.test/a//b",
        "literal": "/* keep */",
    }


def test_load_jsonc_missing_returns_empty(tmp_path):
    assert load_jsonc(tmp_path / "missing.jsonc") == {}


def test_load_jsonc_file(tmp_path):
    path = tmp_path / "config.jsonc"
    path.write_text('{"enabled": true,}', encoding="utf-8")
    assert load_jsonc(path) == {"enabled": True}


def test_load_jsonc_malformed_fails_open(tmp_path, caplog):
    path = tmp_path / "config.jsonc"
    path.write_text('{"enabled":', encoding="utf-8")

    assert load_jsonc(path) == {}
    assert "Could not read shared Magic Context config" in caplog.text


def test_load_jsonc_non_object_fails_open(tmp_path, caplog):
    path = tmp_path / "config.jsonc"
    path.write_text("[]", encoding="utf-8")

    assert load_jsonc(path) == {}
    assert "is not a JSON object" in caplog.text


def test_default_config_path_honors_absolute_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_config_path() == (
        tmp_path / "cortexkit" / "magic-context.jsonc"
    )
