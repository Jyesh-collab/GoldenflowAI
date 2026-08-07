"""Taxonomy contract tests.

Each test pins one rule that, if it silently stopped working, would corrupt the
production-to-QA join rather than raising an error.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from goldenflow.phase0.taxonomy import (
    Platform,
    ScreenDefinition,
    Sensitivity,
    Severity,
    Taxonomy,
)

CONFIG = "config/taxonomy.yaml"


def screen(screen_id: str, **overrides) -> ScreenDefinition:
    defaults = dict(
        screen_id=screen_id,
        display_name=screen_id.replace("_", " ").title(),
        domain="browse",
        analytics_tags=[screen_id.title()],
        page_objects=[f"{screen_id.title()}Screen"],
    )
    defaults.update(overrides)
    return ScreenDefinition(**defaults)


def taxonomy(*screens: ScreenDefinition, domains: list[str] | None = None) -> Taxonomy:
    return Taxonomy(
        version="test",
        app_id="test-app",
        domains=domains if domains is not None else ["browse", "checkout"],
        screens=list(screens),
    )


# ------------------------------------------------------------------ id format


@pytest.mark.parametrize("bad_id", ["CamelCase", "with-dash", "_leading", "trailing_",
                                    "double__underscore", "9numeric", ""])
def test_screen_id_must_be_snake_case(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        screen(bad_id)


@pytest.mark.parametrize("good_id", ["home", "product_detail", "otp_verify", "plp2"])
def test_valid_screen_ids_accepted(good_id: str) -> None:
    assert screen(good_id).screen_id == good_id


# ------------------------------------------------------------- uniqueness rules


def test_duplicate_screen_id_is_an_error() -> None:
    issues = taxonomy(screen("home"), screen("home")).validate_contract()
    assert any(i.rule == "unique_screen_ids" and i.severity is Severity.ERROR
               for i in issues)


def test_analytics_tag_shared_between_screens_is_an_error() -> None:
    """The rule that matters most: an ambiguous tag merges two distinct journeys
    into one silently, and nobody notices for months."""
    issues = taxonomy(
        screen("home", analytics_tags=["Shared"]),
        screen("cart", analytics_tags=["Shared"]),
    ).validate_contract()
    offending = [i for i in issues if i.rule == "unique_analytics_tags"]
    assert len(offending) == 1
    assert offending[0].severity is Severity.ERROR
    assert "cart" in offending[0].message and "home" in offending[0].message


def test_page_object_shared_between_screens_is_an_error() -> None:
    issues = taxonomy(
        screen("home", page_objects=["SharedScreen"]),
        screen("cart", page_objects=["SharedScreen"]),
    ).validate_contract()
    assert any(i.rule == "unique_page_objects" and i.severity is Severity.ERROR
               for i in issues)


# ------------------------------------------------------------------- occlusion


@pytest.mark.parametrize("sensitivity", [Sensitivity.HIGH, Sensitivity.CRITICAL])
def test_sensitive_screen_without_occlusion_is_an_error(sensitivity) -> None:
    issues = taxonomy(
        screen("payment_method", sensitivity=sensitivity, occlusion_required=False)
    ).validate_contract()
    assert any(i.rule == "occlusion_policy" and i.severity is Severity.ERROR
               for i in issues)


@pytest.mark.parametrize("sensitivity", [Sensitivity.NONE, Sensitivity.LOW])
def test_non_sensitive_screen_needs_no_occlusion(sensitivity) -> None:
    issues = taxonomy(screen("home", sensitivity=sensitivity)).validate_contract()
    assert not any(i.rule == "occlusion_policy" for i in issues)


def test_critical_sensitivity_demands_full_screen_occlusion() -> None:
    assert Sensitivity.CRITICAL.requires_full_screen_occlusion
    assert not Sensitivity.HIGH.requires_full_screen_occlusion
    assert Sensitivity.HIGH.requires_occlusion


# ----------------------------------------------------------- observe / automate


def test_screen_without_analytics_tags_warns() -> None:
    issues = taxonomy(screen("ghost", analytics_tags=[])).validate_contract()
    found = [i for i in issues if i.rule == "observable"]
    assert len(found) == 1 and found[0].severity is Severity.WARNING


def test_unbound_screen_warns_but_unbound_critical_screen_errors() -> None:
    """Severity escalates with business criticality - an unbound ordinary screen is
    a backlog item, an unbound critical screen blocks Phase 4."""
    ordinary = taxonomy(screen("blog", page_objects=[])).validate_contract()
    assert [i.severity for i in ordinary if i.rule == "automatable"] == [Severity.WARNING]

    critical = taxonomy(
        screen("cart", page_objects=[], critical=True)
    ).validate_contract()
    assert [i.severity for i in critical if i.rule == "automatable"] == [Severity.ERROR]


def test_unknown_domain_is_an_error() -> None:
    issues = taxonomy(screen("home", domain="nonexistent")).validate_contract()
    assert any(i.rule == "known_domains" and i.severity is Severity.ERROR
               for i in issues)


def test_domain_check_skipped_when_no_domains_declared() -> None:
    issues = taxonomy(screen("home", domain="anything"), domains=[]).validate_contract()
    assert not any(i.rule == "known_domains" for i in issues)


# --------------------------------------------------------------------- lookups


def test_tag_and_page_object_resolution() -> None:
    tax = taxonomy(screen("cart", analytics_tags=["Cart", "Basket"],
                          page_objects=["CartScreen"]))
    assert tax.resolve_tag("Basket").screen_id == "cart"
    assert tax.resolve_page_object("CartScreen").screen_id == "cart"
    assert tax.resolve_tag("Unknown") is None
    assert tax.by_id("cart").display_name == "Cart"
    assert tax.known_tags == {"Cart", "Basket"}


def test_platform_coverage_semantics() -> None:
    assert Platform.BOTH.covers(Platform.ANDROID)
    assert Platform.ANDROID.covers(Platform.BOTH)
    assert Platform.ANDROID.covers(Platform.ANDROID)
    assert not Platform.ANDROID.covers(Platform.IOS)


# ----------------------------------------------------------------- fingerprint


def test_fingerprint_is_order_independent() -> None:
    a, b = screen("home"), screen("cart")
    assert taxonomy(a, b).fingerprint() == taxonomy(b, a).fingerprint()


def test_fingerprint_changes_when_a_join_key_changes() -> None:
    """Drift detection depends on this: if the app repo and automation repo hold
    different taxonomies, the fingerprints must differ."""
    before = taxonomy(screen("home", analytics_tags=["Home"]))
    after = taxonomy(screen("home", analytics_tags=["HomeV2"]))
    assert before.fingerprint() != after.fingerprint()


def test_fingerprint_ignores_cosmetic_fields() -> None:
    before = taxonomy(screen("home", display_name="Home", notes="a"))
    after = taxonomy(screen("home", display_name="Home Feed", notes="b"))
    assert before.fingerprint() == after.fingerprint()


# ------------------------------------------------------- the shipped taxonomy


def test_shipped_taxonomy_has_no_contract_errors() -> None:
    tax = Taxonomy.from_yaml(CONFIG)
    errors = [i for i in tax.validate_contract() if i.severity is Severity.ERROR]
    assert not errors, "shipped taxonomy violates its own contract:\n" + "\n".join(
        i.format() for i in errors
    )


def test_shipped_taxonomy_occludes_every_sensitive_screen() -> None:
    tax = Taxonomy.from_yaml(CONFIG)
    unmasked = [
        s.screen_id
        for s in tax.screens
        if s.sensitivity.requires_occlusion and not s.occlusion_required
    ]
    assert not unmasked, f"sensitive screens would reach session replay: {unmasked}"


def test_shipped_taxonomy_round_trips_through_yaml(tmp_path) -> None:
    tax = Taxonomy.from_yaml(CONFIG)
    out = tmp_path / "taxonomy.yaml"
    tax.to_yaml(out)
    assert Taxonomy.from_yaml(out).fingerprint() == tax.fingerprint()
