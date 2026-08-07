"""Page Objects for the checkout funnel.

Note what is absent: there is no ``PaymentFailureScreen``. Production sees that
screen on roughly 5% of checkout sessions, and the suite has no way to drive it.
Phase 3 should surface that as a high-risk coverage gap rather than as an omission
nobody notices.
"""

from appium.webdriver.common.appiumby import AppiumBy

from pages.browse_pages import BasePage


class CartScreen(BasePage):
    LINE_ITEM = (AppiumBy.ID, "com.acme.shop:id/cart_line_item")
    # Plural: there is one stepper per line item. This was QUANTITY_STEPPER
    # (singular) and tapped via find_element, so increase_quantity() silently
    # always incremented the first row. GoldenFlow's cross-source verification
    # caught it - the locator matched 2 elements while being used as if unique.
    QUANTITY_STEPPERS = "~cart_quantity_stepper"
    SUBTOTAL = "~cart_subtotal"
    CHECKOUT_BUTTON = "~cart_checkout"

    def item_count(self):
        return len(self.driver.find_elements(*self.LINE_ITEM))

    def subtotal(self):
        return self._text(self.SUBTOTAL)

    def increase_quantity(self, index=0):
        steppers = self.driver.find_elements(
            AppiumBy.ACCESSIBILITY_ID, self.QUANTITY_STEPPERS
        )
        steppers[index].click()

    def start_checkout(self):
        self._tap(self.CHECKOUT_BUTTON)


class DeliveryAddressScreen(BasePage):
    SAVED_ADDRESS_ROW = (AppiumBy.ID, "com.acme.shop:id/address_row")
    ADD_ADDRESS = "~address_add_new"
    CONTINUE = "~address_continue"

    def select_saved_address(self, index=0):
        self.driver.find_elements(*self.SAVED_ADDRESS_ROW)[index].click()

    def continue_to_payment(self):
        self._tap(self.CONTINUE)


class PaymentMethodScreen(BasePage):
    # These three were declared as accessibility IDs ("~payment_option_card") until
    # GoldenFlow's cross-source verification caught it: the app's content-desc on
    # all three rows is "payment_option_row", and payment_option_card is the
    # resource-id. The suite had been passing because the fallback happened to
    # find the right row by document order.
    CARD_OPTION = (AppiumBy.ID, "com.acme.shop:id/payment_option_card")
    WALLET_OPTION = (AppiumBy.ID, "com.acme.shop:id/payment_option_wallet")
    COD_OPTION = (AppiumBy.ID, "com.acme.shop:id/payment_option_cod")
    CONTINUE = "~payment_continue"

    def choose_card(self):
        self._tap(self.CARD_OPTION)

    def choose_wallet(self):
        self._tap(self.WALLET_OPTION)

    def continue_to_review(self):
        self._tap(self.CONTINUE)


class OrderReviewScreen(BasePage):
    ORDER_TOTAL = "~review_order_total"
    PLACE_ORDER = "~review_place_order"

    def order_total(self):
        return self._text(self.ORDER_TOTAL)

    def place_order(self):
        self._tap(self.PLACE_ORDER)


class OrderConfirmationScreen(BasePage):
    ORDER_ID = "~confirmation_order_id"
    SUCCESS_BANNER = "~confirmation_success_banner"
    CONTINUE_SHOPPING = "~confirmation_continue_shopping"

    def order_id(self):
        return self._text(self.ORDER_ID)

    def is_successful(self):
        return self._text(self.SUCCESS_BANNER) is not None
