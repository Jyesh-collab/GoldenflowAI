"""Risk join, PII scanning and data quality monitor tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase1.pii import PiiKind, PiiScanner, scan_events
from goldenflow.phase1.quality import Thresholds, run_quality_checks
from goldenflow.phase1.risk import (
    RiskSignal,
    from_crashlytics,
    from_sentry,
    join_risk_signals,
    localisation_rate,
    normalise_risk_stream,
    risk_by_screen,
)
from goldenflow.phase1.store import CanonicalEvent, EventStore

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
T0 = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def event(screen_id: str, offset: int, session: str = "s1") -> CanonicalEvent:
    return CanonicalEvent(
        event_name="screen_view", screen_id=screen_id, screen_tag=screen_id,
        session_id=session, user_id="u1", timestamp=T0 + timedelta(seconds=offset),
    ).finalised()


def signal(offset: int, session: str = "s1", kind: str = "crash") -> RiskSignal:
    return RiskSignal(kind=kind, session_id=session,
                      timestamp=T0 + timedelta(seconds=offset)).finalised()


# ============================================================== risk signals


def test_signal_is_attributed_to_the_preceding_screen() -> None:
    """A crash has no *next* event because the app died - attributing forward would
    be wrong in exactly the case that matters most."""
    session = [event("home", 0), event("cart", 10), event("payment_method", 20)]
    joined = join_risk_signals([signal(15)], {"s1": session})
    assert joined[0].screen_id == "cart"
    assert joined[0].position_in_session == 1


def test_signal_after_the_last_event_attributes_to_the_last_screen() -> None:
    session = [event("home", 0), event("payment_method", 10)]
    joined = join_risk_signals([signal(30)], {"s1": session})
    assert joined[0].screen_id == "payment_method"


def test_signal_before_the_session_starts_is_left_unlocated() -> None:
    """A mislocated crash corrupts the risk weighting of a journey that had nothing
    to do with it; an unlocated one is still useful as a volume signal."""
    joined = join_risk_signals([signal(-10)], {"s1": [event("home", 0)]})
    assert joined[0].screen_id is None
    assert not joined[0].located


def test_signal_for_an_unknown_session_is_left_unlocated() -> None:
    joined = join_risk_signals([signal(5, session="ghost")], {"s1": [event("home", 0)]})
    assert not joined[0].located


def test_join_orders_session_events_defensively() -> None:
    """Callers may pass events in any order; the join must not trust the ordering."""
    unordered = [event("payment_method", 20), event("home", 0), event("cart", 10)]
    joined = join_risk_signals([signal(15)], {"s1": unordered})
    assert joined[0].screen_id == "cart"


def test_localisation_rate_measures_session_id_propagation() -> None:
    """A low rate means session IDs are not flowing from the analytics SDK into the
    crash reporter - the most common Phase 1 integration defect."""
    joined = join_risk_signals(
        [signal(5), signal(5, session="ghost")], {"s1": [event("home", 0)]}
    )
    assert localisation_rate(joined) == 50.0
    assert localisation_rate([]) == 0.0


def test_risk_by_screen_ranks_failures() -> None:
    sessions = {"s1": [event("cart", 0)], "s2": [event("home", 0, "s2")]}
    joined = join_risk_signals(
        [signal(1), signal(2), signal(1, session="s2")], sessions
    )
    assert list(risk_by_screen(joined)) == ["cart", "home"]


def test_crashlytics_adapter() -> None:
    s = from_crashlytics({
        "event_timestamp": int(T0.timestamp() * 1_000_000),
        "issue_title": "NPE in CartAdapter", "error_type": "FATAL", "is_fatal": True,
        "application": {"session_id": "s1"}, "user_id": "u1",
    })
    assert s.kind == "crash" and s.session_id == "s1" and s.fatal
    assert s.source == "crashlytics"


def test_crashlytics_anr_is_distinguished_from_a_crash() -> None:
    s = from_crashlytics({
        "event_timestamp": int(T0.timestamp() * 1_000_000),
        "error_type": "ANR", "application": {"session_id": "s1"},
    })
    assert s.kind == "anr"


def test_sentry_adapter_handles_both_tag_shapes() -> None:
    as_dict = from_sentry({"timestamp": T0.isoformat(), "level": "error",
                           "title": "boom", "tags": {"session_id": "s1"}})
    as_list = from_sentry({"timestamp": T0.isoformat(), "level": "error",
                           "title": "boom",
                           "tags": [{"key": "session_id", "value": "s1"}]})
    assert as_dict.session_id == as_list.session_id == "s1"


def test_uxcam_rage_tap_becomes_a_risk_signal() -> None:
    """The signal no crash reporter provides: a user jabbing a dead button six
    times never crashes the app and never reaches Crashlytics."""
    signals = normalise_risk_stream([{
        "eventName": "Rage Tap", "eventScreen": "Cart",
        "eventDate": "2026-08-04T15:30:45Z",
        "url": "https://app.uxcam.com/session/abc",
        "sessionProperty": {"sessionId": "sess-1"},
        "userProperty": {"kUXCam_UserIdentity": "user-1"},
    }], source="uxcam")
    assert len(signals) == 1
    assert signals[0].kind == "rage_tap"
    assert signals[0].session_id == "sess-1"
    assert not signals[0].fatal, "a rage tap does not kill the app"
    assert "Cart" in signals[0].title
    assert signals[0].properties["uxcam_session_url"].endswith("abc")


def test_uxcam_ui_freeze_is_a_slow_frame_signal() -> None:
    signals = normalise_risk_stream([{
        "eventName": "UI Freeze", "eventDate": "2026-08-04T15:30:45Z",
        "sessionProperty": {"sessionId": "s1"},
    }], source="uxcam")
    assert signals[0].kind == "slow_frame"


def test_uxcam_ordinary_events_are_not_risk_signals() -> None:
    """The UXCam export mixes journey steps and failure signals in one stream."""
    assert normalise_risk_stream([{
        "eventName": "add_to_cart", "eventDate": "2026-08-04T15:30:45Z",
        "sessionProperty": {"sessionId": "s1"},
    }], source="uxcam") == []


def test_risk_records_without_a_timestamp_are_dropped() -> None:
    assert normalise_risk_stream([{"issue_title": "x"}], source="crashlytics") == []


def test_unknown_risk_source_raises() -> None:
    with pytest.raises(ValueError):
        normalise_risk_stream([], source="bugsnag")


def test_signal_id_is_content_addressed() -> None:
    assert signal(5).signal_id == signal(5).signal_id
    assert signal(5).signal_id != signal(6).signal_id


# ===================================================================== PII


def ev_with(props: dict) -> CanonicalEvent:
    return CanonicalEvent(event_name="ticket_submit", session_id="s1",
                          timestamp=T0, properties=props).finalised()


@pytest.mark.parametrize("value,kind", [
    ("user@example.com", PiiKind.EMAIL),
    ("+44 7700 900123", PiiKind.PHONE),
    ("192.168.1.44", PiiKind.IP_ADDRESS),
    ("Bearer abcdefghijklmnopqrstuvwxyz123", PiiKind.CREDENTIAL),
    ("123-45-6789", PiiKind.NATIONAL_ID),
])
def test_detectors(value: str, kind: PiiKind) -> None:
    assert kind in {k for k, _ in PiiScanner().scan_value(value)}


def test_card_numbers_are_luhn_validated() -> None:
    """An order ID that happens to be sixteen digits must not trigger an alarm - a
    scanner people learn to ignore protects nobody."""
    scanner = PiiScanner()
    assert PiiKind.CARD_NUMBER in {k for k, _ in scanner.scan_value("4111111111111111")}
    assert PiiKind.CARD_NUMBER not in {k for k, _ in scanner.scan_value("1234567812345678")}


def test_findings_never_contain_the_matched_value() -> None:
    """A PII report that quotes the PII it found is itself a leak - it gets pasted
    into tickets, Slack and CI logs."""
    report = scan_events([ev_with({"contact": "alice@example.com"})])
    assert report.findings
    for finding in report.findings:
        assert "alice@example.com" not in finding.redacted_sample
        assert "alice" not in finding.format()
        assert "<redacted" in finding.redacted_sample


def test_suspicious_key_names_are_flagged_even_when_empty() -> None:
    """A field named `email` that is null today will not be null tomorrow."""
    report = scan_events([ev_with({"email": None})])
    assert PiiKind.SUSPICIOUS_KEY in {f.kind for f in report.findings}


def test_nested_and_list_properties_are_scanned() -> None:
    nested = scan_events([ev_with({"payload": {"inner": "a@b.co"}})])
    assert nested.findings[0].property_path == "payload.inner"

    listed = scan_events([ev_with({"items": [{"contact": "a@b.co"}]})])
    assert listed.findings[0].property_path == "items[0].contact"


def test_conformant_properties_scan_clean() -> None:
    report = scan_events([CanonicalEvent(
        event_name="add_to_cart", session_id="s1", timestamp=T0,
        properties={"product_id": "sku_1234", "quantity": 2, "price": 19.99},
    )])
    assert report.clean


def test_identical_findings_aggregate() -> None:
    report = scan_events([ev_with({"contact": "a@b.co"}) for _ in range(500)])
    assert len(report.findings) == 1
    assert report.findings[0].occurrences == 500


def test_allowlist_suppresses_known_safe_fields() -> None:
    scanner = PiiScanner(allowlist={"support_email"})
    report = scan_events([ev_with({"support_email": "help@acme.com"})], scanner)
    assert report.clean


# ============================================================ quality monitors


@pytest.fixture
def populated_store() -> EventStore:
    now = datetime.now(timezone.utc)
    with EventStore(":memory:") as store:
        events = []
        screens = [s.screen_id for s in TAXONOMY.screens]
        for i, screen in enumerate(screens):
            for j in range(40):
                events.append(CanonicalEvent(
                    event_name="screen_view", screen_id=screen, screen_tag=screen,
                    session_id=f"s{i}", user_id="u1",
                    timestamp=now - timedelta(minutes=j),
                ))
        store.insert_events(events)
        yield store


def test_healthy_store_passes_every_monitor(populated_store: EventStore) -> None:
    report = run_quality_checks(populated_store, TAXONOMY,
                                thresholds=Thresholds(min_events=100))
    assert report.passed, report.format()


def test_stale_data_fails_freshness() -> None:
    now = datetime.now(timezone.utc)
    with EventStore(":memory:") as store:
        store.insert_events([CanonicalEvent(
            event_name="screen_view", screen_id="home", screen_tag="Home",
            session_id="s1", timestamp=now - timedelta(hours=12),
        )])
        report = run_quality_checks(store, TAXONOMY,
                                    thresholds=Thresholds(min_events=1))
    freshness = next(c for c in report.checks if c.name == "freshness")
    assert not freshness.passed and freshness.value > 4


def test_empty_store_short_circuits_without_dividing_by_zero() -> None:
    with EventStore(":memory:") as store:
        report = run_quality_checks(store, TAXONOMY)
    assert not report.passed
    assert len(report.checks) == 1          # stops after the volume check


def test_orphan_and_null_session_rates_are_measured() -> None:
    now = datetime.now(timezone.utc)
    with EventStore(":memory:") as store:
        store.insert_events(
            [CanonicalEvent(event_name="screen_view", screen_id="home",
                            screen_tag="Home", session_id="s1", timestamp=now)]
            + [CanonicalEvent(event_name="screen_view", screen_id=None,
                              screen_tag="LegacyCheckout", session_id=None,
                              timestamp=now - timedelta(seconds=i))
               for i in range(1, 4)]
        )
        report = run_quality_checks(store, TAXONOMY, thresholds=Thresholds(min_events=1))

    orphans = next(c for c in report.checks if c.name == "orphan_screen_tags")
    nulls = next(c for c in report.checks if c.name == "session_id_presence")
    assert orphans.value == 75.0 and not orphans.passed
    assert nulls.value == 75.0 and not nulls.passed


def test_ordering_and_duplicate_checks_warn_rather_than_block() -> None:
    """Neither corrupts a journey outright, so neither should halt the pipeline."""
    report = run_quality_checks(
        EventStore(":memory:"), TAXONOMY, thresholds=Thresholds(min_events=0)
    )
    names = {c.name: c for c in report.checks}
    if "session_ordering" in names:
        assert not names["session_ordering"].blocking
        assert not names["duplicate_events"].blocking


def test_screen_coverage_reflects_the_taxonomy(populated_store: EventStore) -> None:
    report = run_quality_checks(populated_store, TAXONOMY,
                                thresholds=Thresholds(min_events=100))
    coverage = next(c for c in report.checks if c.name == "screen_coverage")
    assert coverage.value == 100.0


def test_report_serialises_for_ci(populated_store: EventStore) -> None:
    payload = run_quality_checks(populated_store, TAXONOMY,
                                 thresholds=Thresholds(min_events=100)).to_dict()
    assert payload["passed"] is True
    assert all({"name", "passed", "value", "threshold"} <= set(c)
               for c in payload["checks"])
