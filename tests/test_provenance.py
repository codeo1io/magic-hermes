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
  ``doctor`` surface is env-gated WARN-only — never FAIL, never a write,
  and omitted entirely when the variable is unset.

Every fixture is a real throwaway git repository under ``tmp_path`` —
the git state layer is never mocked, and no test touches the network or
the real estate config.
"""

from __future__ import annotations

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
) -> Path:
    """A real throwaway repo: one commit, origin set, healthy tracking.

    ``mangle_branch=True`` reproduces the exact 2026-09-29 incident
    state: ``branch.master.remote`` rewritten to the literal
    ``branch.master.merge`` and ``branch.master.merge`` unset.
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


# --- doctor surface (D6: env-gated, WARN-only, never a write) ---------------


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    # a leaked opt-in must never turn the unset-contract test flaky
    monkeypatch.delenv(provenance.PROVENANCE_ENV, raising=False)
    return home


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


def _run_doctor_json(tmp_path, monkeypatch, capsys):
    """run_doctor --json against a faked sidecar; returns (rc, payload)."""

    from magic_hermes import historian_guard as hg

    db = tmp_path / "context.db"
    hg.make_fixture_db(db)
    monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
    _wire_hermes_config()

    tested = cli.tested_magic_context_version()
    package = tmp_path / "pkg"
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
        rc = cli.run_doctor(json_output=True)
    payload = json.loads(capsys.readouterr().out)
    return rc, payload


def _provenance_rows(payload):
    return [
        check
        for check in payload["checks"]
        if "provenance" in check["message"].lower()
    ]


class TestDoctorProvenanceSurface:
    """The opt-in check: PASS/WARN only, rc 0, no writes, unset → absent."""

    def test_unset_env_omits_the_check_entirely(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
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

    def test_drifted_repo_warns_never_fails(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        repo = _make_repo(tmp_path, "drift", HTTPS, mangle_branch=True)
        config = repo / ".git" / "config"
        before = config.read_text()
        monkeypatch.setenv(provenance.PROVENANCE_ENV, str(repo))
        rc, payload = _run_doctor_json(tmp_path, monkeypatch, capsys)
        rows = _provenance_rows(payload)
        assert len(rows) == 1
        assert rows[0]["status"] == "WARN"
        assert "origin_match" in rows[0]["message"]
        assert "provenance --repair" in rows[0]["message"]
        # WARN-only: the health contract keeps rc 0 and FAIL 0
        assert rc == 0
        assert payload["summary"]["fail"] == 0
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
