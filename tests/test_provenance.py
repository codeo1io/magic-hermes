"""Regression matrix for the repository provenance guard (finding 4bc6f3a5b0c1).

On 2026-09-29 an out-of-band operational action rewrote the canonical
checkout's ``remote.origin.url`` from the pinned SSH URL
``git@github.com:codeo1io/magic-hermes.git`` (maestro's expectation,
/work/projects/maestro/config/phase1.toml:42-44) to HTTPS and mangled
``[branch "master"] remote`` to the literal ``branch.master.merge``.
maestro's phase-1 probe (``Phase1Probes.repo``,
/work/projects/maestro/src/maestro/probes.py:70-94) then FAILed the
estate lane continuously — 86 consecutive "repository provenance
mismatch" verdicts — because the remote URL is shared mutable state
(canonical and every conductor worktree share one ``.git``) that nothing
in this repository owned, pinned, or checked. These tests pin forever:

- the exact drift fixture reproduces that FAIL semantics;
- ``provenance.repair`` restores the SSH fetch URL while carrying the
  HTTPS URL to ``remote.origin.pushurl`` (SSH fetch + HTTPS push is the
  sanctioned steady state) and repairs branch tracking — idempotently,
  refusing foreign repositories without a single write;
- normalization stays byte-parity with the probe, so this guard and the
  probe can never disagree on any input;
- the ``provenance`` CLI is fail-loud (exit 1 on drift/refusal) and the
  ``doctor`` surface is default-on and context-scoped (the KTD1 policy
  matrix: an env pin and the estate-canonical row FAIL on drift — the
  escalation lane the estate's continuous verifier exercises — while a
  same-repo non-canonical checkout merely WARNs advisingly, a CI run
  states an INFO skip, and a foreign checkout stays byte-silent); every
  row is read-only — the doctor never writes, repair is always explicit.

Every fixture is a real throwaway git repository under ``tmp_path`` —
the git state layer is never mocked, and no test touches the network or
the real estate config.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from magic_hermes import cli, provenance

SSH = provenance.EXPECTED_ORIGIN
HTTPS = "https://github.com/codeo1io/magic-hermes.git"


def _sh(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return _sh("git", "-C", str(repo), *args)


def _make_repo(
    root: Path,
    name: str,
    origin_url: str,
    *,
    mangle_branch: bool = False,
    residue_branch: bool = False,
) -> Path:
    """A real throwaway repo: one commit, origin set, healthy tracking.

    ``mangle_branch=True`` reproduces the exact 2026-09-29 incident
    state: ``branch.master.remote`` rewritten to the literal
    ``branch.master.merge`` and ``branch.master.merge`` unset.

    ``residue_branch=True`` reproduces the exact 2026-10-07 incident
    state (finding d59758598379): SSH fetch pinned, the sanctioned
    HTTPS pushurl carried, ``branch.master.remote`` still ``origin``,
    and ``branch.master.merge`` pointing at the fix branch a
    ``git push -u`` left tracked — same-origin residue, advisory tier.
    """

    repo = root / name
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", origin_url)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c")
    _git(repo, "config", "branch.master.remote", "origin")
    _git(repo, "config", "branch.master.merge", "refs/heads/master")
    if mangle_branch:
        _git(repo, "config", "branch.master.remote", "branch.master.merge")
        _git(repo, "config", "--unset", "branch.master.merge")
    if residue_branch:
        _git(repo, "remote", "set-url", "--push", "origin", HTTPS)
        _git(
            repo,
            "config",
            "branch.master.merge",
            "refs/heads/fix/issue-52-retrospective-contract",
        )
    return repo


def _cfg(repo: Path, key: str) -> str | None:
    # git config --get exits 1 for an unset key — that is the None case
    proc = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Drive ``cli.main`` with stdout/stderr captured (no process exit)."""

    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestNormalizationParity:
    """D2 — guard and probe verdicts can never diverge, on any input."""

    @pytest.mark.parametrize(
        "url",
        [
            SSH,
            HTTPS,
            "git@github.com:codeo1io/magic-hermes.git.git",
            "  git@github.com:codeo1io/magic-hermes.git\n",
            "GIT@GITHUB.COM:codeo1io/magic-hermes.git",
            "https://github.com/other/repo",
            "",
        ],
    )
    def test_normalize_equals_probe_expression(self, url):
        # the probe's own expression, probes.py:79-82, re-derived inline
        probe = url.strip().lower().replace(".git", "")
        assert provenance.normalize_origin_url(url) == probe

    @pytest.mark.parametrize(
        ("url", "matches"),
        [
            (SSH, True),
            # the probe's whole-string .replace quirk: any '.git'
            # substring is stripped on BOTH sides, so suffixless and
            # doubled forms still match the pin
            ("git@github.com:codeo1io/magic-hermes", True),
            ("git@github.com:codeo1io/magic-hermes.git.git", True),
            ("GIT@GITHUB.COM:codeo1io/magic-hermes.git", True),
            ("  git@github.com:codeo1io/magic-hermes.git\n", True),
            (HTTPS, False),
            ("git@github.com:other/repo.git", False),
            ("https://gitlab.com/codeo1io/magic-hermes.git", False),
        ],
    )
    def test_verdict_against_the_pin(self, url, matches):
        state = provenance.RepoRemoteState(
            repo=Path("/fixture"), repo_readable=True, fetch_url=url
        )
        assert provenance.check_state(state).origin_match is matches

    @pytest.mark.parametrize(
        ("url_a", "url_b", "same"),
        [
            (SSH, HTTPS, True),
            (SSH, SSH, True),
            (SSH, "git@github.com:other/repo.git", False),
            (SSH, "https://gitlab.com/codeo1io/magic-hermes.git", False),
            (
                SSH,
                "https://x-access-token@github.com:443/codeo1io/magic-hermes.git",
                True,
            ),
        ],
    )
    def test_same_repo_identity(self, url_a, url_b, same):
        assert provenance.same_repo(url_a, url_b) is same


class TestDriftFixture:
    """The 2026-09-29 incident, as the guard sees it — the FAIL class."""

    @pytest.fixture
    def drift_repo(self, tmp_path):
        return _make_repo(
            tmp_path, "drift", HTTPS, mangle_branch=True
        )

    def test_reports_exactly_the_incident_facets(self, drift_repo):
        report = provenance.check_state(provenance.read_state(drift_repo))
        assert report.repo_readable
        assert report.origin_match is False
        assert report.branch_tracking is False
        assert report.drifted_facets == ["origin_match", "branch_tracking"]
        assert report.healthy is False

    def test_probe_would_fail_on_this_fixture(self, drift_repo):
        # what maestro's probe reads: git remote get-url origin
        probe_reads = _git(drift_repo, "remote", "get-url", "origin")
        assert probe_reads == HTTPS
        assert provenance.normalize_origin_url(probe_reads) != (
            provenance.normalize_origin_url(SSH)
        )

    def test_summary_names_the_drift_and_urls(self, drift_repo):
        summary = provenance.check_state(
            provenance.read_state(drift_repo)
        ).summary()
        assert "origin_match" in summary
        assert "branch_tracking" in summary
        assert HTTPS in summary

    def test_no_origin_remote_is_drift(self, tmp_path):
        repo = _make_repo(tmp_path, "noorigin", SSH)
        _git(repo, "remote", "remove", "origin")
        report = provenance.check_state(provenance.read_state(repo))
        assert report.origin_match is False
        assert "origin_match" in report.drifted_facets
        assert report.healthy is False

    def test_not_a_repo_reports_only_repo_readable(self, tmp_path):
        nonrepo = tmp_path / "notarepo"
        nonrepo.mkdir()
        report = provenance.check_state(provenance.read_state(nonrepo))
        assert report.drifted_facets == ["repo_readable"]
        assert report.healthy is False

    def test_detached_head_is_info_not_drift(self, tmp_path):
        repo = _make_repo(tmp_path, "detached", SSH)
        head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "--detach", head)
        report = provenance.check_state(provenance.read_state(repo))
        assert report.branch_tracking is None
        assert report.healthy is True
        assert "detached" in report.summary()


class TestRepair:
    """Restore the pin, preserve push intent, refuse everything else."""

    def test_restores_pin_and_carries_push_intent(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        result = provenance.repair(repo)
        assert len(result.writes) == 4
        assert _cfg(repo, "remote.origin.url") == SSH
        assert _cfg(repo, "remote.origin.pushurl") == HTTPS
        assert _cfg(repo, "branch.master.remote") == "origin"
        assert _cfg(repo, "branch.master.merge") == "refs/heads/master"
        # probe-visible origin is SSH again: the lane PASSes
        assert _git(repo, "remote", "get-url", "origin") == SSH
        assert provenance.check_state(provenance.read_state(repo)).healthy

    def test_idempotent_on_healthy_repo(self, tmp_path):
        repo = _make_repo(tmp_path, "healthy", SSH)
        first = provenance.repair(repo)
        assert first.writes == []
        assert provenance.check_state(provenance.read_state(repo)).healthy

    def test_second_repair_is_a_no_op(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        provenance.repair(repo)
        assert provenance.repair(repo).writes == []

    def test_refuses_foreign_origin_without_writes(self, tmp_path):
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        config = repo / ".git" / "config"
        before = config.read_text()
        with pytest.raises(provenance.ProvenanceError) as excinfo:
            provenance.repair(repo)
        assert "different repository" in str(excinfo.value)
        assert config.read_text() == before

    def test_refuses_missing_origin(self, tmp_path):
        repo = _make_repo(tmp_path, "noorigin", SSH)
        _git(repo, "remote", "remove", "origin")
        with pytest.raises(provenance.ProvenanceError) as excinfo:
            provenance.repair(repo)
        assert "no 'origin' remote" in str(excinfo.value)

    def test_refuses_non_repo_path(self, tmp_path):
        nonrepo = tmp_path / "notarepo"
        nonrepo.mkdir()
        with pytest.raises(provenance.ProvenanceError) as excinfo:
            provenance.repair(nonrepo)
        assert "not a readable git repository" in str(excinfo.value)

    def test_explicit_push_url_set_verbatim_fetch_untouched(self, tmp_path):
        repo = _make_repo(tmp_path, "pu", SSH)
        provenance.repair(repo, push_url=HTTPS)
        assert _cfg(repo, "remote.origin.pushurl") == HTTPS
        assert _cfg(repo, "remote.origin.url") == SSH
        assert provenance.check_state(provenance.read_state(repo)).healthy

    def test_refuses_foreign_explicit_push_url(self, tmp_path):
        repo = _make_repo(tmp_path, "puf", SSH)
        with pytest.raises(provenance.ProvenanceError) as excinfo:
            provenance.repair(repo, push_url="https://github.com/o/r.git")
        assert "different repository" in str(excinfo.value)
        assert _cfg(repo, "remote.origin.pushurl") is None

    def test_refuses_foreign_configured_pushurl(self, tmp_path):
        repo = _make_repo(tmp_path, "fp", SSH)
        _git(repo, "remote", "set-url", "--push", "origin",
             "https://github.com/other/repo.git")
        report = provenance.check_state(provenance.read_state(repo))
        assert report.drifted_facets == ["pushurl_same_repo"]
        with pytest.raises(provenance.ProvenanceError) as excinfo:
            provenance.repair(repo)
        assert "--push-url" in str(excinfo.value)
        # the explicit remedy: a same-repo --push-url lands the fix
        provenance.repair(repo, push_url=HTTPS)
        assert _cfg(repo, "remote.origin.pushurl") == HTTPS

    def test_detached_head_repair_writes_nothing(self, tmp_path):
        repo = _make_repo(tmp_path, "det", SSH)
        head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "--detach", head)
        assert provenance.repair(repo).writes == []


class TestPushurlSteadyState:
    """D5 — SSH fetch + HTTPS push is the sanctioned steady state."""

    def test_healthy_with_https_push_route(self, tmp_path):
        repo = _make_repo(tmp_path, "steady", SSH)
        _git(repo, "remote", "set-url", "--push", "origin", HTTPS)
        report = provenance.check_state(provenance.read_state(repo))
        assert report.healthy
        # the pushurl is read as configured, never the echoed fetch URL
        assert report.push_url == HTTPS

    def test_probe_passes_while_push_rides_https(self, tmp_path):
        repo = _make_repo(tmp_path, "steady", SSH)
        _git(repo, "remote", "set-url", "--push", "origin", HTTPS)
        assert _git(repo, "remote", "get-url", "origin") == SSH
        assert _git(repo, "remote", "get-url", "--push", "origin") == HTTPS


class TestProvenanceCli:
    """Unit 2 — operator surface: fail-loud exits, honest output."""

    def test_healthy_repo_exits_zero(self, tmp_path):
        repo = _make_repo(tmp_path, "healthy", SSH)
        rc, out, _ = _run_cli(["provenance", "--path", str(repo)])
        assert rc == 0
        assert "provenance verified" in out
        assert "DRIFT" not in out

    def test_drift_exits_one_and_names_the_repair(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        rc, out, err = _run_cli(["provenance", "--path", str(repo)])
        assert rc == 1
        assert "origin_match: DRIFT" in out
        assert "branch_tracking: DRIFT" in out
        assert "provenance --repair" in out
        assert err == ""

    def test_repair_prints_every_write_then_verifies(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        rc, out, _err = _run_cli(
            ["provenance", "--path", str(repo), "--repair"]
        )
        assert rc == 0
        assert out.count("│  repair:") == 4
        assert "remote.origin.url" in out
        assert "remote.origin.pushurl" in out
        assert "provenance verified after repair" in out
        assert _cfg(repo, "remote.origin.url") == SSH

    def test_repair_on_healthy_repo_is_a_stated_no_op(self, tmp_path):
        repo = _make_repo(tmp_path, "healthy", SSH)
        rc, out, _ = _run_cli(
            ["provenance", "--path", str(repo), "--repair"]
        )
        assert rc == 0
        assert "nothing to write" in out

    def test_refusal_exits_one_with_stderr_and_zero_writes(self, tmp_path):
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        config = repo / ".git" / "config"
        before = config.read_text()
        rc, _out, err = _run_cli(
            ["provenance", "--path", str(repo), "--repair"]
        )
        assert rc == 1
        assert "repair refused" in err
        assert "different repository" in err
        assert config.read_text() == before

    def test_push_url_requires_repair(self, tmp_path):
        repo = _make_repo(tmp_path, "healthy", SSH)
        rc, _, err = _run_cli(
            ["provenance", "--path", str(repo), "--push-url", HTTPS]
        )
        assert rc == 1
        assert "--repair" in err
        assert _cfg(repo, "remote.origin.pushurl") is None

    def test_json_shape_parses_and_agrees_with_text_verdict(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        rc_text, _, _ = _run_cli(["provenance", "--path", str(repo)])
        rc_json, out, _ = _run_cli(["provenance", "--path", str(repo), "--json"])
        payload = json.loads(out)
        assert rc_json == rc_text == 1
        assert payload["healthy"] is False
        assert payload["facets"]["origin_match"] is False
        assert payload["facets"]["branch_tracking"] is False
        assert payload["repair"] == []

        rc, out, _ = _run_cli(
            ["provenance", "--path", str(repo), "--json", "--repair"]
        )
        payload = json.loads(out)
        assert rc == 0
        assert payload["healthy"] is True
        assert len(payload["repair"]) == 4

    def test_json_refusal_carries_error_field(self, tmp_path):
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        rc, out, _ = _run_cli(
            ["provenance", "--path", str(repo), "--json", "--repair"]
        )
        payload = json.loads(out)
        assert rc == 1
        assert "different repository" in payload["error"]

    def test_default_path_resolves_the_containing_repo(
        self, tmp_path, monkeypatch
    ):
        repo = _make_repo(tmp_path, "here", SSH)
        nested = repo / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)
        rc, out, _ = _run_cli(["provenance"])
        assert rc == 0
        assert str(repo) in out

    def test_detached_head_reports_info_line(self, tmp_path):
        repo = _make_repo(tmp_path, "det", SSH)
        head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "--detach", head)
        rc, out, _ = _run_cli(["provenance", "--path", str(repo)])
        assert rc == 0
        assert "detached HEAD (INFO)" in out

    def test_parser_defaults_are_check_only(self):
        args = cli.build_parser().parse_args(["provenance"])
        assert args.path is None
        assert args.repair is False
        assert args.push_url is None
        assert args.json is False


# --- module-runnable surface (U2 rider: -m entry points) --------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _module_env() -> dict[str, str]:
    """Env making the checkout's ``src/`` importable with no install step."""

    src = str(_REPO_ROOT / "src")
    existing = os.environ.get("PYTHONPATH")
    env = dict(os.environ)
    env["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    # keep the checkout free of __pycache__ debris regardless of how the
    # suite itself was invoked
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_module(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a ``python -m`` entry point exactly as an operator would."""

    return subprocess.run(
        [sys.executable, "-m", *argv],
        cwd=_REPO_ROOT,
        env=_module_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


class TestModuleRunnable:
    """U2 rider — ``python -m`` entry points: the trap closed, parity pinned.

    ``python3 -m magic_hermes.provenance`` used to import the module and
    exit 0 silently on any input — a false-healthy no-op, observed against
    the drifted canonical checkout on 2026-09-29. The rider routes both
    ``-m`` forms through the same ``cli.main`` the installed console
    script dispatches, so exit codes and output are identical by
    construction; these tests pin that parity where it is observable —
    exit codes and the load-bearing facets — never the exact output bytes.
    """

    def test_importing_the_module_stays_inert(self):
        # the guard must never fire on import: cli imports provenance at
        # module level, so a module-level back-import would cycle
        proc = subprocess.run(
            [sys.executable, "-c",
             "import magic_hermes.provenance, magic_hermes.cli"],
            cwd=_REPO_ROOT,
            env=_module_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert proc.returncode == 0
        assert proc.stdout == ""
        assert proc.stderr == ""

    def test_provenance_module_healthy_repo_exits_zero(self, tmp_path):
        repo = _make_repo(tmp_path, "healthy", SSH)
        proc = _run_module(
            ["magic_hermes.provenance", "--path", str(repo)]
        )
        assert proc.returncode == 0
        assert "provenance verified" in proc.stdout
        assert proc.stderr == ""

    def test_provenance_module_drift_exits_one_and_names_facets(
        self, tmp_path
    ):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        proc = _run_module(
            ["magic_hermes.provenance", "--path", str(repo)]
        )
        assert proc.returncode == 1
        assert "origin_match: DRIFT" in proc.stdout
        assert "branch_tracking: DRIFT" in proc.stdout
        assert "provenance --repair" in proc.stdout
        assert proc.stderr == ""

    def test_provenance_module_refusal_exits_one_zero_writes(self, tmp_path):
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        config = repo / ".git" / "config"
        before = config.read_text()
        proc = _run_module(
            ["magic_hermes.provenance", "--path", str(repo), "--repair"]
        )
        assert proc.returncode == 1
        assert "repair refused" in proc.stderr
        assert "different repository" in proc.stderr
        assert config.read_text() == before

    def test_provenance_module_repair_restores_the_pin(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        proc = _run_module(
            ["magic_hermes.provenance", "--path", str(repo), "--repair"]
        )
        assert proc.returncode == 0
        assert "provenance verified after repair" in proc.stdout
        assert _cfg(repo, "remote.origin.url") == SSH
        assert _cfg(repo, "remote.origin.pushurl") == HTTPS

    def test_provenance_module_json_shape(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        proc = _run_module(
            ["magic_hermes.provenance", "--path", str(repo), "--json"]
        )
        payload = json.loads(proc.stdout)
        assert proc.returncode == 1
        assert payload["healthy"] is False
        assert payload["facets"]["origin_match"] is False
        assert payload["facets"]["branch_tracking"] is False
        # both tier columns are present; this drift fixture is
        # strict-tier (mangled tracking remote) so the advisory column
        # is empty — the residue tier is pinned below in
        # TestDoctorProvenanceMergeResidueTier (T6)
        assert payload["drifted_facets"] == [
            "origin_match",
            "branch_tracking",
        ]
        assert payload["advisory_facets"] == []
        assert payload["repair"] == []

    def test_cli_module_provenance_parity_with_console_script(self, tmp_path):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        proc = _run_module(
            ["magic_hermes.cli", "provenance", "--path", str(repo)]
        )
        assert "branch_tracking: DRIFT" in proc.stdout
        # parity by construction: the -m form and cli.main are the same
        # program — same rc, same bytes on both streams. Both sides move
        # together as the output format evolves, so this pins parity,
        # not the format itself.
        rc, out, err = _run_cli(["provenance", "--path", str(repo)])
        assert proc.returncode == rc == 1
        assert proc.stdout == out
        assert proc.stderr == err


# --- doctor surface (D6/KTD1: default-on matrix, strict rows, no writes) ---


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    # a leaked opt-in must never turn the unset-contract test flaky
    monkeypatch.delenv(provenance.PROVENANCE_ENV, raising=False)
    # ... and neither may a leaked CI marker: the matrix keys off it
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return home


_DOCTOR_RUN_SEQ = itertools.count()


def _wire_hermes_config():
    config = Path(cli.hermes_config_path())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "context:\n"
        "  engine: magic-context\n"
        "memory:\n"
        "  provider: magic_context\n"
        "plugins:\n"
        "  enabled:\n"
        "    - magic-hermes\n",
        encoding="utf-8",
    )


def _run_doctor(tmp_path, monkeypatch, capsys, json_output=True):
    """run_doctor against a faked sidecar; returns (rc, payload|text).

    Safe to call repeatedly within one test: every invocation builds a
    fresh fixture DB (the historian fixture DDL does not tolerate
    re-creation over an existing file).
    """

    from magic_hermes import historian_guard as hg

    run = next(_DOCTOR_RUN_SEQ)
    db = tmp_path / f"context-{run}.db"
    hg.make_fixture_db(db)
    monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
    _wire_hermes_config()

    tested = cli.tested_magic_context_version()
    package = tmp_path / f"pkg-{run}"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "@cortexkit/pi-magic-context", "version": tested}),
        encoding="utf-8",
    )
    (package / "dist").mkdir()
    (package / "dist" / "index.js").write_text("// stub\n", encoding="utf-8")
    series = ".".join(map(str, cli.supported_magic_context_series()))

    sidecar = mock.MagicMock()

    def _call(method, params=None, timeout=60):
        if method == "hello":
            return {"harness": "hermes", "package_version": tested}
        return {
            "database_health": "ok",
            "core_symbols_ready": True,
            "supported_series": series,
        }

    sidecar.call.side_effect = _call
    client = mock.MagicMock()
    client.__enter__.return_value = sidecar
    client.__exit__.return_value = False

    with (
        mock.patch.object(
            cli, "discover_installations", return_value=[(package, tested)]
        ),
        mock.patch.object(cli, "RuntimeClient", return_value=client),
        mock.patch.object(cli, "_SIDECAR_RETRY_BACKOFF_S", 0.0),
    ):
        rc = cli.run_doctor(json_output=json_output)
    out = capsys.readouterr().out
    return (rc, out) if not json_output else (rc, json.loads(out))


def _run_doctor_json(tmp_path, monkeypatch, capsys):
    """run_doctor --json against a faked sidecar; returns (rc, payload)."""

    return _run_doctor(tmp_path, monkeypatch, capsys, json_output=True)


def _provenance_rows(payload):
    return [
        check
        for check in payload["checks"]
        if "provenance" in check["message"].lower()
    ]


def _nonrepo_dir(tmp_path) -> Path:
    plain = tmp_path / "plain"
    plain.mkdir(exist_ok=True)  # idempotent: called per-run within a test
    return plain


def _pin_canonical(monkeypatch, repo: Path) -> Path:
    """Point the seam's canonical-path constant (KTD3) at ``repo``.

    Both import sites are pinned — the module constant and the name the
    CLI binds — so the rows below hold whichever one the facet reads,
    and no test ever reads the live estate path (KTD2).
    """

    monkeypatch.setattr(provenance, "EXPECTED_REPO_PATH", repo)
    monkeypatch.setattr(cli, "EXPECTED_REPO_PATH", repo)
    return repo


class TestDoctorProvenanceSurface:
    """The env-pinned row and the always-true invariants.

    An explicit ``MAGIC_HERMES_PROVENANCE_REPO`` pin is the strictest
    row of the KTD1 matrix: drift FAILs and flips the exit code, an
    unreadable pin degrades to a WARN skip, and no row ever writes. The
    default-on bare rows are pinned in TestDoctorProvenanceEscalation.
    """

    def test_unset_env_in_nonrepo_cwd_emits_nothing(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        monkeypatch.chdir(_nonrepo_dir(tmp_path))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert rc == 0
        assert _provenance_rows(payload) == []

    def test_healthy_repo_emits_pass_and_keeps_rc_zero(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "healthy", SSH)
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(repo))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "PASS"
        assert SSH in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_drifted_env_pinned_repo_fails_loud(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        config = repo / ".git" / "config"
        before = config.read_text()
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(repo))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "FAIL"
        assert "origin_match" in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        # strict row: drift flips the exit code — the escalation contract
        assert rc == 1
        assert payload["summary"]["fail"] >= 1
        # and the doctor never mutates git config (repair is explicit)
        assert config.read_text() == before

    def test_unreadable_path_warns_as_skipped(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        nonrepo = tmp_path / "notarepo"
        nonrepo.mkdir()
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(nonrepo))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "not a readable git repo" in rows[0]["message"]
        assert rc == 0

    def test_no_provenance_message_contains_fail_zero(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the phase-2 verdict greps stdout for "FAIL 0"; no provenance
        # wording may ever ride that substring (test_doctor_contract:292)
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(repo))
        _, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert all(
            "FAIL 0" not in check["message"] for check in payload["checks"]
        )


class TestDoctorProvenanceEscalation:
    """KTD1's default-on rows — the blind-verifier class, closed.

    The 2026-10-02 escalation finding: the daemon-facing ``doctor`` ran
    the provenance facet env-gated OFF while the estate's actual
    verifier (maestro phase-1, ``root_cause_repo='magic-hermes'``)
    FAILed continuously — the estate's own health surface was
    structurally blind to the drift class it was suffering. These rows
    pin the standing enforcement matrix:

    - the estate-canonical row is strict: drift FAILs and flips the
      exit code (the escalation lane maestro exercises), health PASSes
      with a ``FAIL 0`` summary;
    - a same-repo non-canonical checkout (every conductor worktree and
      any https dev clone) WARNs advisingly — never FAIL — so the
      standing battery stays green inside worktrees pre-repair;
    - a CI run states an INFO skip (https clones are legitimate there);
    - a foreign repository or plain directory stays byte-silent.

    Every row resolves through the real seam: cwd and the canonical
    constant are pinned to throwaway fixtures, never the live estate
    (KTD2), and no row ever writes (repair is always explicit).
    """

    def test_canonical_drift_fails_and_names_the_repair(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the blind-verifier class, reproduced exactly: a drifted
        # canonical-shaped checkout surfaced through the default-on row
        repo = _make_repo(tmp_path, "canon", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "FAIL"
        assert "origin_match" in rows[0]["message"]
        assert "branch_tracking" in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        assert rc == 1
        assert payload["summary"]["fail"] >= 1

    def test_canonical_drift_flips_the_gate_grep_shape(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # maestro's phase-2 gate is rc==0 AND 'FAIL 0' in stdout: the
        # drifted canonical row must break BOTH halves of that conjunct
        repo = _make_repo(tmp_path, "canon", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        rc, out = _run_doctor(tmp_path, monkeypatch, capsys, json_output=False)
        assert rc == 1
        assert "FAIL 1" in out
        assert "FAIL 0" not in out
        assert "provenance --repair" in out

    def test_healthy_canonical_passes_with_fail_zero(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "canon-ok", SSH)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "PASS"
        assert SSH in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0
        rc, out = _run_doctor(tmp_path, monkeypatch, capsys, json_output=False)
        assert rc == 0
        assert "FAIL 0" in out

    @pytest.mark.parametrize(
        ("ci_var", "ci_val"), [("CI", "1"), ("GITHUB_ACTIONS", "true")]
    )
    def test_ci_environment_states_an_info_skip(
        self, tmp_path, monkeypatch, isolated_home, capsys, ci_var, ci_val
    ):
        # even a would-be-strict drifted canonical cwd is skipped in CI
        repo = _make_repo(tmp_path, "cidev", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        monkeypatch.setenv(ci_var, ci_val)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "INFO"
        assert "skip" in rows[0]["message"].lower()
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_foreign_repo_cwd_stays_silent(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # foreign default: byte-stable output, no row at all
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        _pin_canonical(
            monkeypatch, _make_repo(tmp_path, "canon", SSH)
        )
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert _provenance_rows(payload) == []
        assert rc == 0

    def test_no_repo_cwd_stays_silent(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        monkeypatch.chdir(_nonrepo_dir(tmp_path))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert _provenance_rows(payload) == []
        assert rc == 0

    def test_same_repo_non_canonical_drift_is_advisory_warn(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # a drifted same-repo checkout away from the canonical path:
        # advisory WARN, never a FAIL — this is the row that keeps the
        # standing battery green inside conductor worktrees pre-repair
        repo = _make_repo(tmp_path, "devclone", HTTPS, mangle_branch=True)
        _pin_canonical(
            monkeypatch, _make_repo(tmp_path, "canon", SSH)
        )
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "origin_match" in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_same_repo_non_canonical_healthy_passes(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "wt", SSH)
        _pin_canonical(
            monkeypatch, _make_repo(tmp_path, "canon", SSH)
        )
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "PASS"
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_repair_then_redrift_is_detected_again(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # no caching anywhere: the row reads live git state every run
        repo = _make_repo(tmp_path, "cycle", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)

        rc, p1 = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert rc == 1
        assert _provenance_rows(p1)[0]["status"] == "FAIL"

        provenance.repair(repo)  # heal: SSH fetch, https push carried
        rc, p2 = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert rc == 0
        assert _provenance_rows(p2)[0]["status"] == "PASS"

        _git(repo, "remote", "set-url", "origin", HTTPS)
        _git(repo, "config", "branch.master.remote", "branch.master.merge")
        _git(repo, "config", "--unset", "branch.master.merge")
        rc, p3 = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert rc == 1
        assert _provenance_rows(p3)[0]["status"] == "FAIL"

    def test_doctor_never_writes_even_while_failing(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "nowrite", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        before = _git(repo, "config", "--local", "--list")
        rc, _payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        assert rc == 1
        assert _git(repo, "config", "--local", "--list") == before


class TestDoctorProvenanceMergeResidueTier:
    """The 2026-10-07 residue class (d59758598379) — advisory, not FAIL.

    The estate's canonical checkout drifted to
    ``branch.master.merge=refs/heads/fix/...`` — same-origin residue
    of a ``git push -u`` to a fix branch: every push still aimed at
    the RIGHT repository, yet the strict doctor row FAILed maestro's
    operational contract over workflow noise. Under the tiered
    verdict the residue WARNs every doctor row — the estate's
    rc==0/"FAIL 0" gate stays green — while the message still names
    ``magic-hermes provenance --repair`` and the explicit audit keeps
    exiting 1 until repaired (T5). The 2026-09-29 class — a tracking
    REMOTE off origin, pushes retargetable — stays strict (T4 pins
    it WITHOUT origin_match drift, alongside the mangle_branch
    fixtures above). T1-T7 follow the plan's regression matrix.
    """

    def test_t1_canonical_residue_warns_and_keeps_gate_green(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the estate's exact 2026-10-07 state on the canonical path:
        # advisory WARN, not FAIL — rc stays 0 and "FAIL 0" stays in
        # stdout, so maestro's gate rides through benign residue while
        # the row still names the repair and the doctor never writes
        repo = _make_repo(tmp_path, "canon", SSH, residue_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        config = repo / ".git" / "config"
        before = config.read_text()
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "branch_merge_tracking" in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0
        rc, out = _run_doctor(
            tmp_path, monkeypatch, capsys, json_output=False
        )
        assert rc == 0
        assert "FAIL 0" in out
        assert config.read_text() == before

    def test_t2_env_pinned_residue_warns_not_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the env pin is the strictest row of the matrix — it too WARNs
        # on advisory-only residue: only drifted_facets FAIL a row
        repo = _make_repo(tmp_path, "pinned", SSH, residue_branch=True)
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(repo))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "branch_merge_tracking" in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_t3_same_repo_non_canonical_residue_warns(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the advisory lane is unchanged by the tier split — residue
        # in a conductor-worktree-shaped checkout WARNs with the
        # advisory facet named
        repo = _make_repo(tmp_path, "wt", SSH, residue_branch=True)
        _pin_canonical(monkeypatch, _make_repo(tmp_path, "canon", SSH))
        monkeypatch.chdir(repo)
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "branch_merge_tracking" in rows[0]["message"]
        assert rc == 0
        assert payload["summary"]["fail"] == 0

    def test_t4_retargeted_tracking_remote_still_fails_strict(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the escalation lane survives for the class it was built for:
        # tracking REMOTE off origin (pushes retargetable, the
        # 2026-09-29 class) FAILs the canonical row with ONLY
        # branch_tracking drifted — no origin_match drift needed
        repo = _make_repo(tmp_path, "canon", SSH)
        _git(repo, "config", "branch.master.remote", "other")
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        report = provenance.check_state(provenance.read_state(repo))
        assert report.drifted_facets == ["branch_tracking"]
        assert report.advisory_facets == []
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "FAIL"
        assert "branch_tracking" in rows[0]["message"]
        assert "branch_merge_tracking" not in rows[0]["message"]
        assert "origin_match" not in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        assert rc == 1
        assert payload["summary"]["fail"] >= 1

    def test_t5_audit_fails_on_residue_and_single_write_heals(
        self, tmp_path
    ):
        # the explicit audit stays strict through the tier split:
        # residue exits 1 naming the advisory facet; --repair performs
        # exactly one write — the mechanical merge restore — and
        # verifies after; the pinned fetch and carried pushurl are
        # left byte-untouched (D6: repair() unchanged)
        repo = _make_repo(tmp_path, "residue", SSH, residue_branch=True)
        rc, out, err = _run_cli(["provenance", "--path", str(repo)])
        assert rc == 1
        assert "branch_merge_tracking" in out
        assert "provenance --repair" in out
        assert err == ""
        rc, out, _err = _run_cli(
            ["provenance", "--path", str(repo), "--repair"]
        )
        assert rc == 0
        assert "provenance verified after repair" in out
        assert out.count("│  repair:") == 1
        assert "branch.master.merge: refs/heads/master" in out
        assert _cfg(repo, "branch.master.merge") == "refs/heads/master"
        assert _cfg(repo, "remote.origin.url") == SSH
        assert _cfg(repo, "remote.origin.pushurl") == HTTPS
        twin = _make_repo(tmp_path, "twin", SSH, residue_branch=True)
        rc, out, _err = _run_cli(
            ["provenance", "--path", str(twin), "--json", "--repair"]
        )
        payload = json.loads(out)
        assert rc == 0
        assert payload["healthy"] is True
        assert payload["repair"] == [
            "branch.master.merge: refs/heads/master"
        ]

    def test_t6_json_payload_carries_both_tiers(self, tmp_path):
        # D5: advisory_facets alongside drifted_facets, composite
        # branch_tracking kept — machine consumers see the tier split
        repo = _make_repo(tmp_path, "residue", SSH, residue_branch=True)
        rc, out, _ = _run_cli(
            ["provenance", "--path", str(repo), "--json"]
        )
        payload = json.loads(out)
        assert rc == 1
        assert payload["advisory_facets"] == ["branch_merge_tracking"]
        assert payload["drifted_facets"] == []
        assert payload["facets"]["branch_tracking"] is False
        assert payload["healthy"] is False
        healthy = _make_repo(tmp_path, "healthy", SSH)
        rc, out, _ = _run_cli(
            ["provenance", "--path", str(healthy), "--json"]
        )
        payload = json.loads(out)
        assert rc == 0
        assert payload["advisory_facets"] == []
        assert payload["drifted_facets"] == []

    def test_t7_summary_wording_names_the_tier(self):
        # pure verdict wording (D4) plus the D1 edge cases: strict-only
        # / advisory-only / both / unset tracking remote (strict) /
        # unset merge with remote=origin (advisory)
        def _report(fetch, branch_remote, branch_merge):
            state = provenance.RepoRemoteState(
                repo=Path("/fixture"),
                repo_readable=True,
                fetch_url=fetch,
                push_url=None,
                branch="master",
                branch_remote=branch_remote,
                branch_merge=branch_merge,
            )
            return provenance.check_state(state)

        strict_only = _report(HTTPS, "origin", "refs/heads/master")
        assert strict_only.drifted_facets == ["origin_match"]
        assert strict_only.advisory_facets == []
        assert strict_only.summary().startswith("drift in origin_match: ")

        advisory_only = _report(
            SSH, "origin", "refs/heads/fix/issue-52-retrospective-contract"
        )
        assert advisory_only.branch_tracking is False  # composite stays
        assert advisory_only.drifted_facets == []
        assert advisory_only.advisory_facets == ["branch_merge_tracking"]
        assert advisory_only.summary().startswith(
            "advisory drift in branch_merge_tracking: "
        )

        both = _report(
            HTTPS, "origin", "refs/heads/fix/issue-52-retrospective-contract"
        )
        assert both.drifted_facets == ["origin_match"]
        assert both.advisory_facets == ["branch_merge_tracking"]
        assert both.summary().startswith(
            "drift in origin_match (advisory: branch_merge_tracking): "
        )

        unset_remote = _report(SSH, None, "refs/heads/master")
        assert unset_remote.drifted_facets == ["branch_tracking"]
        assert unset_remote.advisory_facets == []

        merge_unset = _report(SSH, "origin", None)
        assert merge_unset.drifted_facets == []
        assert merge_unset.advisory_facets == ["branch_merge_tracking"]


class TestProvenanceDoctorTargetSeam:
    """KTD2 — the resolution matrix itself, as the seam sees it.

    Precedence: env pin → CI skip → estate canonical → same-repo root
    → silent. Every input is test-controlled (env vars, cwd, and the
    KTD3 canonical constant are all pinned to fixtures) — the seam is
    never allowed to read live estate state from the test lane.
    """

    @pytest.fixture(autouse=True)
    def _neutral_context(self, tmp_path, monkeypatch):
        for var in (provenance.PROVENANCE_ENV, "CI", "GITHUB_ACTIONS"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.chdir(_nonrepo_dir(tmp_path))

    def test_env_pin_wins_over_ci_and_canonical_cwd(
        self, tmp_path, monkeypatch
    ):
        canon = _make_repo(tmp_path, "canon", SSH)
        pinned = tmp_path / "pinned"
        pinned.mkdir()
        _pin_canonical(monkeypatch, canon)
        monkeypatch.chdir(canon)
        monkeypatch.setenv("CI", "1")
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(pinned))
        target, strict = cli._provenance_doctor_target()
        assert target == pinned
        assert strict is True

    def test_ci_skip_beats_canonical_cwd(self, tmp_path, monkeypatch):
        repo = _make_repo(tmp_path, "canon", SSH)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        monkeypatch.setenv("CI", "1")
        assert cli._provenance_doctor_target()[0] is None

    def test_canonical_cwd_resolves_strict(self, tmp_path, monkeypatch):
        repo = _make_repo(tmp_path, "canon", HTTPS, mangle_branch=True)
        _pin_canonical(monkeypatch, repo)
        monkeypatch.chdir(repo)
        target, strict = cli._provenance_doctor_target()
        assert target == repo
        assert strict is True

    def test_same_repo_non_canonical_cwd_resolves_advisory(
        self, tmp_path, monkeypatch
    ):
        repo = _make_repo(tmp_path, "devclone", HTTPS)
        _pin_canonical(
            monkeypatch, _make_repo(tmp_path, "canon", SSH)
        )
        monkeypatch.chdir(repo)
        target, strict = cli._provenance_doctor_target()
        assert target == repo
        assert strict is False

    def test_foreign_repo_cwd_resolves_no_target(
        self, tmp_path, monkeypatch
    ):
        repo = _make_repo(tmp_path, "foreign", "git@github.com:other/repo.git")
        _pin_canonical(
            monkeypatch, _make_repo(tmp_path, "canon", SSH)
        )
        monkeypatch.chdir(repo)
        assert cli._provenance_doctor_target()[0] is None

    def test_no_repo_cwd_resolves_no_target(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(_nonrepo_dir(tmp_path))
        assert cli._provenance_doctor_target()[0] is None
