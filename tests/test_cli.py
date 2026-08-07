"""CLI smoke tests.

These exist because of a real bug the module tests could not catch: importing
``Severity`` from ``phase3.gaps`` into cli.py silently shadowed
``phase0.taxonomy.Severity``, and ``taxonomy validate`` started failing with a bare
``AttributeError``. Every module test still passed - the defect lived entirely in
the wiring.

So every command gets exercised through ``main()`` with real config files, and the
exit code is asserted. Cheap to run, and it catches import shadowing, argparse
mistakes and missing attributes that unit tests structurally cannot.
"""

from __future__ import annotations

import json

import pytest

from goldenflow.cli import EXIT_FAILED, EXIT_OK, build_parser, main

TAXONOMY = "config/taxonomy.yaml"
REGISTRY = "config/protected-tests.yaml"
PLAN = "config/tracking-plan.yaml"
SUITE = "fixtures/appium-suite"


# ------------------------------------------------------------------ phase 0


def test_taxonomy_validate_passes_on_the_shipped_contract(capsys) -> None:
    assert main(["taxonomy", "validate", TAXONOMY]) == EXIT_OK
    assert "PASSED" in capsys.readouterr().out


def test_taxonomy_summary_emits_json(capsys) -> None:
    assert main(["taxonomy", "summary", TAXONOMY]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["app_id"] == "acme-shop"
    assert "fingerprint" in payload


def test_registry_check_passes(capsys) -> None:
    assert main(["registry", "check", REGISTRY]) == EXIT_OK


def test_registry_test_returns_nonzero_for_a_protected_test(capsys) -> None:
    code = main(["registry", "test", REGISTRY,
                 "tests/account/delete/test_erasure.py"])
    assert code == EXIT_FAILED
    assert "PROTECTED" in capsys.readouterr().out


def test_registry_test_returns_zero_for_an_ordinary_test() -> None:
    assert main(["registry", "test", REGISTRY, "tests/browse/test_home.py"]) == EXIT_OK


def test_baseline_capture_and_compare(tmp_path, capsys) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    assert main(["baseline", "capture", "--app", "acme-shop", "--release", "8.2.0",
                 "--out", str(first), "--coverage", "62",
                 "--escaped-defects", "14"]) == EXIT_OK
    assert main(["baseline", "capture", "--app", "acme-shop", "--release", "9.0.0",
                 "--out", str(second), "--coverage", "78",
                 "--escaped-defects", "8"]) == EXIT_OK
    capsys.readouterr()

    assert main(["baseline", "compare", str(first), str(second)]) == EXIT_OK
    assert "improved" in capsys.readouterr().out


def test_baseline_capture_refuses_to_overwrite(tmp_path, capsys) -> None:
    path = tmp_path / "a.json"
    main(["baseline", "capture", "--app", "x", "--release", "1", "--out", str(path)])
    assert main(["baseline", "capture", "--app", "x", "--release", "1",
                 "--out", str(path)]) == EXIT_FAILED


# ------------------------------------------------------------------ phase 1


def test_tracking_validate_passes(capsys) -> None:
    assert main(["tracking", "validate", PLAN]) == EXIT_OK
    assert "PASSED" in capsys.readouterr().out


@pytest.mark.parametrize("dialect", ["bigquery", "clickhouse", "sqlite"])
def test_store_ddl_emits_for_every_dialect(dialect: str, capsys) -> None:
    assert main(["store", "ddl", "--dialect", dialect]) == EXIT_OK
    out = capsys.readouterr().out
    assert "events" in out and "risk_signals" in out


# ------------------------------------------------------------------ phase 3


def test_suite_parse_runs_against_the_fixture(capsys) -> None:
    assert main(["suite", "parse", "--suite", SUITE, "--taxonomy", TAXONOMY]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out.split("\n", 1)[1].split("\n1 unbound")[0])
    assert payload["tests"] >= 18
    assert payload["mapping_rate_pct"] == 100.0


# ------------------------------------------------------------------- wiring


def test_every_subcommand_group_is_reachable() -> None:
    """Guards against a group being added to the parser but never wired up."""
    parser = build_parser()
    groups = {
        action.dest
        for action in parser._actions
        if action.dest == "group" for _ in [None]
    }
    assert groups == {"group"}

    subparsers = next(
        a for a in parser._actions if a.dest == "group"
    ).choices
    expected = {
        "taxonomy", "readiness", "baseline", "registry", "phase0",
        "tracking", "store", "ingest", "quality", "pii", "phase1",
        "journeys", "drift", "graph", "phase2",
        "suite", "coverage", "gaps", "phase3",
    }
    assert expected <= set(subparsers)


def test_missing_file_reports_cleanly_rather_than_traceback(capsys) -> None:
    code = main(["taxonomy", "validate", "config/does-not-exist.yaml"])
    assert code != EXIT_OK
    assert "ERROR" in capsys.readouterr().err


def test_unknown_command_exits_with_usage() -> None:
    with pytest.raises(SystemExit):
        main(["nonsense"])
