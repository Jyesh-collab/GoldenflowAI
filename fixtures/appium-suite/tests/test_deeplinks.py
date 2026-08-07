"""Deep-link tests written against an older navigation model.

These walk transitions that production no longer exhibits - the app now routes
through intermediate screens that these tests skip. Phase 3 should surface them as
obsolete *candidates*, never as automatic deletions: a path can be absent from a
sample because it is genuinely dead, or because it is seasonal, flag-gated, or
simply rare.
"""

import pytest

from pages.account_pages import AccountHomeScreen, SavedCardsScreen
from pages.browse_pages import HomeScreen, SplashScreen
from pages.checkout_pages import OrderConfirmationScreen, PaymentMethodScreen


@pytest.mark.deeplink
def test_deeplink_straight_to_order_confirmation(driver):
    """splash -> order_confirmation. No production session does this any more."""
    SplashScreen(driver).continue_to_home()
    confirmation = OrderConfirmationScreen(driver)
    assert confirmation.order_id() is not None


@pytest.mark.deeplink
def test_deeplink_straight_to_payment(driver):
    """home -> payment_method, skipping cart and address selection."""
    HomeScreen(driver).open_search()
    payment = PaymentMethodScreen(driver)
    payment.choose_card()


@pytest.mark.deeplink
def test_deeplink_saved_cards_from_home(driver):
    """home -> saved_cards. Production routes via account_home."""
    HomeScreen(driver).open_account()
    cards = SavedCardsScreen(driver)
    assert cards.card_count() >= 0
