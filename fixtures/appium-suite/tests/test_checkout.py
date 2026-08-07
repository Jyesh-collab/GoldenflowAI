"""Checkout regression tests."""

import pytest

from pages.browse_pages import HomeScreen, ProductDetailScreen, SearchScreen, SplashScreen
from pages.checkout_pages import (
    CartScreen,
    DeliveryAddressScreen,
    OrderConfirmationScreen,
    OrderReviewScreen,
    PaymentMethodScreen,
)


@pytest.mark.smoke
@pytest.mark.checkout
def test_search_to_purchase_completes(driver):
    """The happy path: search, add to cart, pay, confirm."""
    splash = SplashScreen(driver)
    splash.continue_to_home()

    home = HomeScreen(driver)
    home.open_search()

    search = SearchScreen(driver)
    search.search_for("running shoes")
    assert search.result_count() > 0
    search.open_result()

    pdp = ProductDetailScreen(driver)
    assert pdp.price() is not None
    pdp.add_to_cart()

    cart = CartScreen(driver)
    assert cart.item_count() == 1
    cart.start_checkout()

    address = DeliveryAddressScreen(driver)
    address.select_saved_address()
    address.continue_to_payment()

    payment = PaymentMethodScreen(driver)
    payment.choose_card()
    payment.continue_to_review()

    review = OrderReviewScreen(driver)
    assert review.order_total() is not None
    review.place_order()

    confirmation = OrderConfirmationScreen(driver)
    assert confirmation.is_successful()
    assert confirmation.order_id() is not None


@pytest.mark.checkout
def test_cart_quantity_update_recalculates_subtotal(driver):
    home = HomeScreen(driver)
    home.open_search()

    pdp = ProductDetailScreen(driver)
    pdp.add_to_cart()

    cart = CartScreen(driver)
    before = cart.subtotal()
    cart.increase_quantity()
    assert cart.subtotal() != before


@pytest.mark.checkout
def test_wallet_payment_reaches_review(driver):
    """Covers the wallet route but asserts nothing about the outcome.

    Phase 3 should flag this as an assertion gap: the path is walked, so it counts
    as coverage, but a regression in payment selection would not fail this test.
    """
    cart = CartScreen(driver)
    cart.start_checkout()

    address = DeliveryAddressScreen(driver)
    address.continue_to_payment()

    payment = PaymentMethodScreen(driver)
    payment.choose_wallet()
    payment.continue_to_review()

    OrderReviewScreen(driver)
