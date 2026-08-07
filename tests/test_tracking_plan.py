"""Tracking plan tests - the contract between the app team and GoldenFlow."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase1.tracking_plan import (
    EventSchema,
    EventType,
    PropertySchema,
    PropertyType,
    TrackingPlan,
    ViolationKind,
)

PLAN_PATH = "config/tracking-plan.yaml"


def prop(name: str, type_: str = "string", required: bool = False) -> PropertySchema:
    return PropertySchema(name=name, type=PropertyType(type_), required=required)


def plan(*events: EventSchema, common: list[PropertySchema] | None = None) -> TrackingPlan:
    return TrackingPlan(
        version="test", app_id="test-app",
        common_properties=common or [], events=list(events),
    )


def ev(name: str, **kw) -> EventSchema:
    kw.setdefault("type", EventType.INTERACTION)
    return EventSchema(name=name, **kw)


def kinds(report) -> set[ViolationKind]:
    return {v.kind for v in report.violations}


# ------------------------------------------------------------------ type rules


@pytest.mark.parametrize("type_,value,ok", [
    ("string", "x", True), ("string", 1, False),
    ("integer", 3, True), ("integer", 3.5, False), ("integer", "3", False),
    ("integer", True, False),          # bool is not an int for schema purposes
    ("number", 3, True), ("number", 3.5, True), ("number", "3.5", False),
    ("boolean", True, True), ("boolean", 1, False),
    ("timestamp", "2026-08-03T10:00:00Z", True), ("timestamp", 1700000000, True),
])
def test_type_acceptance(type_: str, value, ok: bool) -> None:
    assert PropertyType(type_).accepts(value) is ok


def test_none_is_always_type_valid() -> None:
    """Nullability is governed by `required`, not by type - otherwise every optional
    property would report a type mismatch when absent."""
    assert all(PropertyType(t).accepts(None)
               for t in ["string", "integer", "number", "boolean", "timestamp"])


def test_numeric_string_is_drift_not_tolerance() -> None:
    """The most common cause of silent aggregation failure downstream."""
    assert not PropertyType.INTEGER.accepts("42")
    assert not PropertyType.NUMBER.accepts("42.0")


# ------------------------------------------------------------------ name rules


@pytest.mark.parametrize("bad", ["CamelCase", "with-dash", "_lead", "9start"])
def test_event_and_property_names_must_be_snake_case(bad: str) -> None:
    with pytest.raises(ValidationError):
        EventSchema(name=bad, type=EventType.INTERACTION)
    with pytest.raises(ValidationError):
        PropertySchema(name=bad, type=PropertyType.STRING)


# ------------------------------------------------------------- plan coherence


def test_duplicate_event_declaration_is_an_error() -> None:
    errors = plan(ev("add_to_cart"), ev("add_to_cart")).validate_plan()
    assert any("more than once" in e for e in errors)


def test_redefining_a_common_property_is_an_error() -> None:
    """Two teams giving the same name two meanings is how a schema rots."""
    errors = plan(
        ev("add_to_cart", properties=[prop("session_id")]),
        common=[prop("session_id", required=True)],
    ).validate_plan()
    assert any("redefines common property" in e for e in errors)


def test_screen_view_restricted_to_specific_screens_is_an_error() -> None:
    errors = plan(
        EventSchema(name="screen_view", type=EventType.SCREEN_VIEW, screens=["home"])
    ).validate_plan()
    assert any("navigation events must be emittable" in e for e in errors)


def test_coherent_plan_has_no_errors() -> None:
    assert plan(ev("add_to_cart", properties=[prop("product_id", required=True)]),
                common=[prop("session_id", required=True)]).validate_plan() == []


# ---------------------------------------------------------- stream validation


def test_undeclared_event_is_reported() -> None:
    report = plan(ev("add_to_cart")).validate_stream([{"event_name": "mystery_tap"}])
    assert ViolationKind.UNKNOWN_EVENT in kinds(report)
    assert report.unknown_event_names == ["mystery_tap"]
    assert report.conformance_pct == 0.0


def test_missing_required_property_is_reported() -> None:
    p = plan(ev("add_to_cart", properties=[prop("product_id", required=True)]))
    report = p.validate_stream([{"event_name": "add_to_cart", "properties": {}}])
    assert ViolationKind.MISSING_REQUIRED_PROPERTY in kinds(report)


def test_type_mismatch_is_reported() -> None:
    p = plan(ev("add_to_cart", properties=[prop("quantity", "integer", True)]))
    report = p.validate_stream(
        [{"event_name": "add_to_cart", "properties": {"quantity": "2"}}]
    )
    assert ViolationKind.TYPE_MISMATCH in kinds(report)
    assert "expected integer, got str" in report.violations[0].detail


def test_flattened_and_nested_property_shapes_both_work() -> None:
    """Real exports come both ways; demanding one shape first would make the
    validator unusable against half of them."""
    p = plan(ev("add_to_cart", properties=[prop("product_id", required=True)]))
    nested = p.validate_stream(
        [{"event_name": "add_to_cart", "properties": {"product_id": "sku_1"}}]
    )
    flat = p.validate_stream([{"event_name": "add_to_cart", "product_id": "sku_1"}])
    assert nested.clean and flat.clean


def test_common_properties_are_enforced_on_every_event() -> None:
    p = plan(ev("add_to_cart"), common=[prop("session_id", required=True)])
    assert not p.validate_stream([{"event_name": "add_to_cart"}]).clean
    assert p.validate_stream(
        [{"event_name": "add_to_cart", "session_id": "s1"}]
    ).clean


def test_undeclared_properties_only_reported_in_strict_mode() -> None:
    """Off by default - undeclared properties are usually harmless additions, and
    failing on them makes the contract hostile to iterate against."""
    p = plan(ev("add_to_cart", properties=[prop("product_id")]))
    stream = [{"event_name": "add_to_cart",
               "properties": {"product_id": "x", "experiment": "b"}}]
    assert p.validate_stream(stream).clean
    assert ViolationKind.UNDECLARED_PROPERTY in kinds(
        p.validate_stream(stream, strict_properties=True)
    )


def test_envelope_fields_are_not_treated_as_undeclared_properties() -> None:
    p = plan(ev("add_to_cart", properties=[prop("product_id")]))
    stream = [{"event_name": "add_to_cart", "session_id": "s1", "user_id": "u1",
               "timestamp": 1, "properties": {"product_id": "x"}}]
    assert p.validate_stream(stream, strict_properties=True).clean


def test_wrong_screen_reported_only_when_a_resolver_is_supplied() -> None:
    """Without a resolver the rule is skipped rather than guessed at."""
    taxonomy = Taxonomy.from_yaml("config/taxonomy.yaml")
    p = plan(ev("place_order", screens=["order_review"]))
    stream = [{"event_name": "place_order", "screen_tag": "Home"}]

    assert p.validate_stream(stream).clean
    report = p.validate_stream(stream, screen_resolver=taxonomy.resolve_tag)
    assert ViolationKind.WRONG_SCREEN in kinds(report)


def test_violations_are_deduplicated_across_a_large_stream() -> None:
    """One bad field seen ten thousand times must read as one problem."""
    p = plan(ev("add_to_cart", properties=[prop("product_id", required=True)]))
    report = p.validate_stream(
        [{"event_name": "add_to_cart", "properties": {}} for _ in range(10_000)]
    )
    assert len(report.violations) == 1
    assert report.events_checked == 10_000
    assert report.conformance_pct == 0.0


def test_conformance_is_the_share_of_clean_events() -> None:
    p = plan(ev("add_to_cart", properties=[prop("product_id", required=True)]))
    stream = [{"event_name": "add_to_cart", "properties": {"product_id": "x"}}] * 9
    stream += [{"event_name": "add_to_cart", "properties": {}}]
    assert p.validate_stream(stream).conformance_pct == 90.0


# ------------------------------------------------------------ shipped contract


def test_shipped_plan_is_coherent() -> None:
    errors = TrackingPlan.from_yaml(PLAN_PATH).validate_plan()
    assert not errors, "shipped tracking plan is incoherent:\n" + "\n".join(errors)


def test_shipped_plan_screens_all_exist_in_the_taxonomy() -> None:
    """A plan referencing a screen the taxonomy does not define would make the
    WRONG_SCREEN rule unenforceable."""
    p = TrackingPlan.from_yaml(PLAN_PATH)
    known = {s.screen_id for s in Taxonomy.from_yaml("config/taxonomy.yaml").screens}
    unknown = {
        screen
        for schema in p.events
        for screen in schema.screens
        if screen != "*" and screen not in known
    }
    assert not unknown, f"plan references undefined screens: {sorted(unknown)}"


def test_shipped_plan_requires_session_id_and_timestamp_on_every_event() -> None:
    """The two fields without which an event cannot become a journey step."""
    common = {p.name for p in TrackingPlan.from_yaml(PLAN_PATH).common_properties
              if p.required}
    assert {"session_id", "timestamp"} <= common
