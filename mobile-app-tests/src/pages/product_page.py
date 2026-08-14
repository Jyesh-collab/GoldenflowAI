"""Product Detail Page Object.

Confirmed against a real device dump: "Add to Cart" has a plain, un-merged
content-desc, so an exact accessibility id works. The product detail page
has no bottom tab bar — cart is reached via the app bar's cart icon, whose
content-desc is unreliable: observed as both "{count}\\nCart" and a bare
"{count}" with no "Cart" text at all across otherwise-identical states, and
"Cart" alone would ambiguously match "Add to Cart" too. Its on-screen
position is fixed app-bar chrome, so it's tapped by coordinate instead.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class ProductPage(BasePage):
    ADD_TO_CART_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Add to Cart")
    ADDED_TO_CART_SNACKBAR = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("added to cart")')

    def add_to_cart(self):
        """Retries the tap if the confirmation snackbar doesn't show up.
        Confirmed live: a tap on "Add to Cart" can be silently dropped by
        the app (same class of issue as cart's "Pay Now" — see
        click_until_visible's docstring), leaving the cart empty with no
        error raised anywhere in the chain."""
        self.click_until_visible(self.ADD_TO_CART_BUTTON, self.ADDED_TO_CART_SNACKBAR, confirm_timeout=12)

    def open_cart(self):
        self.tap_relative(0.903, 0.088)
