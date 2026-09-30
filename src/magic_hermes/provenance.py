"""Repository provenance guard for the canonical git origin.

Maestro finding 4bc6f3a5b0c1 (phase1.repo.magic_hermes, CRITICAL) opened
on 2026-09-29 when an out-of-band operational action rewrote the canonical
checkout's ``remote.origin.url`` from the pinned SSH URL to HTTPS — and
mangled ``[branch "master"] remote`` — while maestro's phase-1 probe
(``Phase1Probes.repo``, /work/projects/maestro/src/maestro/probes.py:70-94,
expectation in config/phase1.toml:42-44) still expects
``git@github.com:codeo1io/magic-hermes.git``. The remote URL is shared
mutable state across the whole estate — the canonical checkout and every
conductor worktree share one ``.git`` — with no owner, no repo-pinned
expectation, and no check. This module makes the repository its own owner:

- ``EXPECTED_ORIGIN`` pins the canonical fetch URL in repo code;
- :func:`check_state` compares against it with maestro-parity
  normalization, so this guard's verdict can never diverge from the
  probe's on any input (parity beats a "cleaner" suffix-only strip);
- :func:`repair` restores a drifted-but-same-repo origin while preserving
  the operator's push intent: the drifted HTTPS fetch URL is carried to
  ``remote.origin.pushurl`` (SSH fetch + HTTPS push is the sanctioned
  steady state — the probe reads the fetch URL, while pushes ride the
  ``gh`` credential helper instead of the intermittently flaky SSH route
  to github.com:22) and the mangled branch tracking is restored. Repair
  is an explicit operator act, idempotent, and refuses foreign
  repositories.

``doctor`` surfaces the check only when ``MAGIC_HERMES_PROVENANCE_REPO``
is set (WARN-only, never FAIL, never a write); the CLI subcommand wiring
lives in ``cli.py``.

Ref: maestro finding 4bc6f3a5b0c1.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

#: The canonical fetch URL this repository pins for itself. maestro's
#: phase-1 probe pins the same value independently (config/phase1.toml);
#: the two pins are deliberate cross-checks, not a single point of truth.
EXPECTED_ORIGIN = "git@github.com:codeo1io/magic-hermes.git"

#: Environment variable opting ``doctor`` into the provenance check.
#: Unset → the check is omitted entirely, keeping default doctor output
#: byte-stable: CI clones over HTTPS while this pin is SSH, so a
#: default-on check would WARN on every healthy CI run (and break the
#: exact-count doctor contract tests). Single definition — ``cli.py``
#: imports it.
PROVENANCE_ENV = "MAGIC_HERMES_PROVENANCE_REPO"

#: Per-git-call timeout. Every call this module makes is a local config
#: read or write; only a pathological hook or a hung filesystem exceeds
#: this. Guards the doctor path against a wedged checkout.
GIT_TIMEOUT_S = 10.0

#: Runner signature injectable for tests: runs ``git -C <repo> *args``
#: and returns ``(returncode, stripped stdout)``.
GitRunner = Callable[[list[str], Path], tuple[int, str]]


class ProvenanceError(RuntimeError):
    """Repair refused or failed — actionable message, zero guessing."""


def normalize_origin_url(url: str) -> str:
    """Normalize a remote URL exactly the way maestro's probe does.

    Parity, not cleanliness: ``probes.py`` compares
    ``stdout.strip().lower().replace(".git", "")`` against
    ``spec.origin.lower().replace(".git", "")`` — and ``str.replace``
    strips *every* ``.git`` substring (``git.git@…`` → ``git@…``), not
    just a trailing suffix. A "better" suffix-only strip here could let
    this guard and the probe disagree on exotic URLs; agreement is the
    point. The probe does not ``.strip()`` its expected side, which is
    immaterial because ``EXPECTED_ORIGIN`` carries no surrounding
    whitespace.
    """

    return url.strip().lower().replace(".git", "")


def repo_slug(url: str) -> str | None:
    """Return the ``host/owner/name`` identity of a remote URL, or None.

    Understands the forms this estate actually uses — scp-like SSH
    (``git@github.com:owner/name.git``), scheme URLs with optional
    credentials and ports (``https://github.com/owner/name.git``), with
    or without the ``.git`` suffix; deep paths identify by their last
    two components. The host is part of the identity, so the same
    ``owner/name`` on a different forge is a different repository. Used
    only for same-repository reasoning (repair refusal, the pushurl
    facet) — never for the probe-parity verdict, which uses
    :func:`normalize_origin_url`.
    """

    text = url.strip().lower()
    if not text:
        return None
    if "://" in text:
        text = text.split("://", 1)[1]
    if ":" in text.split("/", 1)[0]:
        # scp-like host:path (scheme-less) — normalize to host/path so
        # the rest of the parse is scheme-independent
        head, sep, tail = text.partition(":")
        if sep:
            text = f"{head}/{tail}"
    head, sep, rest = text.partition("/")
    if not sep or not rest:
        return None
    host = head.rsplit("@", 1)[-1]  # drop git@ / token@ credentials
    host = host.split(":", 1)[0]  # drop :port
    parts = [part for part in rest.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{host}/{parts[-2]}/{parts[-1].removesuffix('.git')}"


def same_repo(url_a: str, url_b: str) -> bool:
    """True when both URLs identify the same host/owner/name repo."""

    slug_a = repo_slug(url_a)
    return slug_a is not None and slug_a == repo_slug(url_b)


@dataclass
class RepoRemoteState:
    """Read-only snapshot of one checkout's origin/branch remote config."""

    repo: Path
    repo_readable: bool = False
    #: ``remote.origin.url`` — what ``git remote get-url origin`` (and the
    #: maestro probe) reports; None when origin is absent.
    fetch_url: str | None = None
    #: ``remote.origin.pushurl`` — None when unset.
    push_url: str | None = None
    #: Current branch (``git branch --show-current``); None = detached.
    branch: str | None = None
    branch_remote: str | None = None
    branch_merge: str | None = None
    error: str | None = None


@dataclass
class ProvenanceReport:
    """Pure verdict over :class:`RepoRemoteState` — no git calls.

    ``healthy`` ⇔ no drifted facet. ``origin_match`` mirrors the probe's
    whole-string normalized equality exactly (healthy ⇒ probe PASS); the
    pushurl and branch-tracking facets are deliberately stricter, because
    this guard owns more of the 2026-09-29 incident than the probe gates
    on. When the repo is unreadable, ``repo_readable`` is the only
    drifted facet — the others were never evaluated.
    """

    repo: Path
    repo_readable: bool
    expected: str
    fetch_url: str | None
    push_url: str | None
    branch: str | None
    branch_remote: str | None
    branch_merge: str | None
    origin_match: bool
    #: Unset pushurl is the pre-incident steady state, never drift.
    pushurl_same_repo: bool
    #: None = detached HEAD (INFO-only — maestro PASSed detached
    #: checkouts before the incident; branch tracking does not apply).
    branch_tracking: bool | None
    error: str | None = None

    @property
    def drifted_facets(self) -> list[str]:
        if not self.repo_readable:
            return ["repo_readable"]
        facets: list[str] = []
        if not self.origin_match:
            facets.append("origin_match")
        if not self.pushurl_same_repo:
            facets.append("pushurl_same_repo")
        if self.branch_tracking is False:
            facets.append("branch_tracking")
        return facets

    @property
    def healthy(self) -> bool:
        return not self.drifted_facets

    def summary(self) -> str:
        """One-line human verdict naming fetch/push URLs and drift."""
        if not self.repo_readable:
            detail = self.error or "unreadable"
            return f"{self.repo}: not a readable git repository ({detail})"
        fetch = self.fetch_url or "<no origin remote>"
        push = self.push_url or "same as fetch"
        bits = [f"fetch {fetch}", f"push {push}"]
        if self.branch is not None:
            merge = (self.branch_merge or "<unset>").removeprefix(
                "refs/heads/"
            )
            bits.append(
                f"{self.branch} tracks "
                f"{self.branch_remote or '<unset>'}/{merge}"
            )
        else:
            bits.append("no current branch (detached HEAD)")
        if self.healthy:
            return ", ".join(bits)
        return f"drift in {', '.join(self.drifted_facets)}: " + ", ".join(bits)


@dataclass
class RepairResult:
    """Outcome of :func:`repair` — the writes actually performed."""

    repo: Path
    writes: list[str] = field(default_factory=list)

    @property
    def repaired(self) -> bool:
        return bool(self.writes)


def _git(args: list[str], repo: Path) -> tuple[int, str]:
    """Run ``git -C <repo> <args>``; return ``(returncode, stdout)``."""

    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def _config_get(args: list[str], repo: Path, git: GitRunner) -> str | None:
    """One ``git config --get`` read; empty/missing → None."""

    rc, out = git(args, repo)
    return out if rc == 0 and out else None


def read_state(repo: Path, git: GitRunner = _git) -> RepoRemoteState:
    """Collect the origin/branch remote state of ``repo`` (read-only).

    One local ``git -C`` plumbing call per facet, never a write, never
    the network. ``git`` is injectable so tests can fake the subprocess
    layer; real-git fixtures simply use the default.
    """

    repo = Path(repo)
    try:
        rc, _ = git(["rev-parse", "--git-dir"], repo)
        if rc != 0:
            return RepoRemoteState(
                repo=repo,
                repo_readable=False,
                error=f"git rev-parse --git-dir exited {rc}",
            )
        state = RepoRemoteState(repo=repo, repo_readable=True)

        rc, out = git(["remote", "get-url", "origin"], repo)
        state.fetch_url = out if rc == 0 and out else None

        # NB: read pushurl via `git config --get remote.origin.pushurl`,
        # NOT `git remote get-url --push` — the latter silently echoes
        # the fetch URL when no pushurl is configured, hiding exactly
        # the difference this guard exists to see.
        state.push_url = _config_get(
            ["config", "--get", "remote.origin.pushurl"], repo, git
        )

        rc, out = git(["branch", "--show-current"], repo)
        state.branch = out if rc == 0 and out else None

        if state.branch is not None:
            state.branch_remote = _config_get(
                ["config", "--get", f"branch.{state.branch}.remote"],
                repo,
                git,
            )
            state.branch_merge = _config_get(
                ["config", "--get", f"branch.{state.branch}.merge"],
                repo,
                git,
            )
        return state
    except (OSError, subprocess.SubprocessError) as exc:
        return RepoRemoteState(repo=repo, repo_readable=False, error=str(exc))


def check_state(
    state: RepoRemoteState, expected: str = EXPECTED_ORIGIN
) -> ProvenanceReport:
    """Compare ``state`` against ``expected`` with probe-parity rules."""

    origin_match = state.fetch_url is not None and (
        normalize_origin_url(state.fetch_url)
        == normalize_origin_url(expected)
    )
    if state.push_url is None:
        # Unset pushurl is the pre-incident steady state (pure SSH) and
        # never drift; only a *foreign* pushurl — which silently
        # retargets pushes — does.
        pushurl_same_repo = True
    else:
        pushurl_same_repo = same_repo(state.push_url, expected)
    if state.branch is None:
        branch_tracking: bool | None = None
    else:
        branch_tracking = (
            state.branch_remote == "origin"
            and state.branch_merge == f"refs/heads/{state.branch}"
        )
    return ProvenanceReport(
        repo=state.repo,
        repo_readable=state.repo_readable,
        expected=expected,
        fetch_url=state.fetch_url,
        push_url=state.push_url,
        branch=state.branch,
        branch_remote=state.branch_remote,
        branch_merge=state.branch_merge,
        origin_match=origin_match,
        pushurl_same_repo=pushurl_same_repo,
        branch_tracking=branch_tracking,
        error=state.error,
    )


def repair(
    repo: Path,
    state: RepoRemoteState | None = None,
    push_url: str | None = None,
    git: GitRunner = _git,
) -> RepairResult:
    """Restore a drifted origin while preserving push intent (explicit).

    Ordering (each step writes only when something actually drifted):

    1. Refusals, before any write: the path must be a readable git repo,
       ``origin`` must exist, and it must identify the SAME repository as
       ``EXPECTED_ORIGIN`` — a checkout deliberately pointed at a fork is
       an intentional state, not drift to rewrite. A foreign
       ``pushurl`` is likewise never rewritten on the guard's own
       initiative: repair only carries or applies push intent, it never
       guesses one. The explicit remedy is ``push_url`` — a same-repo
       value supplied there overrides the refusal.
    2. Fetch URL drifted (same repo): rewrite to ``EXPECTED_ORIGIN`` via
       ``git remote set-url`` and, when no pushurl is configured, carry
       the old fetch URL to ``remote.origin.pushurl`` so the operator's
       push route (the gh-credential HTTPS route in the 2026-09-29
       incident) survives the repair.
    3. An explicit ``push_url`` (validated same-repo) is applied
       verbatim via ``git remote set-url --push``.
    4. On a branch: restore ``branch.<b>.remote`` to ``origin`` and
       ``branch.<b>.merge`` to ``refs/heads/<b>`` (the incident's
       mangled-tracking repair).

    Idempotent: a healthy repo (and no explicit ``push_url``) repairs to
    zero writes.

    Raises:
        ProvenanceError: refused (before any write), or a git write
            failed mid-repair — repair is idempotent, re-run it to
            complete.
    """

    repo = Path(repo)
    state = state if state is not None else read_state(repo, git=git)
    if not state.repo_readable:
        detail = f" ({state.error})" if state.error else ""
        raise ProvenanceError(f"{repo} is not a readable git repository{detail}")
    if state.fetch_url is None:
        raise ProvenanceError(
            f"{repo} has no 'origin' remote — refusing to guess one; add "
            f"it explicitly: git remote add origin {EXPECTED_ORIGIN}"
        )
    if not same_repo(state.fetch_url, EXPECTED_ORIGIN):
        raise ProvenanceError(
            f"origin points at a different repository ({state.fetch_url}); "
            f"refusing to rewrite a checkout that may be pointed there "
            f"deliberately — re-point it explicitly if this checkout "
            f"should track {EXPECTED_ORIGIN}"
        )
    if push_url is not None and not same_repo(push_url, EXPECTED_ORIGIN):
        raise ProvenanceError(
            f"push URL {push_url} points at a different repository; "
            "refusing to set a push route off-repository"
        )
    if (
        state.push_url is not None
        and push_url is None
        and not same_repo(state.push_url, EXPECTED_ORIGIN)
    ):
        raise ProvenanceError(
            f"remote.origin.pushurl points at a different repository "
            f"({state.push_url}); refusing to rewrite it — fix it "
            "explicitly with --push-url"
        )

    result = RepairResult(repo=repo)
    try:
        fetch_drifted = normalize_origin_url(state.fetch_url) != (
            normalize_origin_url(EXPECTED_ORIGIN)
        )
        if fetch_drifted:
            previous = state.fetch_url
            rc, out = git(["remote", "set-url", "origin", EXPECTED_ORIGIN], repo)
            if rc != 0:
                raise ProvenanceError(
                    f"git remote set-url origin exited {rc}" + _out(out)
                )
            result.writes.append(
                f"remote.origin.url: {previous} -> {EXPECTED_ORIGIN}"
            )
            if state.push_url is None and push_url is None:
                rc, out = git(
                    ["remote", "set-url", "--push", "origin", previous], repo
                )
                if rc != 0:
                    raise ProvenanceError(
                        f"git remote set-url --push exited {rc}" + _out(out)
                    )
                result.writes.append(
                    f"remote.origin.pushurl: {previous} (push intent carried)"
                )
        if push_url is not None:
            rc, out = git(["remote", "set-url", "--push", "origin", push_url], repo)
            if rc != 0:
                raise ProvenanceError(
                    f"git remote set-url --push exited {rc}" + _out(out)
                )
            result.writes.append(f"remote.origin.pushurl: {push_url}")
        if state.branch is not None:
            want_merge = f"refs/heads/{state.branch}"
            if state.branch_remote != "origin":
                rc, out = git(
                    ["config", f"branch.{state.branch}.remote", "origin"], repo
                )
                if rc != 0:
                    raise ProvenanceError(
                        f"git config branch.{state.branch}.remote "
                        f"exited {rc}" + _out(out)
                    )
                result.writes.append(f"branch.{state.branch}.remote: origin")
            if state.branch_merge != want_merge:
                rc, out = git(
                    ["config", f"branch.{state.branch}.merge", want_merge], repo
                )
                if rc != 0:
                    raise ProvenanceError(
                        f"git config branch.{state.branch}.merge "
                        f"exited {rc}" + _out(out)
                    )
                result.writes.append(
                    f"branch.{state.branch}.merge: {want_merge}"
                )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvenanceError(
            f"git failed mid-repair ({exc}); repair is idempotent — "
            "re-run `magic-hermes provenance --repair` to complete it"
        ) from exc
    return result


def _out(out: str) -> str:
    """Append captured stdout to an error message when git said anything."""

    return f": {out}" if out else ""


if __name__ == "__main__":  # ``python -m magic_hermes.provenance`` (U2 rider)
    # Module-runnable vehicle for the provenance command. Before this
    # guard, ``python3 -m magic_hermes.provenance`` imported the module
    # and exited 0 silently on any input — a false-healthy no-op observed
    # against the drifted canonical checkout on 2026-09-29. Everything
    # stays inside the guard, so the module's import surface is untouched
    # and import-inert (``cli`` imports this module at module level, and
    # importing it back here — at ``-m`` run time only — cannot cycle).
    # Exit parity is by construction: this dispatches the same
    # ``cli.main`` the installed console script uses.
    import sys

    from .cli import main as _main

    raise SystemExit(_main(["provenance"] + sys.argv[1:]))
