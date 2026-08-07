"""Baseline snapshot tests.

The Phase 6 improvement claim is only citable if the baseline it is measured
against could not have been quietly rewritten afterwards.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from goldenflow.phase0.baseline import BaselineSnapshot


def snapshot(**overrides) -> BaselineSnapshot:
    defaults = dict(
        app_id="acme-shop",
        release_version="8.2.0",
        captured_by="qe-lead",
        automated_coverage_pct=62.0,
        escaped_defects_per_release=14.0,
        mean_time_to_detect_hours=36.0,
        regression_suite_runtime_minutes=180.0,
        flaky_test_pct=9.5,
        manual_authoring_hours_per_sprint=40.0,
        total_test_count=820,
    )
    defaults.update(overrides)
    return BaselineSnapshot(**defaults)


# ------------------------------------------------------------- write-once rule


def test_baseline_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    snapshot().save(path)
    with pytest.raises(FileExistsError) as exc:
        snapshot(automated_coverage_pct=99.0).save(path)
    assert "write-once" in str(exc.value)


def test_force_allows_correcting_a_capture_error(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    snapshot().save(path)
    snapshot(automated_coverage_pct=71.0).save(path, force=True)
    assert BaselineSnapshot.load(path).automated_coverage_pct == 71.0


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "baseline.json"
    assert snapshot().save(path).exists()


# ---------------------------------------------------------------- tamper check


def test_round_trip_preserves_values(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    original = snapshot()
    original.save(path)
    loaded = BaselineSnapshot.load(path)
    assert loaded.checksum() == original.checksum()
    assert loaded.escaped_defects_per_release == 14.0


def test_edited_baseline_is_rejected(tmp_path: Path) -> None:
    """Tampering after capture must be detectable by someone who was not present."""
    path = tmp_path / "baseline.json"
    snapshot().save(path)

    payload = json.loads(path.read_text())
    payload["escaped_defects_per_release"] = 40.0  # inflate the "before"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError) as exc:
        BaselineSnapshot.load(path)
    assert "checksum mismatch" in str(exc.value)


def test_notes_are_excluded_from_the_checksum() -> None:
    """Annotating a baseline after the fact is legitimate; changing a number is not.

    captured_at is pinned because it defaults to now() and would otherwise vary
    between the two snapshots, making the test pass or fail on clock granularity
    rather than on the behaviour it claims to check.
    """
    when = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    plain = snapshot(notes="", captured_at=when)
    annotated = snapshot(notes="added context", captured_at=when)
    assert plain.checksum() == annotated.checksum()

    # ...and a number still changes it.
    assert snapshot(notes="", captured_at=when,
                    escaped_defects_per_release=99.0).checksum() != plain.checksum()


# ------------------------------------------------------------------ comparison


def test_comparison_understands_metric_direction() -> None:
    before = snapshot()
    after = snapshot(
        release_version="9.4.0",
        escaped_defects_per_release=8.0,     # down - good
        automated_coverage_pct=78.0,         # up   - good
        regression_suite_runtime_minutes=200.0,  # up - bad
    )
    by_metric = {d.metric: d for d in before.compare(after).deltas}

    assert by_metric["escaped_defects_per_release"].improved
    assert by_metric["automated_coverage_pct"].improved
    assert not by_metric["regression_suite_runtime_minutes"].improved


def test_relative_change_is_computed_and_guards_divide_by_zero() -> None:
    before = snapshot(escaped_defects_per_release=20.0,
                      golden_journey_coverage_pct=0.0)
    after = snapshot(escaped_defects_per_release=12.0,
                     golden_journey_coverage_pct=95.0)
    by_metric = {d.metric: d for d in before.compare(after).deltas}

    assert by_metric["escaped_defects_per_release"].relative_pct == -40.0
    # Golden journey coverage starts at zero by definition - the journeys are not
    # yet known in Phase 0 - so a relative change is undefined rather than infinite.
    assert by_metric["golden_journey_coverage_pct"].relative_pct is None
    assert by_metric["golden_journey_coverage_pct"].improved


def test_unchanged_metric_is_neither_improved_nor_regressed() -> None:
    comparison = snapshot().compare(snapshot(release_version="9.0.0"))
    assert comparison.improved_count == 0
    assert comparison.regressed_count == 0


def test_cross_app_comparison_is_refused() -> None:
    with pytest.raises(ValueError) as exc:
        snapshot().compare(snapshot(app_id="other-app"))
    assert "different apps" in str(exc.value)


def test_comparison_serialises_for_reporting() -> None:
    payload = snapshot().compare(snapshot(escaped_defects_per_release=7.0)).to_dict()
    assert payload["app_id"] == "acme-shop"
    assert payload["improved"] == 1
    assert any(d["metric"] == "escaped_defects_per_release" for d in payload["deltas"])
