"""Ingestion and event store tests.

The behaviours pinned hardest are the two that silently corrupt journeys if they
regress: idempotent re-ingest, and refusing to invent a timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase1.ingest import normalise_event, normalise_stream
from goldenflow.phase1.store import CanonicalEvent, Dialect, EventStore, generate_ddl

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
T0 = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store() -> EventStore:
    with EventStore(":memory:") as s:
        yield s


def event(**kw) -> CanonicalEvent:
    defaults = dict(event_name="screen_view", screen_id="home", screen_tag="Home",
                    session_id="s1", user_id="u1", timestamp=T0)
    defaults.update(kw)
    return CanonicalEvent(**defaults)


# ------------------------------------------------------------------- adapters


def test_raw_adapter_resolves_screen_through_the_taxonomy() -> None:
    ev, reason = normalise_event(
        {"event_name": "screen_view", "screen_tag": "PDP", "session_id": "s1",
         "timestamp": "2026-08-03T10:00:00Z"},
        screen_resolver=TAXONOMY.resolve_tag,
    )
    assert reason is None
    assert ev.screen_id == "product_detail"      # resolved from the alias "PDP"
    assert ev.event_type == "screen_view"


def test_rudderstack_adapter() -> None:
    ev, _ = normalise_event(
        {"type": "screen", "event": "Cart", "userId": "u1", "anonymousId": "a1",
         "originalTimestamp": "2026-08-03T10:00:00Z",
         "context": {"sessionId": "s1", "app": {"version": "8.2.0"},
                     "os": {"name": "Android"}},
         "properties": {"item_count": 3}},
        source="rudderstack", screen_resolver=TAXONOMY.resolve_tag,
    )
    assert ev.screen_id == "cart" and ev.session_id == "s1"
    assert ev.app_version == "8.2.0" and ev.platform == "Android"
    assert ev.properties["item_count"] == 3


def test_firebase_adapter_unpacks_typed_event_params() -> None:
    ev, _ = normalise_event(
        {"event_name": "add_to_cart",
         "event_timestamp": 1_785_000_000_000_000,     # microseconds
         "user_id": "u1", "user_pseudo_id": "p1", "platform": "IOS",
         "event_params": [
             {"key": "firebase_screen", "value": {"string_value": "PDP"}},
             {"key": "ga_session_id", "value": {"int_value": 12345}},
             {"key": "price", "value": {"double_value": 19.99}},
         ]},
        source="firebase", screen_resolver=TAXONOMY.resolve_tag,
    )
    assert ev.screen_id == "product_detail"
    # Firebase emits ga_session_id as an integer. It must be coerced, or the event
    # table keys the session as 12345 while the crash table keys it as "12345" and
    # the risk join silently returns nothing.
    assert ev.session_id == "12345"
    assert ev.properties["price"] == 19.99


def test_numeric_identifiers_are_coerced_to_strings() -> None:
    ev, _ = normalise_event(
        {"event_name": "x", "timestamp": 1_785_000_000,
         "session_id": 12345, "user_id": 678, "anonymous_id": 9.0}
    )
    assert (ev.session_id, ev.user_id, ev.anonymous_id) == ("12345", "678", "9")


def test_mixpanel_adapter_strips_reserved_properties() -> None:
    ev, _ = normalise_event(
        {"event": "add_to_cart",
         "properties": {"$screen_name": "PDP", "$session_id": "s1",
                        "distinct_id": "u1", "time": 1_785_000_000,
                        "product_id": "sku_1"}},
        source="mixpanel", screen_resolver=TAXONOMY.resolve_tag,
    )
    assert ev.screen_id == "product_detail" and ev.session_id == "s1"
    assert ev.properties == {"product_id": "sku_1"}   # $-prefixed keys removed


UXCAM_EVENT = {
    "eventId": "e-1",
    "eventName": "add_to_cart",
    "eventScreen": "PDP",
    "eventDate": "2026-08-04T15:30:45Z",
    "url": "https://app.uxcam.com/session/abc123",
    "sessionProperty": {"sessionId": "sess-9", "hasVideo": True, "durationSec": 98.8},
    "userProperty": {"kUXCam_UserIdentity": "user-9", "subscription": "premium"},
    "eventProperty": {"product_id": "sku_1", "price": 19.99},
    "device": {"appVersion": "8.2.0", "platform": "android", "deviceId": "dev-1"},
}


def test_uxcam_adapter() -> None:
    ev, reason = normalise_event(
        UXCAM_EVENT, source="uxcam", screen_resolver=TAXONOMY.resolve_tag)
    assert reason is None
    assert ev.screen_id == "product_detail"      # eventScreen "PDP" via the taxonomy
    assert ev.session_id == "sess-9"
    assert ev.user_id == "user-9" and ev.anonymous_id == "dev-1"
    assert ev.app_version == "8.2.0" and ev.platform == "android"
    assert ev.properties["product_id"] == "sku_1"


def test_uxcam_screen_comes_from_every_event_without_instrumentation() -> None:
    """eventScreen is auto-tagged on every event, so the screen sequence falls out
    of ordering a session - no per-screen instrumentation needed."""
    ev, _ = normalise_event(
        {**UXCAM_EVENT, "eventName": "some_custom_event"},
        source="uxcam", screen_resolver=TAXONOMY.resolve_tag)
    assert ev.screen_id == "product_detail"


def test_uxcam_session_replay_url_is_carried_through() -> None:
    """So a mined journey or a coverage gap can point a reviewer at a real user
    walking the path."""
    ev, _ = normalise_event(UXCAM_EVENT, source="uxcam")
    assert ev.properties["uxcam_session_url"] == "https://app.uxcam.com/session/abc123"


def test_uxcam_iso_timestamps_parse() -> None:
    ev, _ = normalise_event(UXCAM_EVENT, source="uxcam")
    assert ev.timestamp == datetime(2026, 8, 4, 15, 30, 45, tzinfo=timezone.utc)


def test_uxcam_export_unwraps_the_api_envelope() -> None:
    from goldenflow.phase1.ingest import load_uxcam_export
    assert load_uxcam_export({"success": True, "data": [UXCAM_EVENT]}) == [UXCAM_EVENT]
    assert load_uxcam_export([UXCAM_EVENT]) == [UXCAM_EVENT]


def test_uxcam_export_raises_on_a_failed_response() -> None:
    from goldenflow.phase1.ingest import load_uxcam_export
    with pytest.raises(ValueError, match="reported failure"):
        load_uxcam_export({"success": False, "message": "bad apikey"})


def test_packed_datetime_is_not_mistaken_for_an_epoch() -> None:
    """20260804153045 is ~2.0e13. The millisecond heuristic would divide it by 1000
    and return the year 2612 - silently, with every journey misordered."""
    ev, _ = normalise_event({"event_name": "tap", "timestamp": 20260804153045})
    assert ev.timestamp == datetime(2026, 8, 4, 15, 30, 45, tzinfo=timezone.utc)


def test_epoch_millis_still_parse_as_epoch() -> None:
    """The packed-datetime branch must not capture ordinary epoch values."""
    ev, _ = normalise_event({"event_name": "x", "timestamp": 1_785_000_000_000})
    assert 2020 < ev.timestamp.year < 2030


def test_unknown_source_is_rejected() -> None:
    ev, reason = normalise_event({"event_name": "x"}, source="segment")
    assert ev is None and "unknown source" in reason


# ----------------------------------------------------------------- rejections


def test_event_without_a_timestamp_is_rejected() -> None:
    """Defaulting to now would corrupt every sequence the event touches."""
    ev, reason = normalise_event({"event_name": "screen_view", "session_id": "s1"})
    assert ev is None and "timestamp" in reason


def test_event_without_a_name_is_rejected() -> None:
    ev, reason = normalise_event({"timestamp": "2026-08-03T10:00:00Z"})
    assert ev is None and reason == "no event name"


def test_unresolvable_tags_are_ingested_as_orphans_not_dropped() -> None:
    """Dropping them would hide the drift the quality monitors exist to measure."""
    result = normalise_stream(
        [{"event_name": "screen_view", "screen_tag": "LegacyCheckout",
          "session_id": "s1", "timestamp": "2026-08-03T10:00:00Z"}],
        screen_resolver=TAXONOMY.resolve_tag,
    )
    assert result.ingested == 1
    assert result.events[0].screen_id is None
    assert result.orphan_tags == {"LegacyCheckout": 1}


def test_ingest_result_reports_acceptance_rate() -> None:
    result = normalise_stream([
        {"event_name": "screen_view", "screen_tag": "Home", "timestamp": "2026-08-03T10:00:00Z"},
        {"event_name": "screen_view", "screen_tag": "Home"},          # no timestamp
    ], screen_resolver=TAXONOMY.resolve_tag)
    assert result.ingested == 1 and len(result.rejected) == 1
    assert result.acceptance_rate == 50.0


# ------------------------------------------------------------- timestamp forms


@pytest.mark.parametrize("value", [
    "2026-08-03T10:00:00Z",
    "2026-08-03T10:00:00+00:00",
    1_785_000_000,                 # seconds
    1_785_000_000_000,             # milliseconds
    1_785_000_000_000_000,         # microseconds (Firebase)
])
def test_timestamp_forms_are_all_accepted(value) -> None:
    ev, reason = normalise_event({"event_name": "x", "timestamp": value})
    assert reason is None and ev.timestamp.tzinfo is not None


# ------------------------------------------------------------------ identity


def test_event_id_is_content_addressed_and_stable() -> None:
    assert event().compute_id() == event().compute_id()


def test_event_id_changes_with_content() -> None:
    assert event().compute_id() != event(session_id="s2").compute_id()
    assert event().compute_id() != event(timestamp=T0 + timedelta(seconds=1)).compute_id()


def test_reingesting_the_same_batch_is_idempotent(store: EventStore) -> None:
    """At-least-once delivery is the norm across every vendor in the stack; random
    IDs would turn each redelivery into a duplicate that inflates journey counts."""
    batch = [event(), event(event_name="tap", timestamp=T0 + timedelta(seconds=5))]
    assert store.insert_events(batch) == 2
    assert store.insert_events(batch) == 0
    assert store.count_events() == 2


# --------------------------------------------------------------------- store


def test_sessions_read_back_in_time_order(store: EventStore) -> None:
    store.insert_events([
        event(event_name="c", timestamp=T0 + timedelta(seconds=20)),
        event(event_name="a", timestamp=T0),
        event(event_name="b", timestamp=T0 + timedelta(seconds=10)),
    ])
    assert [e.event_name for e in store.session_events("s1")] == ["a", "b", "c"]


def test_store_round_trips_properties_and_types(store: EventStore) -> None:
    store.insert_events([event(properties={"price": 19.99, "qty": 2, "ok": True})])
    loaded = store.session_events("s1")[0]
    assert loaded.properties == {"price": 19.99, "qty": 2, "ok": True}
    assert loaded.timestamp == T0


def test_store_counts_orphans_and_null_sessions(store: EventStore) -> None:
    store.insert_events([
        event(),
        event(event_name="orphan", screen_id=None, screen_tag="LegacyCheckout",
              timestamp=T0 + timedelta(seconds=1)),
        event(event_name="nosession", session_id=None,
              timestamp=T0 + timedelta(seconds=2)),
    ])
    assert store.orphan_count() == 1
    assert store.null_session_count() == 1
    assert store.session_ids() == ["s1"]


def test_screen_counts_rank_by_volume(store: EventStore) -> None:
    store.insert_events(
        [event(screen_id="home", timestamp=T0 + timedelta(seconds=i)) for i in range(3)]
        + [event(screen_id="cart", timestamp=T0 + timedelta(seconds=10))]
    )
    assert list(store.screen_counts()) == ["home", "cart"]


def test_persistent_store_survives_reopen(tmp_path) -> None:
    path = tmp_path / "nested" / "events.db"
    with EventStore(path) as s:
        s.insert_events([event()])
    with EventStore(path) as s:
        assert s.count_events() == 1


# ----------------------------------------------------------------------- DDL


@pytest.mark.parametrize("dialect", list(Dialect))
def test_ddl_generates_for_every_dialect(dialect: Dialect) -> None:
    ddl = generate_ddl(dialect)
    assert "events" in ddl and "risk_signals" in ddl and "session_id" in ddl


def test_bigquery_ddl_partitions_and_clusters_for_session_reads() -> None:
    ddl = generate_ddl(Dialect.BIGQUERY, dataset="prod")
    assert "PARTITION BY DATE(timestamp)" in ddl
    assert "CLUSTER BY session_id" in ddl
    assert "`prod.events`" in ddl


def test_clickhouse_ddl_is_idempotent_on_reingest() -> None:
    ddl = generate_ddl(Dialect.CLICKHOUSE)
    assert "ReplacingMergeTree" in ddl
    assert "ORDER BY (session_id, timestamp, event_id)" in ddl


def test_sqlite_ddl_matches_the_reference_store() -> None:
    """The DDL and the running store must not drift apart."""
    with EventStore(":memory:") as s:
        s.insert_events([event()])
        assert s.count_events() == 1
