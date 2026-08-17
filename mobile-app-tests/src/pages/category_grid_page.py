"""Category product grid Page Object (CategoryProductsGridPage).

Confirmed against a real device dump: each product card's content-desc is
Flutter-merged with its own price/rating/review-count on separate lines
(e.g. "{name}\\n{price}\\n{rating}\\n{reviews}"), so it must be matched with
a "contains" lookup rather than an exact accessibility id.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class CategoryGridPage(BasePage):
    ADD_TO_CART_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Add to Cart")

    def open_product(self, product_name: str):
        """Retries if the product detail page's "Add to Cart" button
        doesn't show up — same silently-dropped-tap risk as elsewhere in
        this app (see BasePage.click_until_visible's docstring)."""
        locator = self.by_description_contains(product_name)
        self.scroll_into_view(locator)
        self.click_until_visible(locator, self.ADD_TO_CART_BUTTON)
