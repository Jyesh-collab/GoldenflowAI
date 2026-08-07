"""Protected test registry tests.

These guard the failure mode most likely to cause real harm: Phase 5 deleting
low-traffic, high-consequence tests because usage frequency looked like a
reasonable proxy for importance.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from goldenflow.phase0.registry import (
    ProtectedTest,
    ProtectedTestRegistry,
    ProtectionCategory,
)

CONFIG = "config/protected-tests.yaml"

GOOD_REASON = (
    "Low production traffic by design; a silent failure carries regulatory "
    "consequence rather than user-visible breakage."
)


def entry(pattern: str, **overrides) -> ProtectedTest:
    defaults = dict(
        pattern=pattern,
        category=ProtectionCategory.REGULATORY,
        reason=GOOD_REASON,
        owner="platform-qe",
        added_on=date(2026, 8, 3),
        review_by=date(2027, 8, 3),
    )
    defaults.update(overrides)
    return ProtectedTest(**defaults)


def registry(*entries: ProtectedTest) -> ProtectedTestRegistry:
    return ProtectedTestRegistry(app_id="test-app", entries=list(entries))


# ------------------------------------------------------------ reason discipline


@pytest.mark.parametrize("weak", ["important", "do not delete", "critical!", ""])
def test_weak_justifications_are_rejected(weak: str) -> None:
    """A registry that is cheap to append to becomes a dumping ground."""
    with pytest.raises(ValidationError):
        entry("**/x/**", reason=weak)


def test_substantive_justification_is_accepted() -> None:
    assert entry("**/x/**").reason.startswith("Low production traffic")


# ------------------------------------------------------------------- matching


@pytest.mark.parametrize("test_id,expected", [
    ("tests/account/delete/test_erasure.py", True),
    ("tests/account/delete/nested/test_confirm.py", True),
    ("tests/account/profile/test_edit.py", False),
])
def test_glob_matching(test_id: str, expected: bool) -> None:
    assert registry(entry("**/account/delete/**")).is_protected(test_id) is expected


@pytest.mark.parametrize("test_id,expected", [
    ("suite/test_duplicate_charge_guard.py", True),
    ("suite/test_idempotency_key.py", True),
    ("suite/test_add_to_cart.py", False),
])
def test_regex_matching(test_id: str, expected: bool) -> None:
    reg = registry(entry("re:.*(duplicate_charge|idempotency).*"))
    assert reg.is_protected(test_id) is expected


def test_first_matching_entry_wins_and_is_returned() -> None:
    reg = registry(
        entry("**/a11y/**", category=ProtectionCategory.ACCESSIBILITY),
        entry("**/*.py", category=ProtectionCategory.REGULATORY),
    )
    match = reg.match("tests/a11y/test_talkback.py")
    assert match is not None
    assert match.category is ProtectionCategory.ACCESSIBILITY


# ------------------------------------------------------------ deletion refusal


def test_assert_deletable_raises_for_protected_tests() -> None:
    """Phase 5's deletion path calls this. It raises rather than returning a
    boolean so that omitting the check is loud instead of silent."""
    reg = registry(entry("**/account/delete/**"))
    with pytest.raises(PermissionError) as exc:
        reg.assert_deletable("tests/account/delete/test_erasure.py")
    assert "Refusing to propose deletion" in str(exc.value)
    assert "platform-qe" in str(exc.value)


def test_assert_deletable_is_silent_for_unprotected_tests() -> None:
    registry(entry("**/account/delete/**")).assert_deletable("tests/browse/test_plp.py")


def test_partition_separates_candidates() -> None:
    reg = registry(entry("**/account/delete/**"))
    deletable, protected = reg.partition([
        "tests/browse/test_plp.py",
        "tests/account/delete/test_erasure.py",
        "tests/browse/test_search.py",
    ])
    assert deletable == ["tests/browse/test_plp.py", "tests/browse/test_search.py"]
    assert protected == ["tests/account/delete/test_erasure.py"]


def test_decision_explains_itself() -> None:
    reg = registry(entry("**/refund/**", category=ProtectionCategory.FINANCIAL))
    decision = reg.decide("tests/support/refund/test_request.py")
    assert decision.protected
    assert "PROTECTED under financial" in decision.explanation
    assert GOOD_REASON[:30] in decision.explanation

    clear = reg.decide("tests/browse/test_home.py")
    assert not clear.protected
    assert "deletion may be proposed" in clear.explanation


# ----------------------------------------------------------------- expiry


def test_stale_entries_are_reported() -> None:
    """Protections expire so the registry cannot silently ossify."""
    today = date(2027, 1, 1)
    reg = registry(
        entry("**/fresh/**", review_by=today + timedelta(days=30)),
        entry("**/stale/**", review_by=today - timedelta(days=1)),
    )
    stale = reg.stale_entries(today)
    assert [e.pattern for e in stale] == ["**/stale/**"]


def test_summary_counts_by_category() -> None:
    reg = registry(
        entry("**/a/**", category=ProtectionCategory.FINANCIAL),
        entry("**/b/**", category=ProtectionCategory.FINANCIAL),
        entry("**/c/**", category=ProtectionCategory.SECURITY),
    )
    assert reg.summary()["by_category"] == {"financial": 2, "security": 1}


# --------------------------------------------------------- the shipped registry


def test_shipped_registry_loads_and_is_populated() -> None:
    reg = ProtectedTestRegistry.from_yaml(CONFIG)
    assert len(reg.entries) >= 10


def test_shipped_registry_has_no_stale_entries_at_capture_time() -> None:
    reg = ProtectedTestRegistry.from_yaml(CONFIG)
    assert reg.stale_entries(date(2026, 8, 3)) == []


def test_shipped_registry_covers_the_categories_that_motivated_it() -> None:
    reg = ProtectedTestRegistry.from_yaml(CONFIG)
    covered = set(reg.coverage_by_category())
    assert {"data_rights", "financial", "accessibility", "security"} <= covered


@pytest.mark.parametrize("test_id", [
    "tests/account/delete/test_right_to_erasure.py",
    "tests/account/data_export/test_portability.py",
    "tests/checkout/payment_failure/test_retry.py",
    "tests/support/refund/test_initiate.py",
    "tests/a11y/test_large_text.py",
    "tests/auth/otp/test_verify.py",
    "e2e/test_talkback_checkout.py",
    "e2e/test_offline_mode_cart.py",
    "e2e/test_marketing_optout.py",
])
def test_canonical_low_traffic_high_consequence_tests_are_protected(test_id) -> None:
    """The concrete list of things a traffic-ranked pruner would delete first."""
    reg = ProtectedTestRegistry.from_yaml(CONFIG)
    assert reg.is_protected(test_id), f"{test_id} is unprotected"


@pytest.mark.parametrize("test_id", [
    "tests/browse/test_home_feed.py",
    "tests/browse/test_product_list_filter.py",
    "tests/browse/test_search_suggestions.py",
])
def test_ordinary_high_traffic_tests_remain_prunable(test_id) -> None:
    """Over-protection is its own failure - it would neuter Phase 5 entirely."""
    reg = ProtectedTestRegistry.from_yaml(CONFIG)
    assert not reg.is_protected(test_id)
