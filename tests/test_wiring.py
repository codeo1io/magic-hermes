"""Wiring convergence contracts (plan unit U4, finding c7d63424 Mode 2).

The lease is the write authorization: ``converge()`` writes only when the
operator-intent lease exists, only when BOTH wiring keys sit in their
factory-default domains (the exact host-clobber signature), and only
through the comment-preserving atomic ``configure_hermes`` writer. Every
miss — no lease, no config file, no YAML library, a torn read, a
deliberate override — is a logged no-op that never raises, because
convergence runs inside the gateway process that loads the plugin
(W2/W3/W6; the ``historian_guard`` stdlib-only convention).

These tests never touch the real ``~/.hermes``: every filesystem fixture
runs under the ``isolated_home`` env pair.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from magic_hermes import wiring

RECHECK_ENV = "MAGIC_HERMES_WIRING_RECHECK_S"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


# The live 2026-10-30 host-clobber shape: factory wiring on both keys while
# plugins.enabled keeps magic-hermes, plus a host comment and an unrelated
# key that must survive the repair byte-for-byte in spirit (W3).
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

WIRED_CONFIG = (
    "context:\n"
    "  engine: magic-context\n"
    "memory:\n"
    "  provider: magic_context\n"
    "plugins:\n"
    "  enabled:\n"
    "    - magic-hermes\n"
)


def _drifted_config(home: Path) -> Path:
    config = home / ".hermes" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(DRIFTED_CONFIG, encoding="utf-8")
    return config


def _seed_lease(home: Path, *, now: float = 100.0) -> Path:
    """Write the lease directly, mirroring the odd lane's fixture."""
    lease = home / ".local" / "share" / "magic-hermes" / "wiring-lease.json"
    lease.parent.mkdir(parents=True, exist_ok=True)
    lease.write_text(
        json.dumps(
            {
                "schema": 1,
                "plugin": "magic-hermes",
                "wires": ["context.engine", "memory.provider"],
                "created_at": now,
                "updated_at": now,
            }
        ),
        encoding="utf-8",
    )
    return lease


@pytest.fixture(autouse=True)
def _clean_recheck_state():
    wiring._reset_recheck_state()
    yield
    wiring._reset_recheck_state()


class TestClassification:
    """The pure classifier over a parsed config document (W2).

    ``repairable`` ⇔ BOTH keys in their factory domains — engine ∈
    {absent, compressor} AND provider ∈ {absent, null} — which is exactly
    the observed host-clobber signature. Anything else, including partial
    states, is a deliberate operator choice.
    """

    def test_converged_when_both_keys_wired(self):
        doc = {
            "context": {"engine": "magic-context"},
            "memory": {"provider": "magic_context"},
        }
        assert wiring.classify_document(doc) == "converged"

    @pytest.mark.parametrize(
        "document",
        [
            # absent / absent (empty document, or empty sections)
            {},
            {"context": {}, "memory": {}},
            # compressor / null — the live clobber shape
            {"context": {"engine": "compressor"}, "memory": {"provider": None}},
            # absent / null
            {"memory": {"provider": None}},
            # compressor / absent
            {"context": {"engine": "compressor"}},
        ],
        ids=["absent-absent", "empty-sections", "compressor-null", "absent-null",
             "compressor-absent"],
    )
    def test_factory_domain_combinations_are_repairable(self, document):
        assert wiring.classify_document(document) == "repairable"

    @pytest.mark.parametrize(
        "document",
        [
            # foreign engine
            {"context": {"engine": "sqlite-vec"}, "memory": {"provider": None}},
            # foreign provider
            {"context": {"engine": "compressor"}, "memory": {"provider": "sqlite"}},
            # the partial clobber: compressor + magic_context
            {
                "context": {"engine": "compressor"},
                "memory": {"provider": "magic_context"},
            },
            # explicit engine null is NOT in the factory domain
            {"context": {"engine": None}, "memory": {"provider": None}},
            # wired engine, foreign provider
            {"context": {"engine": "magic-context"}, "memory": {"provider": "sqlite"}},
        ],
        ids=["foreign-engine", "foreign-provider", "partial-clobber",
             "explicit-engine-null", "wired-engine-foreign-provider"],
    )
    def test_any_foreign_value_is_deliberate(self, document):
        assert wiring.classify_document(document) == "deliberate"

    def test_non_dict_document_is_repairable(self):
        # an empty or comment-only config.yaml parses to None: every key
        # is absent, so the factory domain holds (configure_hermes
        # semantics: non-dict documents are treated as {})
        assert wiring.classify_document(None) == "repairable"


class TestLease:
    def test_write_lease_uses_the_pinned_schema_and_location(
        self, isolated_home
    ):
        lease = wiring.write_lease(now=100.0)
        expected = (
            isolated_home / ".local" / "share" / "magic-hermes" / "wiring-lease.json"
        )
        assert lease == expected
        payload = json.loads(lease.read_text(encoding="utf-8"))
        assert payload["schema"] == 1
        assert payload["plugin"] == "magic-hermes"
        assert payload["wires"] == ["context.engine", "memory.provider"]
        assert payload["created_at"] == 100.0
        assert payload["updated_at"] == 100.0

    def test_read_lease_round_trips(self, isolated_home):
        _seed_lease(isolated_home, now=42.5)
        payload = wiring.read_lease()
        assert payload is not None
        assert payload["schema"] == 1
        assert payload["created_at"] == 42.5

    def test_refresh_preserves_created_at_and_bumps_updated_at(
        self, isolated_home
    ):
        wiring.write_lease(now=100.0)
        refreshed = wiring.write_lease(now=200.0)
        payload = json.loads(refreshed.read_text(encoding="utf-8"))
        assert payload["created_at"] == 100.0
        assert payload["updated_at"] == 200.0

    def test_absent_lease_reads_as_none(self, isolated_home):
        assert wiring.read_lease() is None

    def test_corrupt_lease_reads_as_none_fail_open(self, isolated_home):
        lease = (
            isolated_home / ".local" / "share" / "magic-hermes" / "wiring-lease.json"
        )
        lease.parent.mkdir(parents=True, exist_ok=True)
        lease.write_text("{not json", encoding="utf-8")
        assert wiring.read_lease() is None

    def test_foreign_schema_lease_reads_as_none(self, isolated_home):
        lease = (
            isolated_home / ".local" / "share" / "magic-hermes" / "wiring-lease.json"
        )
        lease.parent.mkdir(parents=True, exist_ok=True)
        lease.write_text(
            json.dumps({"schema": 99, "plugin": "other"}), encoding="utf-8"
        )
        assert wiring.read_lease() is None


class TestConverge:
    def test_drifted_config_plus_lease_repairs_exactly_the_two_keys(
        self, isolated_home
    ):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)

        result = wiring.converge()

        assert result.status == "repaired"
        assert result.changed == ["context.engine", "memory.provider"]
        text = config.read_text(encoding="utf-8")
        assert "engine: magic-context" in text
        assert "provider: magic_context" in text
        # comments, unrelated keys, and the plugins list survive (W3)
        assert "# hermes config (host-managed)" in text
        assert "language: en" in text
        assert "- magic-hermes" in text
        # one timestamped backup of the drifted state was kept
        backups = list(config.parent.glob("config.yaml.bak.*"))
        assert len(backups) == 1
        assert "engine: compressor" in backups[0].read_text(encoding="utf-8")

    def test_second_call_is_idempotent_and_writes_nothing(self, isolated_home):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        wiring.converge()
        backups_after_repair = list(config.parent.glob("config.yaml.bak.*"))
        before = config.read_bytes()

        result = wiring.converge()

        assert result.status == "converged"
        assert result.changed == []
        assert config.read_bytes() == before
        assert list(config.parent.glob("config.yaml.bak.*")) == backups_after_repair

    def test_no_lease_means_zero_writes(self, isolated_home):
        config = _drifted_config(isolated_home)
        before = config.read_bytes()

        result = wiring.converge()

        assert result.status == "no-lease"
        assert config.read_bytes() == before
        assert list(config.parent.glob("config.yaml.bak.*")) == []

    def test_absent_config_file_is_a_no_op(self, isolated_home):
        _seed_lease(isolated_home)
        config = isolated_home / ".hermes" / "config.yaml"

        result = wiring.converge()

        assert result.status == "no-config"
        assert not config.exists()

    def test_deliberate_state_is_never_written(self, isolated_home):
        config = isolated_home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "context:\n  engine: sqlite-vec\nmemory:\n  provider: null\n",
            encoding="utf-8",
        )
        _seed_lease(isolated_home)
        before = config.read_bytes()

        result = wiring.converge()

        assert result.status == "deliberate"
        assert config.read_bytes() == before

    def test_missing_yaml_library_is_a_logged_noop(self, isolated_home):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)

        with mock.patch.dict(sys.modules, {"ruamel.yaml": None, "yaml": None}):
            result = wiring.converge()

        assert result.status == "no-yaml-lib"
        assert "engine: compressor" in config.read_text(encoding="utf-8")

    def test_unparseable_config_is_a_noop_that_never_raises(self, isolated_home):
        config = isolated_home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("{ unclosed mapping\n", encoding="utf-8")
        _seed_lease(isolated_home)

        result = wiring.converge()

        assert result.status == "unparseable"
        assert "{ unclosed mapping" in config.read_text(encoding="utf-8")

    def test_writer_failure_is_contained_not_raised(self, isolated_home, monkeypatch):
        _drifted_config(isolated_home)
        _seed_lease(isolated_home)

        def exploding_configure(config_path=None, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(wiring, "configure_hermes", exploding_configure)
        result = wiring.converge()

        assert result.status == "error"
        assert "disk full" in result.detail

    def test_unplugged_plugin_is_never_reenabled(self, isolated_home):
        # d-sec0-cor1 (W2): the lease authorizes exactly WIRED_KEYS. A
        # deliberate full unwire — factory engine/provider AND the plugin
        # removed from plugins.enabled, lease retained — is repaired on
        # the two leased keys only; convergence must never re-append the
        # plugin: enablement is install's explicit operator act (W4), and
        # a still-loaded gateway's pre_tool_call recheck must not revert
        # a deliberate unplug.
        config = _drifted_config(isolated_home)
        config.write_text(
            DRIFTED_CONFIG.replace("    - magic-hermes\n", ""),
            encoding="utf-8",
        )
        _seed_lease(isolated_home)

        result = wiring.converge()

        assert result.status == "repaired"
        assert result.changed == ["context.engine", "memory.provider"]
        assert "magic-hermes" not in config.read_text(encoding="utf-8")

    def test_operator_value_in_classify_to_write_window_wins(
        self, isolated_home, monkeypatch
    ):
        # U4 key decision (d-cor2-adv1): the write step re-reads and
        # reclassifies immediately before configuring. A deliberate
        # value landing between the first classification and the write
        # must WIN — converge returns the fresh classification as a
        # no-op and never overwrites it.
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        verdicts = iter(["repairable", "deliberate"])
        monkeypatch.setattr(
            wiring, "classify_document", lambda doc: next(verdicts)
        )
        before = config.read_bytes()

        result = wiring.converge()

        assert result.status == "deliberate"
        assert config.read_bytes() == before
        assert list(config.parent.glob("config.yaml.bak.*")) == []

    def test_repair_preserves_the_config_file_mode(self, isolated_home):
        # d-cor2: the tmp+replace rewrite must carry the target's mode
        # across (0600 stays 0600) instead of widening to the umask
        # default — the Hermes config can carry provider credentials.
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        os.chmod(config, 0o600)

        result = wiring.converge()

        assert result.status == "repaired"
        assert stat.S_IMODE(os.stat(config).st_mode) == 0o600

    def test_backup_retention_keeps_newest_ten(self, isolated_home):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        parent = config.parent
        seeded = []
        for index in range(12):
            backup = parent / f"config.yaml.bak.{1_700_000_000 + index}"
            backup.write_text("old\n", encoding="utf-8")
            seeded.append(backup)

        wiring.converge()

        remaining = {
            int(p.name.rsplit(".", 1)[1])
            for p in parent.glob("config.yaml.bak.*")
        }
        assert len(remaining) == 10
        # the three oldest seeded backups were dropped; the newest nine
        # seeded backups plus the fresh repair backup survive
        assert 1_700_000_000 not in remaining
        assert 1_700_000_001 not in remaining
        assert 1_700_000_002 not in remaining
        assert 1_700_000_011 in remaining

    def test_prune_never_deletes_foreign_backup_siblings(self, isolated_home):
        # d-adv2: retention owns only ``bak.<epoch>`` siblings. Operator-
        # made files that merely match the glob (``.bak.manual``,
        # ``.bak.pre-experiment``) sort as oldest under a float() key and
        # were deleted FIRST — new destructive behavior on files the
        # repo never owned. They must survive any prune.
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        parent = config.parent
        foreign = [
            parent / "config.yaml.bak.manual",
            parent / "config.yaml.bak.pre-experiment",
        ]
        for path in foreign:
            path.write_text("operator copy\n", encoding="utf-8")
        for index in range(12):
            backup = parent / f"config.yaml.bak.{1_700_000_000 + index}"
            backup.write_text("old\n", encoding="utf-8")

        wiring.converge()

        for path in foreign:
            assert path.is_file(), f"foreign backup deleted: {path.name}"
            assert path.read_text(encoding="utf-8") == "operator copy\n"
        remaining = {
            int(p.name.rsplit(".", 1)[1])
            for p in parent.glob("config.yaml.bak.*")
            if p.name.rsplit(".", 1)[1].isdigit()
        }
        assert len(remaining) == 10
        assert 1_700_000_000 not in remaining
        assert 1_700_000_011 in remaining


class TestRateLimit:
    def test_should_recheck_boundaries(self):
        # never checked → due
        assert wiring.should_recheck(100.0, None, 900.0) is True
        # before the interval → not due
        assert wiring.should_recheck(100.0, 0.0, 900.0) is False
        # exactly at the interval → due (>=)
        assert wiring.should_recheck(900.0, 0.0, 900.0) is True
        assert wiring.should_recheck(899.999, 0.0, 900.0) is False
        # zero interval → always due
        assert wiring.should_recheck(100.0, 99.999, 0.0) is True

    def test_interval_env_parsing(self, monkeypatch):
        monkeypatch.delenv(RECHECK_ENV, raising=False)
        assert wiring.recheck_interval_s() == 900.0
        monkeypatch.setenv(RECHECK_ENV, "")
        assert wiring.recheck_interval_s() == 900.0
        monkeypatch.setenv(RECHECK_ENV, "junk")
        assert wiring.recheck_interval_s() == 900.0
        monkeypatch.setenv(RECHECK_ENV, "nan")
        assert wiring.recheck_interval_s() == 900.0
        monkeypatch.setenv(RECHECK_ENV, "inf")
        assert wiring.recheck_interval_s() == 900.0
        monkeypatch.setenv(RECHECK_ENV, " 60 ")
        assert wiring.recheck_interval_s() == 60.0
        # negative clamps to 0 (always due), oversized clamps to 86400
        monkeypatch.setenv(RECHECK_ENV, "-5")
        assert wiring.recheck_interval_s() == 0.0
        monkeypatch.setenv(RECHECK_ENV, "999999")
        assert wiring.recheck_interval_s() == 86400.0

    def test_recheck_converges_when_due(self, isolated_home, monkeypatch):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        monkeypatch.setenv(RECHECK_ENV, "0")

        result = wiring.recheck()

        assert result.status == "repaired"
        assert "engine: magic-context" in config.read_text(encoding="utf-8")

    def test_recheck_not_due_leaves_config_untouched(self, isolated_home, monkeypatch):
        config = _drifted_config(isolated_home)
        _seed_lease(isolated_home)
        monkeypatch.setenv(RECHECK_ENV, "0")
        wiring.recheck()
        assert "engine: magic-context" in config.read_text(encoding="utf-8")

        # re-drift; with the interval far from due the recheck must not write
        _drifted_config(isolated_home)
        before = config.read_bytes()
        monkeypatch.setenv(RECHECK_ENV, "86400")

        result = wiring.recheck()

        assert result.status == "not-due"
        assert config.read_bytes() == before

    def test_recheck_without_lease_never_writes(self, isolated_home, monkeypatch):
        config = _drifted_config(isolated_home)
        before = config.read_bytes()
        monkeypatch.setenv(RECHECK_ENV, "0")

        result = wiring.recheck()

        assert result.status == "no-lease"
        assert config.read_bytes() == before


class TestDescribe:
    """Read-only state summary for the doctor's lease-aware rows (U6)."""

    def test_describe_drifted_plus_lease(self, isolated_home):
        _drifted_config(isolated_home)
        _seed_lease(isolated_home)

        state = wiring.describe()

        assert state["has_lease"] is True
        assert state["classification"] == "repairable"
        assert state["engine"] == "compressor"
        assert state["provider"] is None
        assert state["plugin_enabled"] is True

    def test_describe_deliberate_names_the_override(self, isolated_home):
        config = isolated_home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "context:\n  engine: sqlite-vec\n", encoding="utf-8"
        )
        _seed_lease(isolated_home)

        state = wiring.describe()

        assert state["classification"] == "deliberate"
        assert state["engine"] == "sqlite-vec"
        assert state["provider"] == "<absent>"
        assert state["plugin_enabled"] is False

    def test_describe_absent_values_render_as_absent(self, isolated_home):
        config = isolated_home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("language: en\n", encoding="utf-8")

        state = wiring.describe()

        assert state["has_lease"] is False
        assert state["classification"] == "repairable"
        assert state["engine"] == "<absent>"
        assert state["provider"] == "<absent>"

    def test_describe_no_config_file(self, isolated_home):
        state = wiring.describe()
        assert state["has_lease"] is False
        assert state["classification"] == "no-config"

    def test_describe_wired_config(self, isolated_home):
        config = isolated_home / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(WIRED_CONFIG, encoding="utf-8")

        state = wiring.describe()

        assert state["classification"] == "converged"
        assert state["plugin_enabled"] is True


class TestStdlibOnlyConvention:
    def test_module_level_imports_are_stdlib_only(self):
        """The plugin's import path gains no third-party dependency (W6).

        Mirrors the ``historian_guard`` convention: ``wiring.py`` is
        imported by ``plugin.py`` inside the host gateway, which must run
        with zero installed dependencies (``pyproject dependencies = []``).
        Only stdlib modules may appear at module level; YAML-library
        imports stay lazy inside functions.
        """
        source = Path(wiring.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (
                    "wiring.py must not import package siblings at module "
                    "level (island convention, historian_guard style)"
                )
                if node.module:
                    imported.add(node.module.split(".")[0])
        third_party = imported - set(sys.stdlib_module_names)
        assert not third_party, (
            f"wiring.py must stay stdlib-only at module level, found "
            f"{sorted(third_party)}"
        )
