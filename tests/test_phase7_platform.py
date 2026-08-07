"""Tenancy, onboarding, connectors, parity and portfolio tests."""

from __future__ import annotations

from datetime import date

import pytest

from goldenflow.phase0.taxonomy import Sensitivity, Severity
from goldenflow.phase1.tracking_plan import PropertyType
from goldenflow.phase7.onboarding import (
    infer_property_type,
    infer_sensitivity,
    scaffold,
    write_scaffold,
)
from goldenflow.phase7.parity import compare_platforms, split_by_platform
from goldenflow.phase7.portfolio import (
    Connector,
    ConnectorRegistry,
    TenantHealth,
    build_portfolio,
)
from goldenflow.phase7.tenancy import (
    Permission,
    PermissionDenied,
    Principal,
    Role,
    TenantConfig,
    TenantRegistry,
)
from helpers import ev

TENANTS = "config/tenants.yaml"


def event(name="screen_view", screen="Cart", **props):
    return {"event_name": name, "screen_tag": screen, "session_id": "s1",
            "timestamp": "2026-08-04T10:00:00Z", "properties": props}


# ================================================================== tenancy


def test_tenant_id_must_be_a_slug() -> None:
    with pytest.raises(ValueError):
        TenantConfig(tenant_id="not a slug!")


def test_per_tenant_weights_override_the_platform_default() -> None:
    """A payments app and a shopping app should not share a risk weighting."""
    default = TenantConfig(tenant_id="a").scoring_weights()
    custom = TenantConfig(tenant_id="b", weights={"risk": 0.4}).scoring_weights()
    assert custom.risk == 0.4
    assert custom.frequency == default.frequency


def test_shared_configuration_between_tenants_is_flagged() -> None:
    """One team's taxonomy edit silently re-keying another's journey graph presents
    as a coverage collapse, not an error."""
    registry = TenantRegistry(tenants=[
        TenantConfig(tenant_id="a", store_path="artifacts/shared.db"),
        TenantConfig(tenant_id="b", store_path="artifacts/shared.db"),
    ])
    problems = registry.isolation_check()
    assert problems and "shared" in problems[0]


def test_isolated_tenants_pass_the_check() -> None:
    registry = TenantRegistry(tenants=[
        TenantConfig(tenant_id="a", store_path="artifacts/a.db"),
        TenantConfig(tenant_id="b", store_path="artifacts/b.db"),
    ])
    assert registry.isolation_check() == []


def test_duplicate_tenant_is_refused() -> None:
    registry = TenantRegistry(tenants=[TenantConfig(tenant_id="a")])
    with pytest.raises(ValueError, match="already exists"):
        registry.add(TenantConfig(tenant_id="a"))


def test_unknown_tenant_error_names_the_known_ones() -> None:
    registry = TenantRegistry(tenants=[TenantConfig(tenant_id="a")])
    with pytest.raises(KeyError, match="known: a"):
        registry.require("nope")


def test_shipped_tenant_registry_loads_and_is_isolated() -> None:
    registry = TenantRegistry.from_yaml(TENANTS)
    assert len(registry.tenants) >= 3
    assert registry.isolation_check() == []


# ===================================================================== RBAC


@pytest.mark.parametrize("role,permission,allowed", [
    (Role.VIEWER, Permission.READ, True),
    (Role.VIEWER, Permission.GENERATE, False),
    (Role.VIEWER, Permission.APPROVE_DELETION, False),
    (Role.ENGINEER, Permission.GENERATE, True),
    (Role.ENGINEER, Permission.APPROVE_DELETION, False),
    (Role.OWNER, Permission.APPROVE_DELETION, True),
    (Role.OWNER, Permission.ONBOARD_TENANT, False),
    (Role.PLATFORM, Permission.ONBOARD_TENANT, True),
])
def test_role_permissions(role: Role, permission: Permission, allowed: bool) -> None:
    assert Principal("p", role).may(permission) is allowed


def test_tenant_scoping_restricts_a_principal() -> None:
    principal = Principal("p", Role.OWNER, tenants=("acme-shop",))
    assert principal.may(Permission.APPROVE_DELETION, "acme-shop")
    assert not principal.may(Permission.APPROVE_DELETION, "acme-pay")


def test_require_raises_rather_than_returning_false() -> None:
    """Same reasoning as Phase 0's assert_deletable: a boolean is easy to forget."""
    with pytest.raises(PermissionDenied, match="may not approve_deletion"):
        Principal("p", Role.ENGINEER).require(Permission.APPROVE_DELETION)


def test_denial_names_the_role_that_would_suffice() -> None:
    with pytest.raises(PermissionDenied, match="Required role: owner"):
        Principal("p", Role.VIEWER).require(Permission.APPROVE_DELETION)


def test_onboarding_a_tenant_requires_platform_role() -> None:
    registry = TenantRegistry()
    with pytest.raises(PermissionDenied):
        registry.add(TenantConfig(tenant_id="new"), Principal("p", Role.OWNER))
    registry.add(TenantConfig(tenant_id="new"), Principal("p", Role.PLATFORM))
    assert registry.get("new")


# =============================================================== onboarding


@pytest.mark.parametrize("name,expected", [
    ("PaymentMethod", Sensitivity.CRITICAL),
    ("Login", Sensitivity.CRITICAL),
    ("OTPVerify", Sensitivity.CRITICAL),
    ("AddressBook", Sensitivity.HIGH),
    ("OrderHistory", Sensitivity.HIGH),
    ("SearchResults", Sensitivity.LOW),
])
def test_sensitivity_inference(name: str, expected: Sensitivity) -> None:
    assert infer_sensitivity(name)[0] is expected


def test_unmatched_screens_default_to_low_never_none() -> None:
    """An unreviewed `none` on a screen that turns out to show card details is a
    breach, and the scaffolder cannot see the screen."""
    sensitivity, reason = infer_sensitivity("Zorblatt")
    assert sensitivity is Sensitivity.LOW
    assert sensitivity is not Sensitivity.NONE
    assert "defaulted" in reason


def test_inference_reason_travels_into_the_draft() -> None:
    result = scaffold([event(screen="PaymentMethod")], app_id="x")
    screen = result.taxonomy.screens[0]
    assert "DRAFT" in screen.notes and "inferred" in screen.notes


@pytest.mark.parametrize("values,expected", [
    ([True, False], PropertyType.BOOLEAN),
    ([1, 2, 3], PropertyType.INTEGER),
    ([1, 2.5], PropertyType.NUMBER),
    (["a", "b"], PropertyType.STRING),
    ([None, None], PropertyType.STRING),
])
def test_property_type_inference(values, expected) -> None:
    assert infer_property_type(values) is expected


def test_scaffold_drafts_screens_and_events_from_a_sample() -> None:
    events = [event(screen="Cart"), event(screen="PDP"),
              event("add_to_cart", "PDP", product_id="sku_1", price=9.99)]
    result = scaffold(events, app_id="acme")
    assert result.screens_found == 2
    assert {s.screen_id for s in result.taxonomy.screens} == {"cart", "pdp"}
    assert {e.name for e in result.tracking_plan.events} == {"screen_view", "add_to_cart"}


def test_consistently_present_properties_become_required() -> None:
    events = [event("add_to_cart", "PDP", product_id=f"sku_{i}") for i in range(20)]
    result = scaffold(events, app_id="acme")
    schema = next(e for e in result.tracking_plan.events if e.name == "add_to_cart")
    assert schema.property_by_name("product_id").required


def test_intermittent_properties_are_optional() -> None:
    """A property missing 10% of the time is not a contract."""
    events = [event("add_to_cart", "PDP", product_id="x") for _ in range(9)]
    events.append(event("add_to_cart", "PDP", product_id=None))
    result = scaffold(events, app_id="acme")
    schema = next(e for e in result.tracking_plan.events if e.name == "add_to_cart")
    assert not schema.property_by_name("product_id").required


def test_screen_view_is_never_restricted_to_observed_screens() -> None:
    result = scaffold([event(screen="Cart")], app_id="acme")
    schema = next(e for e in result.tracking_plan.events if e.name == "screen_view")
    assert schema.allows_any_screen


def test_scaffold_predicts_its_own_validation_errors() -> None:
    """Without this a team sees a wall of errors and concludes the generator is
    broken, rather than that it handed them a to-do list."""
    result = scaffold([event(screen="PaymentMethod")], app_id="acme")
    errors = result.contract_errors()
    assert errors, "a scaffold cannot satisfy the contract - no Page Objects exist"
    assert all("page_objects" in e or "automatable" in e for e in errors)
    assert "EXPECTED" in result.format()


def test_scaffold_review_items_cover_the_unsafe_inferences() -> None:
    result = scaffold([event(screen="Cart")], app_id="acme")
    kinds = {item.kind for item in result.review_items}
    assert {"sensitivity", "domain", "page_object"} <= kinds
    assert result.estimated_review_hours > 0


def test_scaffold_writes_drafts_with_an_unmistakable_banner(tmp_path) -> None:
    result = scaffold([event(screen="Cart")], app_id="acme")
    written = write_scaffold(result, tmp_path)
    text = written["taxonomy"].read_text(encoding="utf-8")
    assert text.startswith("# DRAFT")
    assert "NOT signed off" in text
    assert "pii-policy" in text


def test_scaffolded_taxonomy_has_no_duplicate_join_keys() -> None:
    """Whatever else is draft, the join keys must be sound or nothing downstream
    can use it."""
    events = [event(screen="Cart"), event(screen="Cart"), event(screen="PDP")]
    result = scaffold(events, app_id="acme")
    errors = [i for i in result.taxonomy.validate_contract()
              if i.severity is Severity.ERROR
              and i.rule in ("unique_screen_ids", "unique_analytics_tags")]
    assert errors == []


# =============================================================== connectors


def test_builtin_connectors_are_registered() -> None:
    registry = ConnectorRegistry()
    assert {"uxcam", "firebase", "mixpanel", "raw"} <= set(registry.names)


def test_uxcam_is_the_only_auto_capture_source() -> None:
    """Auto-capture changes the onboarding estimate by weeks, so it is surfaced."""
    assert ConnectorRegistry().auto_capture_sources() == ["uxcam"]


def test_registering_over_a_builtin_requires_intent() -> None:
    registry = ConnectorRegistry()
    replacement = Connector(name="uxcam", adapter=lambda r: r)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(replacement)
    registry.register(replacement, replace=True)
    assert registry.require("uxcam").adapter is replacement.adapter


def test_unknown_connector_error_lists_the_registered_ones() -> None:
    with pytest.raises(KeyError, match="registered:"):
        ConnectorRegistry().require("amplitude")


def test_a_new_connector_is_a_registration_not_a_code_edit() -> None:
    registry = ConnectorRegistry()
    registry.register(Connector(name="amplitude", adapter=lambda r: r,
                                notes="Manual instrumentation."))
    assert "amplitude" in registry.names


# =================================================================== parity


def platform_trace(*screens: str, platform: str, session: str):
    from goldenflow.phase2.sessionize import sessionize
    events = [ev(s, i * 10, session) for i, s in enumerate(screens)]
    for e in events:
        e.platform = platform
    return sessionize(events).traces[0]


def test_platforms_are_normalised() -> None:
    grouped = split_by_platform([
        platform_trace("home", "cart", platform="iOS", session="a"),
        platform_trace("home", "cart", platform="iphone", session="b"),
        platform_trace("home", "cart", platform="Android", session="c"),
    ])
    assert set(grouped) == {"ios", "android"}
    assert len(grouped["ios"]) == 2


def test_a_single_platform_yields_no_comparison() -> None:
    report = compare_platforms(
        [platform_trace("home", "cart", platform="android", session=f"s{i}")
         for i in range(10)])
    assert report.journeys == []
    assert len(report.platforms) < 2


def test_identical_behaviour_shows_no_divergence() -> None:
    traces = (
        [platform_trace("home", "cart", platform="android", session=f"a{i}")
         for i in range(10)]
        + [platform_trace("home", "cart", platform="ios", session=f"i{i}")
           for i in range(10)]
    )
    assert compare_platforms(traces).divergence == 0.0


def test_platform_exclusive_journeys_are_identified() -> None:
    traces = (
        [platform_trace("home", "cart", platform="android", session=f"a{i}")
         for i in range(10)]
        + [platform_trace("home", "saved_cards", platform="ios", session=f"i{i}")
           for i in range(10)]
    )
    report = compare_platforms(traces)
    exclusive = {j.exclusive_to for j in report.exclusive_journeys}
    assert exclusive == {"android", "ios"}


def test_coverage_asymmetry_is_the_headline_finding() -> None:
    """A team writes the checkout test on Android, ships iOS, and nobody notices
    the iOS path is untested because the aggregate looks fine."""
    traces = (
        [platform_trace("home", "cart", platform="android", session=f"a{i}")
         for i in range(10)]
        + [platform_trace("home", "cart", platform="ios", session=f"i{i}")
           for i in range(10)]
    )
    report = compare_platforms(traces, covered_sequences={
        "android": {("home", "cart")},
        "ios": set(),
    })
    assert len(report.asymmetric_coverage) == 1
    assert "UNTESTED on ios" in report.format()


def test_rare_journeys_do_not_trigger_parity_findings() -> None:
    traces = (
        [platform_trace("home", "cart", platform="android", session=f"a{i}")
         for i in range(10)]
        + [platform_trace("home", "oddity", platform="ios", session="i0")]
        + [platform_trace("home", "cart", platform="ios", session=f"i{i}")
           for i in range(1, 10)]
    )
    report = compare_platforms(traces, min_sessions=5)
    assert not any("oddity" in j.sequence for j in report.journeys)


# ================================================================ portfolio


def test_a_tenant_with_no_measurement_is_not_healthy() -> None:
    """Reporting it as healthy is how a tenant whose pipeline silently stopped sits
    green on a director's dashboard for a quarter."""
    assert TenantHealth("a", status="active").health == "no-data"


def test_health_reflects_coverage_and_critical_gaps() -> None:
    assert TenantHealth("a", coverage_pct=80.0).health == "healthy"
    assert TenantHealth("b", coverage_pct=30.0).health == "at-risk"
    assert TenantHealth("c", coverage_pct=90.0, critical_gaps=1).health == "at-risk"
    assert TenantHealth("d", status="onboarding").health == "onboarding"


def test_defect_delta_is_measured_against_each_tenant_own_baseline() -> None:
    """A portfolio average lets a strong app hide a weak one."""
    tenant = TenantHealth("a", escaped_defects=7, baseline_escaped_defects=14.0)
    assert tenant.defect_delta_pct == -50.0
    assert TenantHealth("b", escaped_defects=7).defect_delta_pct is None


def test_onboarding_duration_is_measured_against_the_target() -> None:
    report = build_portfolio([
        TenantHealth("a", onboarded_on=date(2026, 6, 1), live_on=date(2026, 6, 11)),
        TenantHealth("b", onboarded_on=date(2026, 7, 1), live_on=date(2026, 7, 13)),
    ], onboarding_target_days=14)
    assert report.mean_onboarding_days == 11.0
    assert report.meets_onboarding_target


def test_onboarding_target_is_none_without_data() -> None:
    assert build_portfolio([TenantHealth("a")]).meets_onboarding_target is None


def test_weakest_tenants_are_surfaced_for_platform_effort() -> None:
    report = build_portfolio([
        TenantHealth("strong", coverage_pct=90.0),
        TenantHealth("weak", coverage_pct=20.0),
        TenantHealth("mid", coverage_pct=55.0),
    ])
    assert [t.tenant_id for t in report.weakest(2)] == ["weak", "mid"]


def test_portfolio_serialises() -> None:
    payload = build_portfolio([TenantHealth("a", coverage_pct=80.0)]).to_dict()
    assert payload["tenants"] == 1
    assert payload["per_tenant"][0]["health"] == "healthy"
