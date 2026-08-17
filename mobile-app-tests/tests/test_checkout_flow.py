import allure

from src.pages.home_page import HomePage
from src.pages.categories_page import CategoriesPage
from src.pages.category_grid_page import CategoryGridPage
from src.pages.product_page import ProductPage
from src.pages.cart_page import CartPage
from src.pages.checkout_page import CheckoutPage
from src.pages.thankyou_page import ThankYouPage


@allure.title("Open app, add a product to cart, and complete guest checkout")
def test_open_app_go_to_product_and_checkout(driver, config, test_data, logger):
    wait = config["explicit_wait"]
    product = test_data["product"]
    guest = test_data["guest_checkout"]

    home = HomePage(driver, timeout=wait)
    categories = CategoriesPage(driver, timeout=wait)
    category_grid = CategoryGridPage(driver, timeout=wait)
    product_page = ProductPage(driver, timeout=wait)
    cart = CartPage(driver, timeout=wait)
    checkout = CheckoutPage(driver, timeout=wait)
    thank_you = ThankYouPage(driver, timeout=wait)

    with allure.step("Launch app and wait for home screen"):
        assert home.is_loaded(), "Home tab was not visible after app launch"

    with allure.step(f"Select '{product['category']}' category"):
        home.open_categories()
        categories.select_category(product["category"])

    with allure.step(f"Open product '{product['name']}'"):
        category_grid.open_product(product["name"])

    with allure.step("Add product to cart"):
        product_page.add_to_cart()

    with allure.step("Go to cart"):
        product_page.open_cart()
        assert cart.has_item_priced(product["price"]), "Added product was not found in the cart"

    with allure.step("Proceed to checkout"):
        cart.go_to_checkout()

    with allure.step("Fill guest billing address"):
        checkout.fill_guest_billing_address(guest)
        checkout.save_and_continue()

    with allure.step("Select shipping method"):
        checkout.select_shipping_method()

    with allure.step("Select payment method"):
        checkout.select_payment_method()

    with allure.step("Place order"):
        checkout.place_order()

    with allure.step("Assert order confirmation is shown"):
        success = thank_you.is_order_successful()
        logger.info("Order placed successfully: %s", success)
        assert success, "Thank-you page was not shown after placing the order"
