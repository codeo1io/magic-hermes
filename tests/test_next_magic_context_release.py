from __future__ import annotations

import pytest

from scripts.next_magic_context_release import next_release_tag


def _release(tag: str, published_at: str, *, draft: bool = False) -> dict:
    return {
        "tag_name": tag,
        "published_at": published_at,
        "created_at": published_at,
        "draft": draft,
    }


def test_next_release_tag_returns_oldest_unseen_core_release():
    releases = [
        _release("v0.40.0", "2026-08-22T00:00:00Z"),
        _release("dashboard-v0.14.0", "2026-08-21T12:00:00Z"),
        _release("v0.39.0", "2026-08-21T00:00:00Z"),
        _release("v0.38.0", "2026-08-20T00:00:00Z"),
    ]

    assert next_release_tag(releases, "0.38.0") == "v0.39.0"


def test_next_release_tag_returns_none_when_current_is_latest():
    releases = [
        _release("v0.38.0", "2026-08-20T00:00:00Z"),
        _release("v0.37.0", "2026-08-19T00:00:00Z"),
    ]

    assert next_release_tag(releases, "v0.38.0") is None


def test_next_release_tag_ignores_drafts_and_non_core_tags():
    releases = [
        _release("v0.39.0", "2026-08-21T00:00:00Z", draft=True),
        _release("dashboard-v0.14.0", "2026-08-21T12:00:00Z"),
        _release("v0.38.0", "2026-08-20T00:00:00Z"),
    ]

    assert next_release_tag(releases, "0.38.0") is None


def test_next_release_tag_fails_closed_when_current_release_is_missing():
    releases = [_release("v0.39.0", "2026-08-21T00:00:00Z")]

    with pytest.raises(ValueError, match=r"v0\.38\.0"):
        next_release_tag(releases, "0.38.0")
