"""Page Objects for the browse funnel.

Fixture for exercising GoldenFlow's Phase 3 parsers. Written the way a real Appium
suite is written, so the parser is tested against realistic structure rather than
something shaped to make it pass.
"""

from appium.webdriver.common.appiumby import AppiumBy


class BasePage:
    def __init__(self, driver):
        self.driver = driver

    def _tap(self, locator):
        self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, locator).click()

    def _text(self, locator):
        return self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, locator).text


class SplashScreen(BasePage):
    LOGO = "~splash_logo"
    GET_STARTED = "~splash_get_started"

    def wait_for_load(self):
        return self._text(self.LOGO)

    def continue_to_home(self):
        self._tap(self.GET_STARTED)


class HomeScreen(BasePage):
    BANNER_CAROUSEL = "~home_banner_carousel"
    SEARCH_ENTRY = "~home_search_entry"
    CATEGORY_TILE = (AppiumBy.ID, "com.acme.shop:id/category_tile")
    ACCOUNT_TAB = "~home_tab_account"

    def open_search(self):
        self._tap(self.SEARCH_ENTRY)

    def open_category(self, index=0):
        self.driver.find_elements(*self.CATEGORY_TILE)[index].click()

    def open_account(self):
        self._tap(self.ACCOUNT_TAB)

    def banner_is_visible(self):
        return self._text(self.BANNER_CAROUSEL) is not None


class SearchScreen(BasePage):
    QUERY_FIELD = "~search_query_field"
    SUBMIT = "~search_submit"
    RESULT_ROW = (AppiumBy.ID, "com.acme.shop:id/search_result_row")

    def search_for(self, term):
        self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, self.QUERY_FIELD).send_keys(term)
        self._tap(self.SUBMIT)

    def open_result(self, index=0):
        self.driver.find_elements(*self.RESULT_ROW)[index].click()

    def result_count(self):
        return len(self.driver.find_elements(*self.RESULT_ROW))


class CategoryListScreen(BasePage):
    CATEGORY_ROW = (AppiumBy.ID, "com.acme.shop:id/category_row")

    def open_category(self, index=0):
        self.driver.find_elements(*self.CATEGORY_ROW)[index].click()


class ProductListScreen(BasePage):
    FILTER_BUTTON = "~plp_filter"
    PRODUCT_CARD = (AppiumBy.ID, "com.acme.shop:id/product_card")

    def apply_filter(self):
        self._tap(self.FILTER_BUTTON)

    def open_product(self, index=0):
        self.driver.find_elements(*self.PRODUCT_CARD)[index].click()


class ProductDetailScreen(BasePage):
    TITLE = "~pdp_title"
    PRICE = "~pdp_price"
    VARIANT_PICKER = "~pdp_variant_picker"
    ADD_TO_CART = "~pdp_add_to_cart"

    def title(self):
        return self._text(self.TITLE)

    def price(self):
        return self._text(self.PRICE)

    def select_variant(self):
        self._tap(self.VARIANT_PICKER)

    def add_to_cart(self):
        self._tap(self.ADD_TO_CART)
