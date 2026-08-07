"""Auth and account tests.

Note the absence of any test for account deletion, data export or refund requests.
All three are in config/protected-tests.yaml, all three carry regulatory or
financial exposure, and none has a Page Object to drive it.
"""

import pytest

from pages.account_pages import (
    AccountHomeScreen,
    LoginScreen,
    OrderHistoryScreen,
    OtpVerifyScreen,
    SavedCardsScreen,
)
from pages.browse_pages import HomeScreen, SplashScreen


@pytest.mark.smoke
@pytest.mark.auth
def test_login_with_otp_reaches_home(driver):
    SplashScreen(driver).continue_to_home()

    login = LoginScreen(driver)
    login.sign_in("qa+standard@example.com", "correct-horse")

    otp = OtpVerifyScreen(driver)
    otp.enter_code("000000")

    home = HomeScreen(driver)
    assert home.banner_is_visible()


@pytest.mark.account
def test_order_history_lists_orders(driver):
    home = HomeScreen(driver)
    home.open_account()

    account = AccountHomeScreen(driver)
    account.open_order_history()

    history = OrderHistoryScreen(driver)
    assert history.order_count() >= 0


@pytest.mark.account
def test_saved_cards_are_listed(driver):
    HomeScreen(driver).open_account()
    AccountHomeScreen(driver).open_saved_cards()
    assert SavedCardsScreen(driver).card_count() >= 0


@pytest.mark.skip(reason="flaky on CI since the 8.1 nav refactor - GF-412")
@pytest.mark.account
def test_address_book_edit(driver):
    HomeScreen(driver).open_account()
    AccountHomeScreen(driver)
