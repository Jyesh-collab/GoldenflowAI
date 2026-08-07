"""Readiness audit tests.

The behaviour worth pinning hardest is that a blocking defect fails the gate even
when the weighted average looks acceptable. An app can score in the high seventies
while having session IDs so broken that no journey can ever be reconstructed;
averaging hides that, and the audit exists precisely to not hide it.
"""

from __future__ import annotations

from goldenflow.phase0.readiness import audit_readiness
from goldenflow.phase0.taxonomy import ScreenDefinition, Taxonomy


def make_taxonomy(n_screens: int = 3) -> Taxonomy:
    return Taxonomy(
        version="test",
        app_id="test-app",
        domains=["browse"],
        screens=[
            ScreenDefinition(
                screen_id=f"screen_{i}",
                display_name=f"Screen {i}",
                domain="browse",
                analytics_tags=[f"Screen{i}"],
                page_objects=[f"Screen{i}Page"],
            )
            for i in range(n_screens)
        ],
    )


def event(tag: str, *, name="screen_view", session="s1", user="u1", ts=1_700_000_000):
    return {
        "event_name": name,
        "screen_tag": tag,
        "session_id": session,
        "user_id": user,
        "timestamp": ts,
    }


def dimension(report, name):
    return next(d for d in report.dimensions if d.name == name)


# ------------------------------------------------------------------ empty input


def test_empty_sample_is_a_blocking_failure() -> None:
    report = audit_readiness([], make_taxonomy())
    assert report.score == 0.0
    assert not report.passed
    assert report.blockers
    assert "NO-GO" in report.verdict


# -------------------------------------------------------------- perfect sample


def test_fully_instrumented_app_scores_100_and_passes() -> None:
    tax = make_taxonomy(3)
    events = []
    for i in range(3):
        events.append(event(f"Screen{i}", ts=1_700_000_000 + i * 2))
        events.append(event(f"Screen{i}", name="tap", ts=1_700_000_001 + i * 2))
    report = audit_readiness(events, tax)
    assert report.score == 100.0
    assert report.passed
    assert report.verdict == "GO"
    assert report.remediation_plan() == []


# ------------------------------------------------------------- screen coverage


def test_unobserved_screens_lower_coverage_and_are_named() -> None:
    tax = make_taxonomy(4)
    report = audit_readiness([event("Screen0"), event("Screen1")], tax)
    cov = dimension(report, "screen_coverage")
    assert cov.score == 50.0
    assert "screen_2" in cov.remediation and "screen_3" in cov.remediation


# ------------------------------------------------------------- action coverage


def test_navigation_only_instrumentation_flags_action_coverage() -> None:
    """Screen views alone give sequence but no intent, so journeys cannot be scored
    by business value in Phase 2."""
    tax = make_taxonomy(2)
    report = audit_readiness([event("Screen0"), event("Screen1")], tax)
    assert dimension(report, "action_coverage").score == 0.0
    assert "intent" in dimension(report, "action_coverage").remediation


# ----------------------------------------------------------- session integrity


def test_missing_session_ids_block_the_gate_regardless_of_other_scores() -> None:
    tax = make_taxonomy(1)
    events = [event("Screen0", session=None, name="tap") for _ in range(10)]
    report = audit_readiness(events, tax)
    session_dim = dimension(report, "session_integrity")
    assert session_dim.blocker
    assert not report.passed
    assert "blocking defect" in report.verdict


def test_out_of_order_sessions_reduce_integrity_without_blocking() -> None:
    tax = make_taxonomy(1)
    events = [
        event("Screen0", ts=1_700_000_500),
        event("Screen0", ts=1_700_000_100),  # arrives out of order
    ]
    report = audit_readiness(events, tax)
    dim = dimension(report, "session_integrity")
    assert 0 < dim.score < 100
    assert not dim.blocker  # presence is fine; only ordering degraded


def test_session_remediation_wording_scales_with_severity() -> None:
    """A 99%-scoring dimension describing itself as broken trains readers to ignore
    the whole report."""
    tax = make_taxonomy(1)

    near_perfect = [event("Screen0", ts=1_700_000_000 + i) for i in range(100)]
    near_perfect[0]["session_id"] = None  # 99% presence
    mild = dimension(audit_readiness(near_perfect, tax), "session_integrity")
    assert "must be fixed in the app" not in mild.remediation

    broken = [event("Screen0", session=None, ts=1_700_000_000 + i) for i in range(100)]
    severe = dimension(audit_readiness(broken, tax), "session_integrity")
    assert "must be fixed in the app" in severe.remediation
    assert severe.blocker


def test_blocker_overrides_a_passing_aggregate_score() -> None:
    """Guards the specific failure mode the design calls out: a good average
    concealing an unrecoverable defect."""
    tax = make_taxonomy(1)
    events = []
    for i in range(100):
        # 70% carry a session id - below the 80% blocker threshold - while every
        # other dimension is perfect.
        session = "s1" if i % 10 < 7 else None
        events.append(event("Screen0", session=session, ts=1_700_000_000 + i))
        events.append(event("Screen0", name="tap", session=session,
                            ts=1_700_000_000 + i))
    report = audit_readiness(events, tax)
    assert report.score >= report.threshold, "aggregate alone would have passed"
    assert not report.passed, "blocker must veto the aggregate"


# --------------------------------------------------------- identity stability


def test_anonymous_traffic_reduces_identity_score() -> None:
    tax = make_taxonomy(1)
    events = [event("Screen0", user=None if i % 2 else "u1", ts=1_700_000_000 + i)
              for i in range(10)]
    assert dimension(audit_readiness(events, tax), "identity_stability").score < 100


def test_session_spanning_two_users_is_reported() -> None:
    tax = make_taxonomy(1)
    events = [
        event("Screen0", user="u1", ts=1_700_000_000),
        event("Screen0", user="u2", ts=1_700_000_001),
    ]
    dim = dimension(audit_readiness(events, tax), "identity_stability")
    assert "1 session(s) span multiple user IDs" in dim.detail


# --------------------------------------------------------- naming consistency


def test_unmapped_tags_are_counted_and_surfaced() -> None:
    tax = make_taxonomy(1)
    events = [event("Screen0")] + [event("LegacyHome") for _ in range(3)]
    dim = dimension(audit_readiness(events, tax), "naming_consistency")
    assert dim.score == 50.0
    assert "LegacyHome (3x)" in dim.remediation


# ------------------------------------------------------------------ timestamps


def test_iso_and_epoch_millisecond_timestamps_are_both_accepted() -> None:
    tax = make_taxonomy(1)
    events = [
        event("Screen0", ts="2026-08-03T10:00:00Z"),
        event("Screen0", ts=1_700_000_000_000),  # milliseconds
    ]
    assert dimension(audit_readiness(events, tax), "session_integrity").score > 0


def test_alternate_field_names_are_tolerated() -> None:
    """Real exports vary; the audit reads the data it is given rather than
    demanding a rename first."""
    tax = make_taxonomy(1)
    events = [{"event": "screen_view", "screen": "Screen0",
               "session_id": "s1", "user_id": "u1", "ts": 1_700_000_000}]
    assert dimension(audit_readiness(events, tax), "screen_coverage").score == 100.0


# ----------------------------------------------------------------- thresholds


def test_threshold_is_configurable() -> None:
    tax = make_taxonomy(4)
    events = [event("Screen0"), event("Screen0", name="tap")]
    assert not audit_readiness(events, tax, threshold=90).passed
    assert audit_readiness(events, tax, threshold=10).passed


def test_report_serialises_for_ci_consumption() -> None:
    report = audit_readiness([event("Screen0")], make_taxonomy(1))
    payload = report.to_dict()
    assert set(payload) >= {"app_id", "score", "verdict", "passed", "dimensions",
                            "remediation"}
    assert len(payload["dimensions"]) == 6


def test_remediation_is_ordered_by_weighted_impact() -> None:
    tax = make_taxonomy(4)
    report = audit_readiness([event("Screen0")], tax)
    plan = report.remediation_plan()
    assert plan and plan[0].startswith("[screen_coverage]")
