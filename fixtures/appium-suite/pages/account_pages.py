"""Page Objects for auth and account.

``AccountDeleteScreen``, ``DataExportScreen`` and ``RefundRequestScreen`` are
deliberately missing. All three are in the protected test registry, all three carry
regulatory or financial exposure, and none of them can currently be automated
because no Page Object exists to drive them.
"""

from appium.webdriver.common.appiumby import AppiumBy

from pages.browse_pages import BasePage


class LoginScreen(BasePage):
    EMAIL_FIELD = "~login_email"
    PASSWORD_FIELD = "~login_password"
    SUBMIT = "~login_submit"
    FORGOT_LINK = "~login_forgot_password"

    def sign_in(self, email, password):
        self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, self.EMAIL_FIELD).send_keys(email)
        self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, self.PASSWORD_FIELD).send_keys(password)
        self._tap(self.SUBMIT)


class OtpVerifyScreen(BasePage):
    CODE_FIELD = "~otp_code_field"
    VERIFY = "~otp_verify"
    RESEND = "~otp_resend"

    def enter_code(self, code):
        self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, self.CODE_FIELD).send_keys(code)
        self._tap(self.VERIFY)


class AccountHomeScreen(BasePage):
    ORDER_HISTORY_ROW = "~account_order_history"
    SAVED_CARDS_ROW = "~account_saved_cards"
    ADDRESS_BOOK_ROW = "~account_address_book"
    PRIVACY_ROW = "~account_privacy"

    def open_order_history(self):
        self._tap(self.ORDER_HISTORY_ROW)

    def open_saved_cards(self):
        self._tap(self.SAVED_CARDS_ROW)


class OrderHistoryScreen(BasePage):
    ORDER_ROW = (AppiumBy.ID, "com.acme.shop:id/order_row")
    EMPTY_STATE = "~order_history_empty"

    def order_count(self):
        return len(self.driver.find_elements(*self.ORDER_ROW))

    def open_order(self, index=0):
        self.driver.find_elements(*self.ORDER_ROW)[index].click()


class SavedCardsScreen(BasePage):
    CARD_ROW = (AppiumBy.ID, "com.acme.shop:id/saved_card_row")
    ADD_CARD = "~saved_cards_add"

    def card_count(self):
        return len(self.driver.find_elements(*self.CARD_ROW))
