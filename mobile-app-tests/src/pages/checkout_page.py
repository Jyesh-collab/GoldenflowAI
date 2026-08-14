"""Checkout Page Object (guest checkout flow).

Confirmed against a real device dump:
  - Billing form fields (First Name, Last Name, Email, Phone, Street
    Address, City, Postcode) are native EditTexts whose label surfaces as
    the `hint` attribute, not content-desc — located by hint (see
    BasePage._find_by_hint; not XPath, which was intermittently unreliable
    for this).
  - Country/State are non-EditText fields with the same hint-only pattern.
    Tapping either opens a searchable bottom sheet (hint="Search..."), so
    that tap is retried until the search field shows up. Typing a value
    then filters the list down to a single, exact-match content-desc item,
    avoiding any need to scroll a long list.
  - "Save & Continue" / "Place Order" have plain, un-merged content-desc.
  - Shipping/payment method rows have Flutter-merged content-desc (e.g.
    "$10.00\\nFlat Rate", "Money Transfer\\nMoney Transfer"), so they're
    matched with `click_containing`.
  - This store's active payment methods are online gateways (Stripe,
    Razorpay, PayU, PhonePe, PayPal Smart Button/Standard) plus
    "Money Transfer" — there is no Cash On Delivery. Money Transfer is the
    only one that completes without leaving the app for an external
    redirect/SDK, so it's the default here.
  - Guest checkout defaults `useSameAddressForShipping` to True (see
    CheckoutState in checkout_bloc.dart), so filling the billing form once
    and tapping "Save & Continue" is enough to reach shipping/payment
    selection — no separate shipping-address form is required.
  - The first shipping method is pre-selected automatically, but selecting
    one explicitly keeps the test deterministic.
"""

from appium.webdriver.common.appiumby import AppiumBy

from .base_page import BasePage


class CheckoutPage(BasePage):
    SAVE_CONTINUE_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Save & Continue")
    PLACE_ORDER_BUTTON = (AppiumBy.ACCESSIBILITY_ID, "Place Order")
    ADDRESS_CHANGE_LINK = (AppiumBy.ACCESSIBILITY_ID, "Change")
    ORDER_SUCCESS_TITLE = (AppiumBy.ACCESSIBILITY_ID, "Thank you for your order!")

    def fill_guest_billing_address(self, address: dict):
        self.type_by_hint("First Name", address["first_name"])
        self.type_by_hint("Last Name", address["last_name"])
        self.type_by_hint("Email", address["email"])
        self.type_by_hint("Phone", address["phone"])
        self.type_by_hint("Street Address", address["street_address"])

        self.click_by_hint("Country", confirm_hint="Search...")
        self._search_and_select(address["country"])

        self.click_by_hint("State", confirm_hint="Search...")
        self._search_and_select(address["state"])

        self.type_by_hint("City", address["city"])
        self.type_by_hint("Postcode", address["postcode"])

    def _search_and_select(self, value: str, attempts: int = 3):
        """Retries typing into the search field until [value] actually
        shows up filtered into the list — same silently-dropped-input risk
        as elsewhere in this app (see click_until_visible's docstring), but
        for send_keys rather than a tap: confirmed live that typing into
        "Search..." can silently not register (list stays unfiltered,
        showing the full A-Z country list), which then makes the result
        item impossible to find — with ~195 countries, an unfiltered list
        only renders the first handful in the virtualized ListView, so the
        target is neither visible nor attached, not just off, without
        scrolling further than intended here."""
        result_locator = (AppiumBy.ACCESSIBILITY_ID, value)
        for _ in range(attempts):
            self.type_by_hint("Search...", value)
            if self.is_displayed(result_locator, timeout=5):
                break
        self.click(result_locator)

    def save_and_continue(self):
        """Retries if the address-confirmed view (with its "Change" link)
        doesn't show up — same silently-dropped-tap risk as elsewhere in
        this app (see click_until_visible's docstring)."""
        self.click_until_visible(self.SAVE_CONTINUE_BUTTON, self.ADDRESS_CHANGE_LINK)

    def select_shipping_method(self, contains_text: str = "Flat Rate"):
        self.click_containing(contains_text)

    def select_payment_method(self, contains_text: str = "Money Transfer"):
        self.click_containing(contains_text)

    def place_order(self):
        """Retries if the thank-you page doesn't show up — same
        silently-dropped-tap risk as elsewhere in this app (see
        click_until_visible's docstring). A dropped tap here is safe to
        retry: if the first tap didn't register, no order was placed, so
        there's nothing to double-submit."""
        self.click_until_visible(self.PLACE_ORDER_BUTTON, self.ORDER_SUCCESS_TITLE, confirm_timeout=30)
