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
import os
import subprocess
import sys
import time
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
    hello_report=None,
    doctor_error=None,
    ctor_sink=None,
    config_writer=None,
):
    """Run cli.run_doctor against a patched sidecar.

    ``doctor_report`` is the dict the mocked ``doctor`` RPC returns;
    ``sidecar_error`` (if set) makes every sidecar call raise instead;
    ``hello_report`` merges additive fields into the hello response
    (e.g. ``bind_state``); ``doctor_error`` makes only the ``doctor``
    call raise (hello stays healthy — the deferred-bind failure
    channel); ``ctor_sink`` (a list) collects the RuntimeClient
    constructor kwargs; ``config_writer`` replaces the default wired
    config fixture (the lease-aware wiring-row tests).
    Returns ``(rc, doctor_call_params)``; the caller reads stdout via
    its own ``capsys``.
    """
    from magic_hermes import historian_guard as hg

    db = tmp_path / "context.db"
    if not db.exists():
        hg.make_fixture_db(db)
    monkeypatch.setenv("MAGIC_CONTEXT_DB_PATH", str(db))
    if config_writer is None:
        _wire_config()
    else:
        config_writer()

    tested = cli.tested_magic_context_version()
    package = make_package(tmp_path / "pkg", tested)
    series = ".".join(map(str, cli.supported_magic_context_series()))

    sidecar = mock.MagicMock()

    def _call(method, params=None, timeout=60):
        if sidecar_error is not None:
            raise sidecar_error
        if method == "hello":
            hello = {"harness": "hermes", "package_version": tested}
            if hello_report:
                hello.update(hello_report)
            return hello
        if doctor_error is not None:
            raise doctor_error
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

    def _capture_ctor(*args, **kwargs):
        if ctor_sink is not None:
            ctor_sink.append(kwargs)
        return client

    with (
        mock.patch.object(
            cli, "discover_installations", return_value=[(package, tested)]
        ),
        mock.patch.object(cli, "RuntimeClient", side_effect=_capture_ctor),
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
        # prefixes it does not know — the JSON shape must not invent keys.
        # The deferred-bind fields (U2) are equally additive: an old
        # sidecar reports no bind_state and the payload must not mint one.
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, {}, json_output=True)
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert "scan_mode" not in payload
        assert "store_bytes" not in payload
        assert "probe_ms" not in payload
        assert "bind_state" not in payload
        assert "bind_waited_ms" not in payload


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
            # U3 (finding c7d63424): the typed lane-skew FAIL row must
            # obey the honesty invariant like every other FAIL row
            (
                {},
                RuntimeError(
                    "Runtime exited during hello (status 1); "
                    "stderr: [magic-context] storage fatal: refusing to "
                    "open context.db; upstream migration lane v95 is newer "
                    "than this binary supports (max v94). A pinned or stale "
                    "plugin is likely sharing this database"
                ),
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


class TestLaneSkewTypedVerdict:
    """U3 (finding c7d63424) — the storage-fence refusal renders ONE
    typed FAIL row in the machine-readable report: the fence's own lane
    numbers, the sanctioned adoption path, and the underlying refusal
    quoted for evidence. maestro's verdict sees FAIL 1 / rc 1 (the
    escalation lane), with count semantics identical to the generic
    sidecar-failure row it replaces (D5)."""

    FENCE = (
        "Runtime exited during hello (status 1); "
        "stderr: [magic-context] storage fatal: refusing to open "
        "context.db; upstream migration lane v95 is newer than this "
        "binary supports (max v94). A pinned or stale plugin is likely "
        "sharing this database with a newer instance; update or unpin "
        "Magic Context with 'npx @cortexkit/magic-context@latest "
        "doctor --force', then restart."
    )

    def test_json_carries_the_typed_row_with_fence_numbers(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            json_output=True,
            sidecar_error=RuntimeError(self.FENCE),
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 1
        fails = [c for c in payload["checks"] if c["status"] == "FAIL"]
        assert len(fails) == 1
        message = fails[0]["message"]
        assert "shared-store lane skew" in message
        assert "lane v95" in message
        assert "max v94" in message
        # D3: the sanctioned adoption path is the guidance
        assert "scripts/next_magic_context_release.py" in message
        assert "scripts/sync_magic_context_release.py" in message
        assert "magic-hermes install" in message
        # the underlying refusal stays quoted — it is the evidence
        assert "storage fatal" in message
        assert payload["summary"]["fail"] == 1

    def test_text_summary_renders_the_typed_row_once(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # D5 holds in text mode too: exactly one typed FAIL row, the
        # summary stays FAIL 1, and no row embeds the verdict substring
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            sidecar_error=RuntimeError(self.FENCE),
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert out.count("shared-store lane skew") == 1
        summary_line = next(line for line in out.splitlines() if "Summary:" in line)
        assert "FAIL 1" in summary_line
        assert "FAIL 0" not in out


class TestFullIntegrityPlumbing:
    def test_flag_reaches_sidecar_as_param(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _, params = _run_doctor(
            tmp_path, monkeypatch, isolated_home, {}, full_integrity=True
        )
        capsys.readouterr()
        assert params["full_integrity"] is True

    def test_default_is_not_full_integrity(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _, params = _run_doctor(tmp_path, monkeypatch, isolated_home, {})
        capsys.readouterr()
        assert params["full_integrity"] is False

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

    def test_wall_budget_fits_observer_shot(self):
        # U15/U17 (plan 0d118f58): every attempt timeout derives from the
        # remaining wall budget, so the budget itself — plus the render
        # reserve kept back for printing — is the process total that must
        # fit inside maestro's 90 s single-shot kill with room to spare.
        budget = cli._DOCTOR_WALL_BUDGET_S
        reserve = cli._DOCTOR_WALL_RENDER_RESERVE_S
        assert isinstance(budget, float) and budget > 0
        assert isinstance(reserve, float) and 0.0 < reserve < 1.0
        assert budget + reserve < 90.0
        assert budget >= cli._SIDECAR_FAST_FAIL_S + cli._SIDECAR_RETRY_BACKOFF_S


class TestDeferredBindContract:
    """U2 (plan 2026-10-31, Mode 1) — the doctor sidecar is spawned in
    deferred-bind mode, the bounded wait is passed in the doctor call,
    and bind contention renders as the honest WARN lane.

    The bridge side of the contract (U1) answers hello before the
    shared-store bind resolves; these tests pin the Python side against
    the pinned response shapes — the Node sidecar is never spawned."""

    def test_bind_contention_renders_honest_warn_verdict(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        report = {
            "database_health": (
                "skipped:bind-contention bind pending after 68000ms "
                "(budget 68000ms)"
            ),
            "scan_mode": "bind-pending",
            "bind_state": "pending",
            "bind_waited_ms": 68000,
        }
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, report)
        out = capsys.readouterr().out
        # maestro contract: rc==0 and "FAIL 0" in stdout
        assert rc == 0
        assert "FAIL 0" in out
        # exactly one WARN row names the contention with its numbers
        assert out.count("bind-contention") == 1
        assert "68000ms" in out
        assert "PRAGMA quick_check" in out
        # honesty (U16c discipline): the row must NOT claim the store
        # opened or that probe reads succeeded — the bind never landed
        warn_line = next(
            line for line in out.splitlines() if "bind-contention" in line
        )
        assert "the store opened" not in warn_line
        assert "probe reads succeeded" not in warn_line
        assert "bind never landed" in warn_line
        # the quick_check PASS wording is reserved for real scans
        assert "quick_check ok" not in out

    def test_doctor_call_carries_bounded_bind_wait_and_defer_env(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        ctor_kwargs: list[dict] = []
        _, params = _run_doctor(
            tmp_path, monkeypatch, isolated_home, {}, ctor_sink=ctor_kwargs
        )
        capsys.readouterr()
        # the sidecar is spawned in deferred-bind mode
        assert ctor_kwargs, "the doctor must construct a RuntimeClient"
        assert all(
            kw.get("extra_env") == {"MAGIC_HERMES_DEFER_BIND": "1"}
            for kw in ctor_kwargs
        )
        # the bounded wait rides the doctor call params: positive, and
        # derived from the wall budget minus the margin (75 - 0.25 - 2)
        assert params is not None
        bind_wait_s = params["bind_wait_s"]
        assert isinstance(bind_wait_s, float)
        assert 0.0 < bind_wait_s <= 75.0 - 0.25
        assert bind_wait_s <= 75.0 - 0.25 - 2.0 + 5.0  # elapsed allowance

    def test_bound_state_renders_in_handshake_row_and_json(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        report = {"bind_state": "bound", "scan_mode": "quick_check"}
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            report,
            hello_report={"bind_state": "bound"},
            json_output=True,
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        # additive fields ride the JSON payload when present
        assert payload["bind_state"] == "bound"
        assert payload["scan_mode"] == "quick_check"
        # the handshake PASS row names the bind state
        handshake = next(
            c for c in payload["checks"] if "handshake ok" in c["message"]
        )
        assert handshake["status"] == "PASS"
        assert "bind=bound" in handshake["message"]

    def test_pending_handshake_without_bind_state_keeps_row_shape(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # an old sidecar reports no bind_state: the handshake row keeps
        # today's wording byte-for-byte (additive-absence)
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, {})
        out = capsys.readouterr().out
        assert rc == 0
        handshake = next(
            line for line in out.splitlines() if "handshake ok" in line
        )
        assert "bind=" not in handshake

    def test_json_carries_bind_waited_ms_additively(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        report = {
            "database_health": (
                "skipped:bind-contention bind pending after 73000ms "
                "(budget 73000ms)"
            ),
            "scan_mode": "bind-pending",
            "bind_state": "pending",
            "bind_waited_ms": 73000,
        }
        rc, _ = _run_doctor(
            tmp_path, monkeypatch, isolated_home, report, json_output=True
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["bind_state"] == "pending"
        assert payload["bind_waited_ms"] == 73000
        assert payload["scan_mode"] == "bind-pending"
        assert payload["summary"]["fail"] == 0

    def test_fence_after_hello_renders_typed_lane_skew_once(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # the deferred-bind fence channel: hello answers (healthy — no
        # more waiting on the bind), then the rethrown module-load-path
        # refusal arrives as the doctor RPC's error. The fence sentence
        # must type it exactly like the hello-channel fence (R3).
        fence = RuntimeError(
            "doctor: Magic Context refused to open its shared context "
            "database. Reason (from /tmp/magic-context.log): "
            "[magic-context] storage fatal: refusing to open context.db; "
            "upstream migration lane v95 is newer than this binary "
            "supports (max v94). A pinned or stale plugin is likely "
            "sharing this database with a newer instance"
        )
        rc, _ = _run_doctor(
            tmp_path, monkeypatch, isolated_home, {}, doctor_error=fence
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert out.count("shared-store lane skew") == 1
        assert "lane v95" in out
        assert "max v94" in out
        summary_line = next(line for line in out.splitlines() if "Summary:" in line)
        assert "FAIL 1" in summary_line

    def test_non_fence_bind_error_renders_single_generic_fail(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        # a bind failure that is neither a fence sentence nor transient
        # (structural) rethrows through the same RPC error channel and
        # renders today's generic sidecar-failure family — exactly one
        # FAIL row, rc 1, wording unchanged.
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            doctor_error=RuntimeError(
                "doctor: Magic Context refused to open its shared context "
                "database. Reason (from /tmp/magic-context.log): "
                "storage fatal: disk I/O error reading the store header"
            ),
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert out.count("Magic Context sidecar failed") >= 1
        fail_lines = [line for line in out.splitlines() if "FAIL" in line]
        assert all("lane skew" not in line for line in fail_lines)
        summary_line = next(line for line in out.splitlines() if "Summary:" in line)
        assert "FAIL 1" in summary_line
        assert "FAIL 0" not in out


DRIFTED_CONFIG = (
    "# hermes config (host-managed)\n"
    "language: en\n"
    "context:\n"
    "  engine: compressor\n"
    "memory:\n"
    "  provider: null\n"
    "plugins:\n"
    "  enabled:\n"
    "    - magic-hermes\n"
)


def _write_drifted_config():
    config = Path(cli.hermes_config_path())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(DRIFTED_CONFIG, encoding="utf-8")
    return config


def _write_foreign_engine_config():
    config = Path(cli.hermes_config_path())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "context:\n"
        "  engine: sqlite-vec\n"
        "memory:\n"
        "  provider: null\n"
        "plugins:\n"
        "  enabled:\n"
        "    - magic-hermes\n",
        encoding="utf-8",
    )
    return config


def _write_pluginless_config():
    config = Path(cli.hermes_config_path())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "context:\n"
        "  engine: magic-context\n"
        "memory:\n"
        "  provider: magic_context\n",
        encoding="utf-8",
    )
    return config


def _seed_lease(isolated_home):
    from magic_hermes import wiring

    wiring.write_lease(now=100.0)


class TestLeaseAwareWiringRows:
    """U6 (plan 2026-10-31, Mode 2) — the unwired-config FAIL rows name
    lease/convergence state truthfully. Every variant keeps status FAIL
    and rc 1 (W4/R3): the doctor reports wiring, never claims activation."""

    def _wiring_rows(self, out):
        return [
            line for line in out.splitlines() if "Hermes config missing wiring" in line
        ]

    def test_lease_plus_repairable_names_convergence_pending(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _seed_lease(isolated_home)
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            config_writer=_write_drifted_config,
        )
        out = capsys.readouterr().out
        assert rc == 1
        rows = self._wiring_rows(out)
        assert len(rows) == 1
        row = rows[0]
        assert "FAIL" in row
        assert "context.engine=magic-context" in row  # the missing labels stay
        assert "wiring lease active at" in row
        lease_path = str(
            isolated_home / ".local" / "share" / "magic-hermes" / "wiring-lease.json"
        )
        assert lease_path in row
        assert "convergence pending" in row
        assert "magic-hermes install" in row

    def test_lease_plus_deliberate_names_the_override(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _seed_lease(isolated_home)
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            config_writer=_write_foreign_engine_config,
        )
        out = capsys.readouterr().out
        assert rc == 1
        rows = self._wiring_rows(out)
        assert len(rows) == 1
        row = rows[0]
        assert "sqlite-vec" in row
        assert "deliberate" in row
        assert "wiring lease active at" in row
        # the opt-out is named
        assert "delete the lease" in row

    def test_lease_present_but_plugin_lost_names_enablement_gap(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _seed_lease(isolated_home)
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            config_writer=_write_pluginless_config,
        )
        out = capsys.readouterr().out
        assert rc == 1
        rows = self._wiring_rows(out)
        assert len(rows) == 1
        row = rows[0]
        assert "plugins.enabled" in row  # missing-label for the plugin itself
        assert "lost magic-hermes" in row
        assert "cannot restore its own enablement" in row
        # no self-repair hint: convergence cannot fix this shape
        assert "convergence pending" not in row

    def test_no_lease_keeps_todays_message(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        rc, _ = _run_doctor(
            tmp_path,
            monkeypatch,
            isolated_home,
            {},
            config_writer=_write_drifted_config,
        )
        out = capsys.readouterr().out
        assert rc == 1
        rows = self._wiring_rows(out)
        assert len(rows) == 1
        row = rows[0]
        assert row.endswith("— run `magic-hermes install`")
        assert "lease" not in row
        assert "convergence" not in row

    def test_wired_config_stays_pass_regardless_of_lease(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _seed_lease(isolated_home)
        rc, _ = _run_doctor(tmp_path, monkeypatch, isolated_home, {})
        out = capsys.readouterr().out
        assert rc == 0
        assert "Hermes config wired at" in out
        assert "Hermes config missing wiring" not in out

    def test_text_scan_fallback_renders_lease_aware_variant(
        self, tmp_path, monkeypatch, isolated_home, capsys
    ):
        _seed_lease(isolated_home)
        with mock.patch.dict(
            "sys.modules", {"ruamel.yaml": None, "yaml": None}
        ):
            rc, _ = _run_doctor(
                tmp_path,
                monkeypatch,
                isolated_home,
                {},
                config_writer=_write_drifted_config,
            )
        out = capsys.readouterr().out
        assert rc == 1
        rows = self._wiring_rows(out)
        assert len(rows) == 1
        row = rows[0]
        # lease-aware but honest: without a YAML parser the doctor cannot
        # classify the drift, so it must not claim convergence is pending
        assert "wiring lease active at" in row
        assert "magic-hermes install" in row
        assert "convergence pending" not in row


_OBSERVER_DRIVER = r'''
import json
import os
import sys
from pathlib import Path
from unittest import mock

from magic_hermes import cli

TESTED = os.environ["DOCTOR_TESTED_VERSION"]
RESPONSE = json.loads(os.environ["DOCTOR_SIDECAR_RESPONSE"])


class _FakeSidecar:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def call(self, method, params=None, timeout=None):
        if method == "hello":
            error = os.environ.get("DOCTOR_SIDECAR_ERROR")
            if error:
                raise RuntimeError(error)
            return {"harness": "hermes", "package_version": TESTED}
        return RESPONSE


with mock.patch.object(
    cli,
    "discover_installations",
    return_value=[(Path(os.environ["DOCTOR_PACKAGE"]), TESTED)],
), mock.patch.object(cli, "RuntimeClient", _FakeSidecar):
    sys.exit(cli.run_doctor())
'''


class TestObserverBudgetMatrix:
    """U17 (plan 0d118f58, finding c7d63424) — the forced-regime matrix
    under a 90 s observer.

    maestro's phase-2 verdict runs the real ``magic-hermes doctor``
    process single-shot with a 90 s kill (phase2.py:296-316: rc124 ->
    ERROR inconclusive-timeout). Each row drives the real CLI entry in a
    subprocess under that same 90 s enforcement and asserts the invariant
    the finding broke: a verdict always renders — rc 0 with "FAIL 0" for
    the healthy regime and for every degraded one, never a hang.

    The Node sidecar is faked in-driver (these suites never spawn Node);
    each regime's response mirrors the bridge contract shapes pinned by
    TestDegradedScanContract. The ``skipped:deadline`` *emission* is the
    even lane's unit (U14); this row pins the CLI-side verdict invariant
    for that shape regardless of which side mints it."""

    OBSERVER_BUDGET_S = 90.0

    def _run_row(self, tmp_path, isolated_home, env_forces, response=None):
        """Run one matrix row as a real subprocess observer.

        Returns ``(proc, elapsed)``; the subprocess timeout is the 90 s
        observer budget itself — a hang fails the row exactly the way
        maestro's phase-2 verdict would ERROR on rc124.
        """
        from magic_hermes import historian_guard as hg

        db = tmp_path / "context.db"
        hg.make_fixture_db(db)
        hg.apply_guard(db, verify=False)
        _wire_config()
        tested = cli.tested_magic_context_version()
        package = make_package(tmp_path / "pkg", tested)
        series = ".".join(map(str, cli.supported_magic_context_series()))

        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("MAGIC_CONTEXT_", "MAGIC_HERMES_"))
        }
        payload = {"database_health": "ok"}
        if response:
            payload.update(response)
        env.update(
            {
                "HOME": str(Path.home()),
                "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]),
                "MAGIC_CONTEXT_DB_PATH": str(db),
                "DOCTOR_TESTED_VERSION": tested,
                "DOCTOR_PACKAGE": str(package),
                "DOCTOR_SIDECAR_RESPONSE": json.dumps(
                    {
                        "core_symbols_ready": True,
                        "supported_series": series,
                        **payload,
                    }
                ),
            }
        )
        env.update(env_forces)
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-c", _OBSERVER_DRIVER],
            capture_output=True,
            text=True,
            timeout=self.OBSERVER_BUDGET_S,
            env=env,
            cwd=str(tmp_path),
        )
        elapsed = time.monotonic() - started
        assert elapsed < self.OBSERVER_BUDGET_S
        return proc

    def test_healthy_regime_renders_pass_verdict(self, tmp_path, isolated_home):
        proc = self._run_row(tmp_path, isolated_home, {})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL 0" in proc.stdout
        assert "quick_check ok" in proc.stdout

    def test_store_size_gate_degrades_to_warn_not_hang(
        self, tmp_path, isolated_home
    ):
        proc = self._run_row(
            tmp_path,
            isolated_home,
            {"MAGIC_CONTEXT_DOCTOR_SCAN_MAX_BYTES": "4096"},
            {"database_health": "skipped:store-size 3690000000 > 4096"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL 0" in proc.stdout
        assert "store-size" in proc.stdout
        assert "WARN 1 / FAIL 0" in proc.stdout

    def test_probe_ms_gate_degrades_to_warn_not_hang(
        self, tmp_path, isolated_home
    ):
        proc = self._run_row(
            tmp_path,
            isolated_home,
            {"MAGIC_CONTEXT_DOCTOR_PROBE_MAX_MS": "0.001"},
            {"database_health": "skipped:probe-ms 4300.5 > 0.001"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL 0" in proc.stdout
        assert "probe-ms" in proc.stdout
        assert "WARN 1 / FAIL 0" in proc.stdout

    def test_scan_deadline_gate_degrades_to_warn_not_hang(
        self, tmp_path, isolated_home
    ):
        proc = self._run_row(
            tmp_path,
            isolated_home,
            {"MAGIC_CONTEXT_DOCTOR_SCAN_DEADLINE_S": "0.001"},
            {"database_health": "skipped:deadline 12.5 > 0.001"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL 0" in proc.stdout
        assert "deadline" in proc.stdout
        assert "WARN 1 / FAIL 0" in proc.stdout

    def test_exhausted_wall_budget_renders_verdict_without_sidecar(
        self, tmp_path, isolated_home
    ):
        proc = self._run_row(
            tmp_path,
            isolated_home,
            {"MAGIC_CONTEXT_DOCTOR_WALL_BUDGET_S": "0"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL 0" in proc.stdout
        assert "wall-budget" in proc.stdout
        assert "PRAGMA quick_check" in proc.stdout
        assert "quick_check ok" not in proc.stdout

    def test_lane_skew_fence_renders_typed_fail_within_observer_shot(
        self, tmp_path, isolated_home
    ):
        # U3 (finding c7d63424): a fence-refusing store renders the typed
        # lane-skew FAIL row inside the 90 s single-shot budget — rc 1
        # and FAIL 1, the escalation lane — with no extra spawns: the
        # typed row is minted inside the existing failure branch.
        fence = (
            "Runtime exited during hello (status 1); "
            "stderr: [magic-context] storage fatal: refusing to open "
            "context.db; upstream migration lane v95 is newer than this "
            "binary supports (max v94). A pinned or stale plugin is "
            "likely sharing this database with a newer instance"
        )
        proc = self._run_row(
            tmp_path,
            isolated_home,
            {"DOCTOR_SIDECAR_ERROR": fence},
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "shared-store lane skew" in proc.stdout
        assert "lane v95" in proc.stdout
        assert "scripts/sync_magic_context_release.py" in proc.stdout
        assert "/ FAIL 1" in proc.stdout


class TestDeploySmoke:
    """U17 deploy smoke — the interpreter maestro probes must carry the
    newest tagged release of magic-hermes. Gated behind
    MAGIC_HERMES_DEPLOY_SMOKE=1 (the ``release.py --deploy`` lane sets it);
    the ordinary test lane skips, so the rider adds no skips to it.

    With MAGIC_HERMES_DEPLOY_VENV set, the installed version is resolved by
    the TARGET venv's own interpreter — the one maestro actually probes —
    instead of this (development) interpreter, whose metadata can be stale."""

    def test_installed_version_matches_newest_tag(self):
        if os.environ.get("MAGIC_HERMES_DEPLOY_SMOKE") != "1":
            pytest.skip("deploy smoke runs only with MAGIC_HERMES_DEPLOY_SMOKE=1")
        from importlib import metadata

        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tags = subprocess.run(
            ["git", "-C", root, "tag", "-l", "v*"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert tags, "no v* tags found in the release repository"
        newest = max(tags, key=lambda t: tuple(map(int, t[1:].split("."))))
        target = os.environ.get("MAGIC_HERMES_DEPLOY_VENV")
        if target:
            probe = subprocess.run(
                [
                    str(Path(target) / "bin" / "python"),
                    "-c",
                    "import importlib.metadata as m; "
                    "print(m.version('magic-hermes'))",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert probe.returncode == 0, (
                f"deploy target venv {target} could not resolve magic-hermes "
                f"metadata: {(probe.stderr or probe.stdout).strip()}"
            )
            installed = probe.stdout.strip()
        else:
            installed = metadata.version("magic-hermes")
        assert installed == newest[1:], (
            f"deployed interpreter has magic-hermes {installed} but the "
            f"newest tag is {newest}"
        )
