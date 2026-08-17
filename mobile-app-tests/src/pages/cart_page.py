"""Cart Page Object.

"Pay Now" has a plain, un-merged content-desc. The cart item's product name
Text has NO accessibility exposure at all (confirmed against a real device
dump: no content-desc, no text attribute) — the unit price is the only
per-item value actually exposed, so that's what's used to confirm an item
was added.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class CartPage(BasePage):
    PAY_NOW_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Pay Now")

    def has_item_priced(self, price_text: str, timeout: int | None = None) -> bool:
        return self.is_displayed((AppiumBy.ACCESSIBILITY_ID, price_text), timeout=timeout)

    def go_to_checkout(self):
        """Confirms against the First Name field itself (by hint), not the
        nearby "Billing Address" label. Observed live: the static
        "Billing Address" header can be present in the accessibility tree
        before the actual interactive form (with real `hint`-bearing
        EditTexts) has finished mounting — this app uses shimmer/skeleton
        loading states elsewhere too. Confirming on the exact field the
        next step needs closes that gap instead of just moving it."""
        self.click_until_visible(
            self.PAY_NOW_BUTTON, lambda timeout: self.is_hint_displayed("First Name", timeout=timeout)
        )
