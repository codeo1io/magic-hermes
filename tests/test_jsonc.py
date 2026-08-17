"""Tests for the JSONC config bridge."""

from pathlib import Path

from magic_hermes.jsonc import load_jsonc, strip_jsonc


def test_strip_line_and_block_comments():
    import json
    text = '{\n  // historian\n  "historian": {"model": "zai/glm-4.7"}, /* block\ncomment */\n  "x": 1,\n}'
    assert json.loads(strip_jsonc(text)) == {"historian": {"model": "zai/glm-4.7"}, "x": 1}


def test_comment_markers_inside_strings_survive():
    text = '{"url": "http://not-a-comment", "note": "has // inside"}'
    assert strip_jsonc(text) == text


def test_trailing_commas_removed():
    assert (
        strip_jsonc('{"a": [1, 2, 3,], "b": {"c": 1,},}')
        == '{"a": [1, 2, 3], "b": {"c": 1}}'
    )


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert load_jsonc(tmp_path / "nope.jsonc") == {}


def test_load_real_shaped_config(tmp_path: Path):
    p = tmp_path / "magic-context.jsonc"
    p.write_text(
        """{
        // historian model for compaction summaries
        "historian": { "model": "zai/glm-4.7" },
        "embeddings": { "model": "text-embedding-3-small" },
        }"""
    )
    cfg = load_jsonc(p)
    assert cfg["historian"]["model"] == "zai/glm-4.7"
    assert cfg["embeddings"]["model"] == "text-embedding-3-small"
