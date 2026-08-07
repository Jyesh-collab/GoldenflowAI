"""Locator resolution, fingerprinting and self-healing tests."""

from __future__ import annotations

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase2.clustering import cluster_variants
from goldenflow.phase2.mining import Variant
from goldenflow.phase2.scoring import score_journeys
from goldenflow.phase3.parser_text import parse_repository
from goldenflow.phase4.fingerprint import (
    element_similarity,
    fingerprint,
    match_screens,
    similarity,
)
from goldenflow.phase4.healing import MIN_REPAIR_CONFIDENCE, heal, matches, propose_repair
from goldenflow.phase4.hierarchy import load_dump_directory, parse_page_source
from goldenflow.phase4.locators import (
    ElementLocators,
    Locator,
    Strategy,
    best,
    rank,
)
from goldenflow.phase4.resolver import (
    from_dumps,
    from_page_objects,
    merge,
    resolve_journeys,
    verify_against_dumps,
)

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
V1_DIR = "fixtures/ui-dumps/v8.2.0"
V2_DIR = "fixtures/ui-dumps/v9.0.0"


@pytest.fixture(scope="module")
def v1():
    return load_dump_directory(V1_DIR, app_version="8.2.0")


@pytest.fixture(scope="module")
def v2():
    return load_dump_directory(V2_DIR, app_version="9.0.0")


@pytest.fixture(scope="module")
def suite():
    return parse_repository(TAXONOMY, "fixtures/appium-suite")


def loc(strategy: Strategy, value: str, *, matches_n: int = 1, screen="cart") -> Locator:
    return Locator(strategy=strategy, value=value, screen_id=screen,
                   match_count=matches_n)


def journey(*screens: str, count: int = 10):
    clustering = cluster_variants([Variant(sequence=tuple(screens), count=count)])
    return score_journeys(clustering, taxonomy=TAXONOMY)


# ============================================================ stability model


def test_strategies_rank_in_the_documented_order() -> None:
    order = [
        Strategy.ACCESSIBILITY_ID, Strategy.RESOURCE_ID, Strategy.SEMANTIC_XPATH,
        Strategy.CLASS_CHAIN, Strategy.POSITIONAL_XPATH, Strategy.COORDINATES,
    ]
    scores = [s.stability for s in order]
    assert scores == sorted(scores, reverse=True)


def test_positional_and_coordinate_locators_are_never_usable() -> None:
    """A test that passes today and breaks next build costs more trust than the
    missing test would have."""
    assert not loc(Strategy.POSITIONAL_XPATH, "/x[1]/y[2]").usable
    assert not loc(Strategy.COORDINATES, "540,1200").usable


def test_ambiguity_beats_strategy_rank() -> None:
    """A test that taps the wrong one of three matches fails in a way that looks
    like a product bug - worse than a locator that breaks loudly."""
    ambiguous = loc(Strategy.ACCESSIBILITY_ID, "row", matches_n=3)
    unique = loc(Strategy.RESOURCE_ID, "com.x:id/row_card")
    assert ambiguous.stability < unique.stability
    assert not ambiguous.usable and unique.usable
    assert rank([ambiguous, unique])[0] is unique


def test_zero_match_locator_has_no_stability() -> None:
    assert loc(Strategy.ACCESSIBILITY_ID, "gone", matches_n=0).stability == 0.0


def test_best_returns_none_rather_than_the_least_bad_option() -> None:
    """A caller handed a positional XPath will use it; a caller handed None reports
    an unresolvable step, which is the honest outcome."""
    assert best([loc(Strategy.POSITIONAL_XPATH, "/a[1]")]) is None


def test_diagnostics_explain_why_resolution_failed() -> None:
    empty = ElementLocators("cart", "checkout")
    assert "no candidates found" in empty.diagnose()

    ambiguous = ElementLocators("cart", "row", candidates=[
        loc(Strategy.ACCESSIBILITY_ID, "row", matches_n=4)])
    assert "ambiguous" in ambiguous.diagnose()

    fragile = ElementLocators("cart", "btn", candidates=[
        loc(Strategy.POSITIONAL_XPATH, "/a[1]")])
    assert "fragile" in fragile.diagnose()


# ============================================================ hierarchy parsing


def test_android_page_source_parses(v1) -> None:
    cart = v1["cart"]
    assert cart.platform == "android"
    checkout = cart.element_by_role("cart_checkout")
    assert checkout is not None
    assert checkout.resource_id == "com.acme.shop:id/cart_checkout"
    assert checkout.text == "Checkout" and checkout.clickable


def test_ios_page_source_parses() -> None:
    """iOS has no resource-id concept, which is why iOS suites lean on
    accessibility identifiers."""
    dump = parse_page_source("""
    <XCUIElementTypeApplication name="Acme">
      <XCUIElementTypeButton name="cart_checkout" label="Checkout" visible="true"/>
    </XCUIElementTypeApplication>
    """, "cart", platform="ios")
    button = dump.elements[1]
    assert button.accessibility_id == "cart_checkout"
    assert button.label == "Checkout"
    assert button.short_type == "Button"


def test_malformed_xml_names_the_screen() -> None:
    """A bare ExpatError in a batch of 200 dumps tells you nothing."""
    with pytest.raises(ValueError, match="cart"):
        parse_page_source("<hierarchy><broken>", "cart")


def test_match_counts_are_computed_screen_wide(v1) -> None:
    payment = v1["payment_method"]
    counts = payment.match_counts()
    assert counts[(Strategy.ACCESSIBILITY_ID, "payment_option_row")] == 3
    assert counts[(Strategy.RESOURCE_ID, "com.acme.shop:id/payment_option_card")] == 1


def test_unique_roles_keep_ambiguously_labelled_elements_apart(v1) -> None:
    """Three payment rows share one content-desc. Collapsing them would make the
    resolver arbitrarily pick whichever parsed first."""
    roles = set(v1["payment_method"].unique_roles().values())
    assert {"payment_option_card", "payment_option_wallet",
            "payment_option_cod"} <= roles


# =============================================================== resolution


def test_crawl_resolves_a_screen_with_no_page_object(v1, suite) -> None:
    """payment_failure is the highest-consequence gap Phase 3 found, and the suite
    has no Page Object for it. The crawl is what makes it closeable."""
    assert not any(p.screen_id == "payment_failure" for p in suite.page_objects)
    screens = from_dumps(v1)
    assert screens["payment_failure"].is_resolved


def test_account_delete_resolves_from_crawl(v1) -> None:
    """Two production sessions, in the protected registry, no Page Object."""
    resolved = from_dumps(v1)["account_delete"]
    assert resolved.is_resolved
    assert "account_delete_acknowledge" in resolved.elements


def test_element_without_accessibility_id_falls_back_to_resource_id(v1) -> None:
    """The delete-confirm button has no content-desc. Resolution must degrade to
    resource-id, not fail and not silently take a positional XPath."""
    resolved = from_dumps(v1)["account_delete"]
    confirm = resolved.elements.get("delete_confirm")
    assert confirm is not None and confirm.is_resolved
    assert confirm.resolved.strategy is Strategy.RESOURCE_ID


def test_sources_merge_rather_than_compete(v1, suite) -> None:
    screens = merge(from_page_objects(suite, TAXONOMY), from_dumps(v1))
    assert screens["cart"].sources == {"page_object", "crawl"}


def test_roles_align_across_sources_on_unique_locators(v1, suite) -> None:
    """The Page Object calls it CONTINUE; the crawl calls it payment_continue.
    Same element, and they must fold together."""
    screens = merge(from_page_objects(suite, TAXONOMY), from_dumps(v1))
    payment = screens["payment_method"]
    assert not ("continue" in payment.elements and "payment_continue" in payment.elements)


def test_shared_ambiguous_locator_does_not_merge_distinct_elements(v1, suite) -> None:
    """Three rows share an accessibility ID. Joining on it would fold wallet and
    cash-on-delivery into card and silently generate a test that taps the wrong one.

    Asserted on the resolved locator *values* rather than role names, because role
    naming legitimately shifts depending on which source wins the merge.
    """
    screens = merge(from_page_objects(suite, TAXONOMY), from_dumps(v1))
    resolved = {
        element.resolved.value
        for element in screens["payment_method"].elements.values()
        if element.is_resolved
    }
    assert {
        "com.acme.shop:id/payment_option_card",
        "com.acme.shop:id/payment_option_wallet",
        "com.acme.shop:id/payment_option_cod",
    } <= resolved, "the three rows must stay independently addressable"


def test_unreached_screens_are_counted_as_unresolved(v1) -> None:
    """Leaving them out would report '18/18 resolved' while eight screens block
    journeys."""
    journeys = journey("cart", "help_center")
    report = resolve_journeys(journeys, from_dumps(v1))
    assert "help_center" in report.screens
    assert not report.screens["help_center"].is_resolved
    assert report.screens["help_center"].diagnostics()


def test_journey_with_one_blocked_step_is_not_executable(v1) -> None:
    """Phase 5 cannot generate a test that navigates eight screens and guesses at
    the ninth."""
    report = resolve_journeys(journey("cart", "help_center"), from_dumps(v1))
    resolution = report.journeys[0]
    assert resolution.ratio == 0.5
    assert not resolution.executable
    assert resolution.blocking == ["help_center"]


def test_fully_resolved_journey_is_executable(v1) -> None:
    report = resolve_journeys(journey("cart", "payment_method"), from_dumps(v1))
    assert report.journeys[0].executable
    assert report.step_resolution_pct == 100.0


# ==================================================== cross-source verification


def _pom_resolution(role: str, strategy: Strategy, value: str, screen="payment_method"):
    """A Page Object claim, built explicitly.

    Deliberately not derived from the fixture suite: coupling this test to the
    fixture *containing* defects means fixing the fixture breaks the test, which is
    exactly what happened the first time.
    """
    from goldenflow.phase4.resolver import SOURCE_PAGE_OBJECT

    element = ElementLocators(screen, role, candidates=[
        Locator(strategy=strategy, value=value, screen_id=screen,
                element_role=role, source=SOURCE_PAGE_OBJECT)
    ])
    from goldenflow.phase4.resolver import ScreenResolution
    resolution = ScreenResolution(screen, elements={role: element},
                                  sources={SOURCE_PAGE_OBJECT})
    return {screen: resolution}


def test_misdeclared_strategy_is_reported(v1) -> None:
    """The app's content-desc is payment_option_row; payment_option_card is the
    resource-id. Declaring it as an accessibility ID matches nothing."""
    screens = _pom_resolution("card_option", Strategy.ACCESSIBILITY_ID,
                              "payment_option_card")
    disagreements = verify_against_dumps(screens, v1)
    assert any("no such element exists" in d for d in disagreements)


def test_ambiguous_page_object_locator_is_reported(v1) -> None:
    screens = _pom_resolution("checkout_button", Strategy.ACCESSIBILITY_ID,
                              "cart_quantity_stepper", screen="cart")
    disagreements = verify_against_dumps(screens, v1)
    assert any("matches 2 elements" in d for d in disagreements)


def test_collection_shaped_roles_are_not_flagged_as_ambiguous(v1) -> None:
    """LINE_ITEM matching both cart rows is a list locator, not a defect. Flagging
    it would bury the real findings under noise."""
    screens = _pom_resolution("line_items", Strategy.ACCESSIBILITY_ID,
                              "cart_line_item", screen="cart")
    assert verify_against_dumps(screens, v1) == []


def test_verification_downgrades_an_unverifiable_locator(v1) -> None:
    screens = _pom_resolution("card_option", Strategy.ACCESSIBILITY_ID,
                              "payment_option_card")
    verify_against_dumps(screens, v1)
    card = screens["payment_method"].elements["card_option"]
    assert not card.is_resolved, "a locator matching nothing must not resolve"


def test_shipped_fixture_page_objects_agree_with_the_hierarchy(v1, suite) -> None:
    """Regression guard: the fixture POM had three misdeclared strategies and one
    ambiguous locator until verification caught them."""
    screens = merge(from_page_objects(suite, TAXONOMY))
    assert verify_against_dumps(screens, v1) == []


def test_verification_is_skipped_for_screens_without_a_dump(suite) -> None:
    screens = merge(from_page_objects(suite, TAXONOMY))
    assert verify_against_dumps(screens, {}) == []


# ============================================================== fingerprinting


def test_a_screen_matches_itself(v1) -> None:
    print_a = fingerprint(v1["cart"])
    assert similarity(print_a, print_a) == 1.0


def test_identity_survives_a_resource_id_refactor(v1, v2) -> None:
    """Every resource-id changed between builds; the fingerprint must not care."""
    score = similarity(fingerprint(v1["payment_method"]),
                       fingerprint(v2["payment_method"]))
    assert score > 0.8


def test_different_screens_do_not_match(v1) -> None:
    score = similarity(fingerprint(v1["cart"]), fingerprint(v1["account_delete"]))
    assert score < 0.3


def test_uncaptured_screens_are_not_reported_as_removed(v1, v2) -> None:
    """A crawler that did not reach a screen proves nothing about whether it still
    exists. Calling it removal would send someone to delete a live Page Object, and
    would report a catastrophic identity regression every time a crawl times out.
    """
    report = match_screens(v1, v2)
    matched = {m.screen_id for m in report.of_verdict("matched")}
    assert {"cart", "payment_method"} <= matched

    uncaptured = {m.screen_id for m in report.of_verdict("not_captured")}
    assert {"account_delete", "payment_failure"} <= uncaptured
    assert report.of_verdict("removed") == []


def test_stability_is_measured_only_over_jointly_captured_screens(v1, v2) -> None:
    report = match_screens(v1, v2)
    assert len(report.comparable_matches) == 2
    assert report.stability_pct == 100.0, (
        "two screens matched cleanly; the two uncaptured ones must not dilute it"
    )


def test_fingerprint_digest_ignores_volatile_detail(v1, v2) -> None:
    """Resource IDs are excluded deliberately - including them would make the
    fingerprint agree with itself only when nothing had changed."""
    a = fingerprint(v1["payment_method"])
    assert "payment_option_card" not in " ".join(sorted(a.accessibility_ids))
    assert a.digest == fingerprint(v1["payment_method"]).digest


def test_element_similarity_weights_semantic_signals(v1, v2) -> None:
    before = v1["payment_method"].element_by_role("payment_title")
    after = next(e for e in v2["payment_method"].elements
                 if e.accessibility_id == "payment_title")
    assert element_similarity(before, after) > 0.8


# ================================================================== healing


def test_unbroken_locator_produces_no_repair(v1, v2) -> None:
    """No news is the common case and must not generate noise."""
    survivor = Locator(Strategy.ACCESSIBILITY_ID, "cart_checkout", "cart")
    assert propose_repair(survivor, v1["cart"], v2["cart"]) is None


def test_accessibility_ids_survive_a_refactor_that_breaks_resource_ids(v1, v2) -> None:
    """The empirical case for the whole stability ranking."""
    a11y = Locator(Strategy.ACCESSIBILITY_ID, "cart_checkout", "cart")
    resource = Locator(Strategy.RESOURCE_ID, "com.acme.shop:id/cart_checkout", "cart")

    assert propose_repair(a11y, v1["cart"], v2["cart"]) is None
    repair = propose_repair(resource, v1["cart"], v2["cart"])
    assert repair is not None and repair.repairable
    assert repair.replacement.value in ("cart_checkout",
                                        "com.acme.shop:id/cart_primary_cta")


def test_repair_maps_a_renamed_resource_id(v1, v2) -> None:
    broken = Locator(Strategy.RESOURCE_ID, "com.acme.shop:id/payment_option_card",
                     "payment_method")
    repair = propose_repair(broken, v1["payment_method"], v2["payment_method"])
    assert repair.repairable
    assert repair.confidence >= MIN_REPAIR_CONFIDENCE
    assert "checkout_pay_card" in repair.replacement.value


def test_newly_ambiguous_locator_is_reported_unrepairable(v1, v2) -> None:
    broken = Locator(Strategy.ACCESSIBILITY_ID, "payment_option_row", "payment_method")
    repair = propose_repair(broken, v1["payment_method"], v2["payment_method"])
    assert repair is not None and not repair.repairable
    assert "matches" in repair.reason


def test_locator_that_never_matched_gets_no_speculative_repair(v1, v2) -> None:
    ghost = Locator(Strategy.ACCESSIBILITY_ID, "never_existed", "cart")
    repair = propose_repair(ghost, v1["cart"], v2["cart"])
    assert not repair.repairable
    assert "baseline" in repair.reason


def test_downgrades_require_review_and_never_auto_apply() -> None:
    """Silent downgrades are how a suite rots while its pass rate stays green."""
    from goldenflow.phase4.healing import Repair
    repair = Repair(
        screen_id="cart", element_role="checkout",
        broken=Locator(Strategy.ACCESSIBILITY_ID, "a", "cart"),
        replacement=Locator(Strategy.POSITIONAL_XPATH, "/a[1]", "cart"),
        confidence=0.9, reason="",
    )
    assert repair.is_downgrade and repair.requires_review


def test_upgrade_does_not_require_review() -> None:
    from goldenflow.phase4.healing import Repair
    repair = Repair(
        screen_id="cart", element_role="checkout",
        broken=Locator(Strategy.RESOURCE_ID, "com.x:id/a", "cart"),
        replacement=Locator(Strategy.ACCESSIBILITY_ID, "a", "cart"),
        confidence=0.9, reason="",
    )
    assert not repair.is_downgrade and not repair.requires_review


def test_matches_finds_elements_by_each_strategy(v1) -> None:
    cart = v1["cart"]
    assert len(matches(Locator(Strategy.ACCESSIBILITY_ID, "cart_checkout", "cart"), cart)) == 1
    assert len(matches(Locator(Strategy.ACCESSIBILITY_ID, "cart_line_item", "cart"), cart)) == 2
    assert len(matches(Locator(Strategy.RESOURCE_ID,
                               "com.acme.shop:id/cart_subtotal", "cart"), cart)) == 1


def test_heal_reports_survival_and_skips_absent_screens(v1, v2) -> None:
    locators = [
        Locator(Strategy.ACCESSIBILITY_ID, "cart_checkout", "cart"),
        Locator(Strategy.RESOURCE_ID, "com.acme.shop:id/cart_checkout", "cart"),
        Locator(Strategy.ACCESSIBILITY_ID, "x", "payment_failure"),   # no v2 dump
    ]
    report = heal(locators, v1, v2)
    assert report.checked == 2, "screens missing from the new build are skipped"
    assert 0 < report.survival_pct < 100
