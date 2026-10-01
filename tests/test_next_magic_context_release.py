from __future__ import annotations

import pytest

from scripts.next_magic_context_release import is_series_jump, next_release_tag


def _release(tag: str, published_at: str, *, draft: bool = False) -> dict:
    return {
        "tag_name": tag,
        "published_at": published_at,
        "created_at": published_at,
        "draft": draft,
    }


def test_next_release_tag_returns_newest_stable_release_above_pin():
    # Upstream published a whole series between nightly syncs; the sync
    # must catch up to the newest stable release in one step, not adopt
    # an already-superseded intermediate (observed 0.43.2 -> 0.44.0).
    releases = [
        _release("v0.43.2", "2026-09-20T00:00:00Z"),
        _release("v0.44.0", "2026-09-28T11:46:59Z"),
        _release("v0.44.1", "2026-09-28T19:25:03Z"),
        _release("v0.44.2", "2026-09-29T18:28:04Z"),
        _release("v0.44.3", "2026-09-29T23:28:57Z"),
        _release("v0.44.4", "2026-09-30T09:18:25Z"),
    ]

    assert next_release_tag(releases, "0.43.2") == "v0.44.4"


def test_next_release_tag_skips_series_ahead_of_newer_series():
    releases = [
        _release("v0.38.0", "2026-08-20T00:00:00Z"),
        _release("v0.39.0", "2026-08-21T00:00:00Z"),
        _release("v0.40.0", "2026-08-22T00:00:00Z"),
    ]

    assert next_release_tag(releases, "0.38.0") == "v0.40.0"


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


def test_next_release_tag_ignores_prerelease_flagged_releases():
    def flagged(tag: str, published_at: str) -> dict:
        item = _release(tag, published_at)
        item["prerelease"] = True
        return item

    releases = [
        _release("v0.38.0", "2026-08-20T00:00:00Z"),
        _release("v0.39.0", "2026-08-21T00:00:00Z"),
        flagged("v0.39.1", "2026-08-21T12:00:00Z"),
    ]

    assert next_release_tag(releases, "0.38.0") == "v0.39.0"


def test_next_release_tag_fails_closed_when_current_release_is_missing():
    releases = [_release("v0.39.0", "2026-08-21T00:00:00Z")]

    with pytest.raises(ValueError, match=r"v0\.38\.0"):
        next_release_tag(releases, "0.38.0")


def test_patch_update_is_not_a_series_jump():
    assert not is_series_jump("0.43.2", "0.43.3")
    assert not is_series_jump("v0.43.2", "v0.43.3")


def test_minor_update_is_a_series_jump():
    assert is_series_jump("0.43.2", "0.44.0")
    assert is_series_jump("v0.43.2", "0.44.0")


def test_major_update_is_a_series_jump():
    assert is_series_jump("0.43.2", "1.0.0")


def test_same_series_patch_chain_stays_in_series():
    assert not is_series_jump("0.44.0", "0.44.1")
