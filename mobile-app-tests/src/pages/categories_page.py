"""Categories tab Page Object (category_page.dart's chip row).

Confirmed against a real device dump: chip content-desc values are plain,
un-merged category names (e.g. "Household"), unlike the Home screen's
category carousel, whose items merge an accessibility label with the
category name and don't reliably include every category. Tapping a chip
here navigates straight into that category's product grid
(CategoryProductsGridPage).
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class CategoriesPage(BasePage):
    ITEMS_FOUND_HEADER = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Items Found")')

    def select_category(self, category_name: str):
        """Retries if the product grid's "N Items Found" header doesn't
        show up — same silently-dropped-tap risk as elsewhere in this app
        (see BasePage.click_until_visible's docstring)."""
        locator = (AppiumBy.ACCESSIBILITY_ID, category_name)
        self.scroll_into_view(locator)
        self.click_until_visible(locator, self.ITEMS_FOUND_HEADER)
