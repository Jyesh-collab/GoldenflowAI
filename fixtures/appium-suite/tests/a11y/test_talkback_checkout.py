"""Accessibility checkout tests, driven with TalkBack enabled.

These walk transitions that barely register in production analytics - not because
nobody uses them, but because assistive-technology users are under-represented in
instrumentation in the first place. The traffic signal is biased against them
twice over.

Phase 3 should therefore classify these as *protected and retained* rather than as
obsolete candidates. That is the whole point of config/protected-tests.yaml, and
this file exists so the guardrail is exercised rather than assumed.
"""

import pytest

from pages.account_pages import AccountHomeScreen
from pages.checkout_pages import OrderConfirmationScreen


@pytest.mark.a11y
def test_talkback_reaches_confirmation_from_account(driver):
    """account_home -> order_confirmation via the accessibility shortcut.

    Sighted users never take this route, so production shows no such transition.
    """
    account = AccountHomeScreen(driver)
    account.open_order_history()

    confirmation = OrderConfirmationScreen(driver)
    assert confirmation.is_successful()
