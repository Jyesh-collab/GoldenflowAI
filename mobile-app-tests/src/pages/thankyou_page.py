"""Thank You (order confirmation) Page Object.

No Semantics/key on this page — confirmed by source inspection of
lib/features/checkout/presentation/pages/thankyou_page.dart. Order success
is verified via the static confirmation text.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class ThankYouPage(BasePage):
    ORDER_SUCCESS_TITLE = (AppiumBy.ACCESSIBILITY_ID, "Thank you for your order!")

    def is_order_successful(self, timeout: int | None = None) -> bool:
        return self.is_displayed(self.ORDER_SUCCESS_TITLE, timeout=timeout)
