"""Contract and honesty invariants for `magic-hermes doctor` (R3).

Maestro's phase-2 verdict (maestro phase2.py) is a single-shot
``rc == 0 and "FAIL 0" in stdout`` within a 90s budget, with no retry.
These tests pin the invariants that keep the doctor honest under that
contract (finding c7d63424, plan units U5/U7):

- a degraded (``skipped:``) integrity scan is a WARN, never a FAIL, names
  the gate that tripped with its numbers, and points at the offline audit
  command (R1 honesty);
- no FAIL-status message may contain the literal substring ``FAIL 0`` —
  the verdict greps stdout, so such a message would masquerade as a pass;
- a real quick_check keeps its PASS wording;
- ``--json`` carries the sidecar's additive scan fields and its summary
  counts stay consistent with the text summary;
- the ``--full-integrity`` override reaches the sidecar verbatim (RQ7);
- the fast-fail retry constants stay inside maestro's budget (R2).

The Node sidecar is never spawned here: RuntimeClient is patched, and the
doctor report shapes mirror what bridge/runtime.mjs ``runtimeDoctor``
returns once the bounded-scan rider is active:
``skipped:store-size <bytes> > <gate>``, ``skipped:probe-ms <ms> > <gate>``,
``skipped:probe-error <message>``, with ``scan_mode`` ``full``/``metadata``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from magic_hermes import cli


def make_package(root: Path, version: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@cortexkit/pi-magic-context", "version": version}),
        encoding="utf-8",
    )
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist" / "index.js").write_text("// stub\n", encoding="utf-8")
    return root


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


def _wire_config():
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


def _run_doctor(
    tmp_path,
    monkeypatch,
    isolated_home,
    doctor_report,
    full_integrity=False,
    json_output=False,
    sidecar_error=None,
):
    """Run cli.run_doctor against a patched sidecar.

    ``doctor_report`` is the dict the mocked ``doctor`` RPC returns;
    ``sidecar_error`` (if set) makes every sidecar call raise instead.
    Returns ``(rc, doctor_call_params)``; the caller reads stdout via
    its own ``capsys``.
    """
    from magic_hermes import historian_guard as hg

    db = tmp_path / "context.db"
    if not db.exists():
        hg.make_fixture_db(db)
    monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
    _wire_config()

    tested = cli.tested_magic_context_version()
    package = make_package(tmp_path / "pkg", tested)
    series = ".".join(map(str, cli.supported_magic_context_series()))

    sidecar = mock.MagicMock()

    def _call(method, params=None, timeout=60):
        if sidecar_error is not None:
            raise sidecar_error
        if method == "hello":
            return {"harness": "hermes", "package_version": tested}
        report = {
            "database_health": "ok",
            "core_symbols_ready": True,
            "supported_series": series,
        }
        report.update(doctor_report)
        return report

    sidecar.call.side_effect = _call
    client = mock.MagicMock()
    client.__enter__.return_value = sidecar
    client.__exit__.return_value = False

    with (
        mock.patch.object(
            cli, "discover_installations", return_value=[(package, tested)]
        ),
        mock.patch.object(cli, "RuntimeClient", return_value=client),
        # the retry backoff is real wall-clock sleep; these tests exercise
        # the report mapping, not the timing, so zero it out
        mock.patch.object(cli, "_SIDECAR_RETRY_BACKOFF_S", 0.0),
    ):
        rc = cli.run_doctor(json_output=json_output, full_integrity=full_integrity)
    doctor_calls = [c for c in sidecar.call.call_args_list if c.args[0] == "doctor"]
    params = doctor_calls[0].args[1] if doctor_calls else None
    return rc, params


def _degraded_report():
    # the exact emission shape of runtime.mjs runtimeDoctor when the size
    # gate trips on the live 3.4 GB store
    return {
        "database_health": "skipped:store-size 3690000000 > 1610612736",
        "scan_mode": "metadata",
        "store_bytes": 3690000000,
        "probe_ms": 4.3,
    }


class TestDegradedScanContract:
    def test_store_gate_skip_is_warn_and_keeps_verdict(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, _degraded_report())
        out = capsys.readouterr().out
        # maestro contract: rc==0 and "FAIL 0" in stdout
        assert rc == 0
        assert "FAIL 0" in out
        # honest degradation: exactly one WARN names the gate and numbers
        assert out.count("integrity scan skipped") == 1
        assert "store-size" in out
        assert "3690000000" in out
        assert "1610612736" in out
        # actionable: the offline audit command is named
        assert "PRAGMA quick_check" in out
        assert "sqlite3" in out
        # the quick_check PASS wording is reserved for real scans
        assert "quick_check ok" not in out

    def test_probe_gate_skip_keeps_the_same_contract(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        report = _degraded_report()
        report["database_health"] = "skipped:probe-ms 4300.5 > 2000"
        report["probe_ms"] = 4300.5
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, report)
        out = capsys.readouterr().out
        assert rc == 0
        assert "FAIL 0" in out
        assert out.count("integrity scan skipped") == 1
        assert "probe-ms" in out
        assert "4300.5" in out

    def test_probe_error_skip_keeps_the_same_contract(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # a store whose catalog read itself throws (locked or damaged
        # under contention) degrades exactly like a gate skip: WARN, not
        # FAIL, with the probe error quoted — the bridge omits probe_ms
        # on this path because the probe never completed
        report = _degraded_report()
        report.pop("probe_ms")
        report["database_health"] = (
            "skipped:probe-error SqliteError: database is locked"
        )
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, report)
        out = capsys.readouterr().out
        assert rc == 0
        assert "FAIL 0" in out
        assert out.count("integrity scan skipped") == 1
        assert "probe-error" in out
        assert "database is locked" in out

    def test_real_scan_pass_wording_unchanged(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {"scan_mode": "quick_check"},
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Sidecar opened the shared DB (quick_check ok)" in out


class TestJsonContract:
    def test_json_carries_scan_fields_and_consistent_counts(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, _ = _run_doctor(
            tmp_path, monkeypatch, isolated_home, _degraded_report(), json_output=True
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["scan_mode"] == "metadata"
        assert payload["store_bytes"] == 3690000000
        assert payload["probe_ms"] == 4.3
        # summary counts must equal the actual check counts
        counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "INFO": 0}
        for check in payload["checks"]:
            counts[check["status"]] += 1
        assert payload["summary"] == {
            "pass": counts["PASS"],
            "warn": counts["WARN"],
            "fail": counts["FAIL"],
        }
        assert payload["summary"]["fail"] == 0

        # the text summary reports the same counts as the JSON summary
        _run_doctor(tmp_path, monkeypatch, isolated_home, _degraded_report())
        text = capsys.readouterr().out
        summary_line = next(
            line for line in text.splitlines() if "Summary: PASS" in line
        )
        assert f"PASS {payload['summary']['pass']}" in summary_line
        assert f"WARN {payload['summary']['warn']}" in summary_line
        assert f"FAIL {payload['summary']['fail']}" in summary_line

    def test_json_without_scan_fields_omits_them(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # an older sidecar (no rider) reports neither scan_mode nor health
        # prefixes it does not know — the JSON shape must not invent keys
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, {}, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert "scan_mode" not in payload
        assert "store_bytes" not in payload
        assert "probe_ms" not in payload


class TestHonestyInvariant:
    """No FAIL-status message may contain the literal "FAIL 0".

    maestro's verdict is `rc == 0 and "FAIL 0" in stdout`; a FAIL message
    embedding that substring would let a failing doctor masquerade as a
    pass. Checked across the hostile report shapes the doctor can emit.
    """

    @pytest.mark.parametrize(
        "report,error",
        [
            (
                {"database_health": "error:database is locked"},
                None,
            ),
            (
                {},
                RuntimeError(
                    "Runtime exited during doctor with status 1; "
                    "stderr: Error: database is locked"
                ),
            ),
            (
                {"database_health": "skipped:store-size 3690000000 > 1610612736"},
                None,
            ),
        ],
    )
    def test_no_fail_message_contains_fail_zero(
        self, tmp_path, monkeypatch, isolated_home, capsys, report, error
    ):
        _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            report,
            json_output=True,
            sidecar_error=error,
        )
        payload = json.loads(capsys.readouterr().out)
        fails = [c for c in payload["checks"] if c["status"] == "FAIL"]
        assert all("FAIL 0" not in c["message"] for c in fails)


class TestFullIntegrityPlumbing:
    def test_flag_reaches_sidecar_as_param(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _, params = _run_doctor(
            tmp_path, monkeypatch, isolated_home, {}, full_integrity=True
        )
        capsys.readouterr()
        assert params == {"full_integrity": True}

    def test_default_is_not_full_integrity(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _, params = _run_doctor(tmp_path, monkeypatch, isolated_home, {})
        capsys.readouterr()
        assert params == {"full_integrity": False}

    def test_parser_accepts_flag_and_main_plumbs_it(self):
        args = cli.build_parser().parse_args(["doctor", "--full-integrity"])
        assert args.full_integrity is True
        args = cli.build_parser().parse_args(["doctor"])
        assert args.full_integrity is False
        with mock.patch.object(cli, "run_doctor", return_value=0) as run:
            cli.main(["doctor", "--full-integrity"])
        assert run.call_args.kwargs.get("full_integrity") is True


class TestRetryBudget:
    """R2 (finding c7d63424) — the doctor sidecar pair retries exactly
    once, and only on a fast failure. The constants are the whole budget:
    fast-fail window + backoff + one fast second boot lock must fit
    inside maestro's 90 s single-shot verdict with room for the direct
    store checks, so their sum is pinned at 15 + 3 = 18 s <= 20 s."""

    def test_constants_are_positive_floats_within_budget(self):
        fast = cli._SIDECAR_FAST_FAIL_S
        backoff = cli._SIDECAR_RETRY_BACKOFF_S
        assert isinstance(fast, float) and fast > 0
        assert isinstance(backoff, float) and backoff > 0
        assert fast + backoff <= 20.0
