"""Home screen Page Object.

Locators use accessibility id (Flutter's Semantics `label`, exposed to
UiAutomator2 as content-desc) since the app has no native resource-ids.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class HomePage(BasePage):
    HOME_TAB = (AppiumBy.ACCESSIBILITY_ID, "Home")
    CATEGORIES_TAB = (AppiumBy.ACCESSIBILITY_ID, "Categories")
    CART_TAB = (AppiumBy.ACCESSIBILITY_ID, "Cart")
    ACCOUNT_TAB = (AppiumBy.ACCESSIBILITY_ID, "Account")

    def is_loaded(self) -> bool:
        return self.is_displayed(self.HOME_TAB)
