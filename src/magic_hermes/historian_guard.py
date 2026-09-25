"""Shared-store classification guard for ``historian_runs`` telemetry.

Upstream ``@cortexkit/pi-magic-context`` seeds historian run telemetry with
``status='failed'`` and only some code paths classify a failure reason. The
benign early-return paths (drain budget, nothing to process, ...) commit
rows with ``status='failed'`` AND ``failure_reason IS NULL``, which downstream
consumers (maestro's ``phase1.context.operational`` evaluator) count as
operational failures — the root cause of finding d59758598379.

``historian_runs`` is write-only in the entire upstream dist (a single
INSERT, zero SELECTs), so this module installs a server-side classification
at the store's recording boundary: an ``AFTER INSERT`` trigger that
reclassifies exactly the defect class ``(status='failed' AND
failure_reason IS NULL)`` to upstream's own benign vocabulary
(``status='noop'`` plus an explicit marker reason). Because the trigger
lives in the DB itself, every writer connection — including
already-running sessions — is covered immediately, and npm package churn
never removes it. Reasoned failures, successes, and existing noop rows
pass through untouched; historical rows are never rewritten.

Ref: maestro finding d59758598379 (phase1.context.operational).
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Machine-local name of the classification trigger.
TRIGGER_NAME = "mh_historian_classification_guard"

#: Failure reason written for rows the guard reclassifies. Doubles as the
#: visibility marker: doctor reports the trailing-24h count of rows
#: carrying it, so any future genuine NULL-reason failure stays inspectable.
MARKER_REASON = (
    "unclassified historian early-return (magic-hermes classification guard v1)"
)

#: Set to the first upstream release that carries the telemetry
#: classification fix once validated; until then the guard stays (KTD-5).
RETIRES_AT_UPSTREAM: str | None = None

#: Write-lock wait for the one-shot deploy transaction. The shared store is
#: WAL; conductor sessions mint rows every few seconds, so a short patient
#: timeout beats failing under load.
BUSY_TIMEOUT_MS = 10_000

#: Trailing window for ``marker_rows_24h`` (milliseconds — ``created_at``
#: is epoch-milliseconds in the live store).
MARKER_WINDOW_MS = 24 * 60 * 60 * 1000

#: The exact trigger DDL this module owns. Single source of truth: used for
#: CREATE, for drift comparison, and (normalized) by ``guard_status``.
GUARD_DDL = f"""\
CREATE TRIGGER {TRIGGER_NAME}
AFTER INSERT ON historian_runs
WHEN NEW.status = 'failed' AND NEW.failure_reason IS NULL
BEGIN
  UPDATE historian_runs
     SET status = 'noop',
         failure_reason = '{MARKER_REASON}'
   WHERE id = NEW.id;
END;
"""

#: Production-faithful ``historian_runs`` shape (22 columns, captured from
#: the live shared store 2026-09-25) used for fixture databases.
HISTORIAN_RUNS_DDL = """\
CREATE TABLE historian_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL,
      harness TEXT NOT NULL DEFAULT 'opencode',
      subagent_invocation_id INTEGER,
      run_kind TEXT NOT NULL,
      status TEXT NOT NULL,
      failure_reason TEXT,
      chunk_start_ordinal INTEGER,
      chunk_end_ordinal INTEGER,
      unprocessed_from INTEGER,
      compartments_produced INTEGER NOT NULL DEFAULT 0,
      compartment_id_min INTEGER,
      compartment_id_max INTEGER,
      facts_emitted INTEGER NOT NULL DEFAULT 0,
      facts_by_category_json TEXT,
      events_emitted INTEGER NOT NULL DEFAULT 0,
      importance_min INTEGER,
      importance_max INTEGER,
      importance_avg REAL,
      discarded_last INTEGER NOT NULL DEFAULT 0,
      legacy INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    )
"""

_IF_NOT_EXISTS_RE = re.compile(r"^create\s+trigger\s+(if\s+not\s+exists\s+)?", re.I)


class GuardError(RuntimeError):
    """Raised when the guard cannot be applied to a store."""


def _normalize_ddl(sql: str) -> str:
    """Collapse a trigger definition to a comparable canonical form.

    Whitespace and casing differences are drift-comparison noise; the
    trigger *body* is not. ``IF NOT EXISTS`` is stripped because SQLite may
    store the clause depending on how the trigger was created, and the
    trailing statement semicolon is dropped because ``sqlite_master`` does
    not store it.
    """

    text = " ".join(sql.split())
    text = _IF_NOT_EXISTS_RE.sub("create trigger ", text, count=1).strip()
    return text.rstrip(";")


def _expected_ddl() -> str:
    return _normalize_ddl(GUARD_DDL)


@dataclass
class GuardStatus:
    """Read-only snapshot of the guard's state on one store."""

    present: bool
    matches: bool = False
    marker_rows_24h: int = 0
    error: str | None = None


@dataclass
class GuardResult:
    """Outcome of ``apply_guard``."""

    db_path: Path
    applied: bool = False
    repaired: bool = False
    #: Semantic verification failures (empty list == verified).
    verification_failures: list[str] = field(default_factory=list)


def _connect_rw(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    con.execute(f"pragma busy_timeout = {BUSY_TIMEOUT_MS}")
    return con


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _current_trigger_sql(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "select sql from sqlite_master where type = 'trigger' and name = ?",
        (TRIGGER_NAME,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def apply_guard(db_path: Path, *, verify: bool = True) -> GuardResult:
    """Install (or repair) the classification trigger on ``db_path``.

    Idempotent: when the live trigger already matches the canonical DDL
    nothing is written. On drift (any difference after normalization) the
    trigger is dropped and re-created from the owned DDL inside one
    ``BEGIN IMMEDIATE`` transaction. With ``verify`` (default) a fixture-database
    semantic self-check runs afterwards — the live store is never probed
    with test rows.
    """

    db_path = Path(db_path)
    if not db_path.is_file():
        raise GuardError(
            f"no shared context store at {db_path} — it is created on first "
            "successful runtime bind; nothing to guard"
        )

    result = GuardResult(db_path=db_path)
    con = _connect_rw(db_path)
    try:
        if not _table_exists(con, "historian_runs"):
            raise GuardError(
                f"{db_path} has no historian_runs table; refusing to guard "
                "a store with an unexpected schema"
            )
        current = _current_trigger_sql(con)
        if current is not None and _normalize_ddl(current) == _expected_ddl():
            result.applied = True
        else:
            result.repaired = current is not None
            con.execute("begin immediate")
            con.execute(f"drop trigger if exists {TRIGGER_NAME}")
            con.execute(GUARD_DDL)
            con.execute("commit")
            result.applied = True
    finally:
        con.close()

    if verify:
        result.verification_failures = verify_guard_semantics()
    return result


def remove_guard(db_path: Path) -> bool:
    """Drop the classification trigger. Returns True if one was removed.

    Retirement path (KTD-5): removing a guard from a store that has none is
    a successful no-op returning False, so ``guard remove`` stays idempotent.
    """

    db_path = Path(db_path)
    if not db_path.is_file():
        return False
    con = _connect_rw(db_path)
    try:
        if _current_trigger_sql(con) is None:
            return False
        con.execute("begin immediate")
        con.execute(f"drop trigger if exists {TRIGGER_NAME}")
        con.execute("commit")
        return True
    finally:
        con.close()


def guard_status(db_path: Path) -> GuardStatus:
    """Read the guard's state without writing anything."""

    db_path = Path(db_path)
    if not db_path.is_file():
        return GuardStatus(present=False)
    try:
        con = _connect_ro(db_path)
        try:
            current = _current_trigger_sql(con)
            present = current is not None
            matches = present and _normalize_ddl(current) == _expected_ddl()
            cutoff = int(time.time() * 1000) - MARKER_WINDOW_MS
            row = con.execute(
                "select count(*) from historian_runs "
                "where failure_reason = ? and created_at >= ?",
                (MARKER_REASON, cutoff),
            ).fetchone()
            return GuardStatus(
                present=present,
                matches=matches,
                marker_rows_24h=int(row[0]) if row else 0,
            )
        finally:
            con.close()
    except sqlite3.Error as exc:
        return GuardStatus(present=False, error=str(exc))


def make_fixture_db(db_path: Path) -> Path:
    """Create a fixture store with the production ``historian_runs`` shape."""

    db_path = Path(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(HISTORIAN_RUNS_DDL)
        con.commit()
    finally:
        con.close()
    return db_path


def insert_run(
    con: sqlite3.Connection,
    *,
    session_id: str = "s-test",
    harness: str = "pi",
    status: str = "failed",
    failure_reason: str | None = None,
    created_at: int | None = None,
) -> int:
    """Insert a minimal-but-valid historian run row; return its id.

    Columns mirror the telemetry the upstream runner actually populates on
    its early-return paths (status seeded before classification, most
    counters defaulted by the schema). Commits so callers may close the
    connection freely.
    """

    cursor = con.execute(
        "insert into historian_runs "
        "(session_id, harness, run_kind, status, failure_reason, created_at) "
        "values (?, ?, 'automatic', ?, ?, ?)",
        (
            session_id,
            harness,
            status,
            failure_reason,
            created_at if created_at is not None else int(time.time() * 1000),
        ),
    )
    con.commit()
    return int(cursor.lastrowid)


def verify_guard_semantics() -> list[str]:
    """Prove the trigger's classification contract on a throwaway fixture.

    Builds a temp fixture DB (never touches any live store), applies the
    guard through the production path, and asserts the four classification
    lanes: defect-class rows become ``noop``+marker, while reasoned
    failures, successes, and existing noop rows pass through untouched.
    Returns a list of human-readable failures — empty means verified.
    """

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mh-guard-verify-") as tmp:
        fixture = make_fixture_db(Path(tmp) / "fixture.db")
        apply_guard(fixture, verify=False)

        probes: list[tuple[str, str, str | None, str, str | None]] = [
            # label, inserted status, inserted reason, expected status, expected reason
            ("defect-class failed/NULL", "failed", None, "noop", MARKER_REASON),
            ("reasoned failure", "failed", "drain budget exhausted", "failed",
             "drain budget exhausted"),
            ("success", "success", None, "success", None),
            ("existing noop", "noop", "already classified", "noop",
             "already classified"),
        ]
        con = sqlite3.connect(fixture)
        try:
            for label, status, reason, want_status, want_reason in probes:
                row_id = insert_run(con, status=status, failure_reason=reason)
                row = con.execute(
                    "select status, failure_reason from historian_runs where id = ?",
                    (row_id,),
                ).fetchone()
                if row != (want_status, want_reason):
                    failures.append(
                        f"{label}: expected ({want_status!r}, {want_reason!r}), "
                        f"got {row!r}"
                    )
        finally:
            con.close()
    return failures
