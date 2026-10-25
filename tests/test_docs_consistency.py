"""Guardrails for documented repository claims.

These tests keep the README, the sync workflow, and the pinned upstream
manifest in agreement. They exist because the sync workflow drives releases
automatically: if a documented cadence or pin claim drifts from the actual
configuration, operators reason about the wrong system.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC_WORKFLOW = ROOT / ".github" / "workflows" / "sync-magic-context.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"


def _upstream_pin() -> str:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    return package["dependencies"]["@cortexkit/pi-magic-context"].lstrip("=^~")


def _tested_version() -> str:
    manifest = json.loads(
        (ROOT / "src" / "magic_hermes" / "magic_context_compat.json").read_text(
            encoding="utf-8"
        )
    )
    return manifest["tested_version"]


def _sync_crons() -> list[str]:
    workflow_text = SYNC_WORKFLOW.read_text(encoding="utf-8")
    return re.findall(r"cron:\s*\"([^\"]+)\"", workflow_text)


def _hourly_backstop(crons: list[str]) -> str:
    hourly = [c for c in crons if c.split()[1] == "*"]
    assert hourly, (
        "sync workflow must keep an hourly backstop cron (lane 2): the host "
        "watcher that dispatches repository_dispatch runs on the live host, "
        "so the workflow needs its own catch-up schedule"
    )
    return hourly[0]


def _daily_backstop(crons: list[str]) -> str:
    daily = [c for c in crons if c.split()[1] != "*"]
    assert daily, (
        "sync workflow must keep the daily 00:07 UTC cron as a second "
        "backstop lane"
    )
    return daily[0]


def _ci_node_major() -> int:
    workflow_text = CI_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r'node-version:\s*"(\d+)"', workflow_text)
    assert match is not None, "ci workflow must pin a node-version"
    return int(match.group(1))


def test_upstream_pin_matches_compatibility_manifest():
    """package.json and magic_context_compat.json must never disagree."""
    assert _upstream_pin() == _tested_version()


def test_readme_cadence_claim_matches_sync_workflow_cron():
    """README's sync cadence claim must match the workflow's actual schedule."""
    crons = _sync_crons()
    hourly = _hourly_backstop(crons)
    daily = _daily_backstop(crons)
    readme = README.read_text(encoding="utf-8")
    for cron in (hourly, daily):
        fields = cron.split()
        assert len(fields) == 5, f"expected a simple cron expression, got {cron!r}"
        _minute, _hour, day, month, weekday = fields
        assert {day, month, weekday} == {"*"}, (
            "cadence claim in this test only models simple schedules; "
            f"update the README claim and this test together for {cron!r}"
        )
    expected_daily = "daily at 00:07 UTC"
    assert expected_daily in readme, (
        f"README sync-cadence claim does not mention the daily backstop "
        f"{expected_daily!r} (cron {daily!r})"
    )
    assert "hourly" in readme, (
        "README must mention the hourly backstop cadence "
        f"(cron {hourly!r})"
    )
    assert "magic-context-watch" in readme, (
        "README must name the host watcher (magic-context-watch) that "
        "dispatches the workflow's repository_dispatch trigger within "
        "minutes of an upstream release"
    )
    assert "repository_dispatch" in readme, (
        "README must document that the workflow supports immediate "
        "repository_dispatch, since the host watcher depends on it"
    )


def test_readme_pin_claim_when_present_matches_manifest():
    """A README 'through X.Y.Z' claim, if present, must equal the pinned release."""
    readme = README.read_text(encoding="utf-8")
    claims = re.findall(r"through (\d+\.\d+\.\d+)", readme)
    for claim in claims:
        assert claim == _tested_version(), (
            f"README claims upstream {claim} but the manifest pins "
            f"{_tested_version()}"
        )


def test_readme_adoption_policy_matches_resolver():
    """README's sync-adoption claim must match the resolver's actual policy.

    Regression for the 2026-10 PR #41 review: 29d4bfe made the sync adopt
    the newest stable release strictly above the pin, but the README still
    described the older publish-order stepping, so the claim drifted through
    two green syncs with no guard to catch it.
    """
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"oldest\s+unseen\s+core\s+release", readme) is None, (
        "README still claims the sync processes the oldest unseen core "
        "release first; since 29d4bfe the resolver adopts the newest stable "
        "release above the pin; update the README claim and this test together"
    )
    assert re.search(r"newest\s+stable\s+core\s+release", readme), (
        "README does not state that the sync adopts the newest stable core "
        "release above the pinned version; update the README claim and this "
        "test together"
    )
    resolver = (ROOT / "scripts" / "next_magic_context_release.py").read_text(
        encoding="utf-8"
    )
    assert "newest stable release strictly above the current pin" in resolver, (
        "scripts/next_magic_context_release.py no longer states the "
        "newest-stable-above-pin contract in next_release_tag's docstring; "
        "update the README claim and this test together"
    )


def test_readme_node_floor_agrees_with_ci_pin():
    """README's Node.js floor must exist and never exceed the CI pin.

    The floor exists because the pinned Magic Context core drives the
    shared SQLite store through the built-in node:sqlite module; CI is the
    configuration that actually validates the documented floor, so a CI
    node below it would leave the README claim unvalidated.
    """
    readme = README.read_text(encoding="utf-8")
    match = re.search(r"Node\.js (\d+) or newer", readme)
    assert match is not None, (
        "README Requirements must state a 'Node.js <major> or newer' floor; "
        "update the README claim and this test together"
    )
    floor = int(match.group(1))
    ci_node = _ci_node_major()
    assert ci_node >= floor, (
        f"CI validates on node {ci_node}, below the README floor {floor}; "
        "the documented floor must never exceed what CI validates"
    )


class TestSyncWorkflowGuardrails:
    """The auto-release workflow must keep its series-jump guard intact."""

    def test_pull_requests_permission_declared(self):
        text = SYNC_WORKFLOW.read_text(encoding="utf-8")
        assert "pull-requests: write" in text, (
            "sync workflow needs pull-requests: write to open adoption PRs"
        )

    def test_direct_release_is_gated_on_series_jump(self):
        text = SYNC_WORKFLOW.read_text(encoding="utf-8")
        assert "series_jump" in text, (
            "sync workflow lost its series-jump gate"
        )

    def test_adoption_pr_step_exists(self):
        text = SYNC_WORKFLOW.read_text(encoding="utf-8")
        assert "gh pr create" in text, (
            "sync workflow must open an adoption PR instead of direct-releasing "
            "series jumps"
        )

    def test_series_jump_gate_payload_compiles(self):
        """The gate's ``python -c`` payload must be valid Python.

        Regression for the 2026-09-30 nightly: the payload was a multi-line
        string whose first code line was indented, so Python raised
        IndentationError, the ``if`` took that as "not a series jump", and a
        minor bump (0.43.2 -> 0.44.0) silently took the direct-release path
        instead of the PR-gated adoption path.
        """
        text = SYNC_WORKFLOW.read_text(encoding="utf-8")
        payloads = re.findall(r'python -c "([^"]+)"', text)
        assert payloads, "sync workflow lost its inline series-jump gate command"
        for payload in payloads:
            compile(payload, "<series-jump-gate>", "exec")
