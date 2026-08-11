"""Common Page Object helpers: explicit waits only, no static sleeps."""

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BasePage:
    def __init__(self, driver, timeout: int = 20):
        self.driver = driver
        self.timeout = timeout

    def find(self, locator):
        return WebDriverWait(self.driver, self.timeout).until(
            EC.presence_of_element_located(locator)
        )

    def click(self, locator):
        element = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable(locator)
        )
        element.click()

    def is_displayed(self, locator, timeout: int | None = None) -> bool:
        try:
            WebDriverWait(self.driver, timeout or self.timeout).until(
                EC.presence_of_element_located(locator)
            )
            return True
        except Exception:
            return False

    def get_text(self, locator) -> str:
        return self.find(locator).text
