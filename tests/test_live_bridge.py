"""Live integration gate: the Python connector against the real Node bridge daemon.

Spawns `node bridge/daemon.mjs` with a temp runtime dir and a temp copy of a
fixture shared DB, then drives the full connector stack through the actual
subc wire. Skipped when node is unavailable.
"""

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from magic_hermes.session import MagicContextSession

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "bridge" / "daemon.mjs"


def _node_available() -> bool:
    return shutil.which("node") is not None


@pytest.fixture()
def bridge(tmp_path):
    if not _node_available():
        pytest.skip("node not available for live bridge gate")

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    db = tmp_path / "context.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_path TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
          normalized_hash TEXT NOT NULL, scope TEXT DEFAULT 'project',
          source_session_id TEXT, source_type TEXT, seen_count INTEGER DEFAULT 1,
          first_seen_at INTEGER, created_at INTEGER, updated_at INTEGER,
          last_seen_at INTEGER, status TEXT DEFAULT 'active');
        CREATE TABLE notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          type TEXT DEFAULT 'session', status TEXT DEFAULT 'active', content TEXT,
          session_id TEXT, project_path TEXT, created_at INTEGER, updated_at INTEGER,
          harness TEXT DEFAULT 'opencode');
        """
    )
    con.commit()
    con.close()

    proc = subprocess.Popen(
        [
            "node",
            str(BRIDGE),
            "--db",
            str(db),
            "--runtime-dir",
            str(runtime),
            "--project-root",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        info = json.loads(line)
    except Exception:
        proc.kill()
        out, errout = proc.communicate(timeout=5)
        pytest.fail(f"bridge failed to start: {errout or out}")
    yield {"conn_path": info["conn_path"], "db": db, "proc": proc}
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_live_bridge_end_to_end(bridge, tmp_path):
    os.environ["SUBC_CONNECTION_FILE"] = bridge["conn_path"]
    session = MagicContextSession(project_root=str(tmp_path), session_id="live-1")
    session.connect(retries=8)
    try:
        # session lifecycle
        session.call(
            "session.begin",
            {"session_id": "live-1", "platform": "hermes", "model": "glm-5.2"},
        )

        # memory write -> search -> list -> expand -> archive roundtrip
        w = session.call(
            "memory.write",
            {"content": "bridge gate: sqlite wal is on", "category": "constraints"},
        )
        assert w["id"] > 0
        dup = session.call("memory.write", {"content": "bridge gate: sqlite wal is on"})
        assert dup["duplicate"] is True and dup["id"] == w["id"]
        s = session.call("memory.search", {"query": "wal sqlite", "limit": 5})
        assert any("wal" in r["content"].lower() for r in s["results"])
        lst = session.call("memory.list", {})
        assert any(m["id"] == w["id"] for m in lst["memories"])
        exp = session.call("memory.expand", {"id": w["id"]})
        assert exp["id"] == w["id"]
        session.call("memory.archive", {"ids": [w["id"]]})
        s2 = session.call("memory.search", {"query": "wal sqlite", "limit": 5})
        assert all(r["id"] != w["id"] for r in s2["results"])

        # notes roundtrip
        session.call("notes.manage", {"action": "write", "content": "live gate note"})
        st = session.call("notes.status", {})
        assert any("live gate note" in n["content"] for n in st["notes"])

        # compaction roundtrip
        msgs = [{"role": "system", "content": "sys"}] + [
            {"role": "user", "content": f"m{i}"} for i in range(20)
        ]
        out = session.call(
            "context.compact", {"messages": msgs, "current_tokens": 100000}
        )
        assert out["messages"][0]["role"] == "system"
        assert len(out["messages"]) < len(msgs)

        # prune roundtrip
        pr = session.call(
            "context.prune_tool_results",
            {"messages": [{"role": "tool", "content": "x" * 9000}]},
        )
        assert pr["pruned"] == 1

        session.call("session.end", {"session_id": "live-1", "message_count": 21})
    finally:
        session.close()

    # bridge-local bookkeeping landed
    hermes = sqlite3.connect(bridge["db"].parent / "runtime" / "hermes.db")
    rows = hermes.execute(
        "SELECT session_id, message_count FROM mh_sessions WHERE session_id='live-1'"
    ).fetchall()
    hermes.close()
    assert rows and rows[0][0] == "live-1"
