"""Suite parser tests.

The behaviours pinned hardest are the ones that silently corrupt coverage: source
ordering, comment stripping, and the rule that a LOW-confidence guess can never make
a path look tested.
"""

from __future__ import annotations

import pytest

from goldenflow.phase0.taxonomy import Taxonomy
from goldenflow.phase3.models import Confidence, Language, ParsedTest, ScreenReference
from goldenflow.phase3.parser_python import PythonSuiteParser
from goldenflow.phase3.parser_text import TextSuiteParser, parse_repository, strip_comments

TAXONOMY = Taxonomy.from_yaml("config/taxonomy.yaml")
SUITE_ROOT = "fixtures/appium-suite"


@pytest.fixture(scope="module")
def suite():
    return parse_repository(TAXONOMY, SUITE_ROOT)


def python_parser(tmp_path):
    return PythonSuiteParser(TAXONOMY, tmp_path)


def write(tmp_path, name: str, source: str):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


# ================================================================= confidence


def test_low_confidence_never_counts_as_coverage() -> None:
    """A guess from a test name must not be able to make a path look tested."""
    test = ParsedTest(test_id="t", name="test_cart", file_path="f",
                      language=Language.PYTHON)
    test.screens.append(ScreenReference("cart", Confidence.LOW, "name guess", 1, 1))
    assert test.sequence == ()
    assert test.covered_screens == set()
    assert test.uncertain_screens


def test_test_confidence_is_its_weakest_link() -> None:
    test = ParsedTest(test_id="t", name="t", file_path="f", language=Language.PYTHON)
    test.screens.append(ScreenReference("home", Confidence.HIGH, "e", 1, 1))
    test.screens.append(ScreenReference("cart", Confidence.MEDIUM, "e", 2, 2))
    assert test.confidence is Confidence.MEDIUM


# ==================================================================== ordering


def test_sequence_follows_source_order_not_discovery_order(tmp_path) -> None:
    """ast.walk is breadth-first. Ordering by it produces sequences that read
    backwards, which yields wrong transitions and therefore wrong coverage."""
    path = write(tmp_path, "test_order.py", """
from pages import SplashScreen, LoginScreen, OtpVerifyScreen, HomeScreen

def test_login(driver):
    SplashScreen(driver).continue_to_home()
    login = LoginScreen(driver)
    login.sign_in("a", "b")
    otp = OtpVerifyScreen(driver)
    otp.enter_code("000000")
    home = HomeScreen(driver)
    assert home.banner_is_visible()
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert test.sequence == ("splash", "login", "otp_verify", "home")


def test_consecutive_duplicate_screens_collapse(tmp_path) -> None:
    path = write(tmp_path, "test_dup.py", """
from pages import CartScreen

def test_cart(driver):
    cart = CartScreen(driver)
    cart.item_count()
    cart.start_checkout()
""")
    assert python_parser(tmp_path).parse_file(path).tests[0].sequence == ("cart",)


# ============================================================== python parser


def test_page_object_instantiation_is_high_confidence(tmp_path) -> None:
    path = write(tmp_path, "test_hi.py", """
from pages import CartScreen

def test_cart(driver):
    CartScreen(driver).start_checkout()
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert test.sequence == ("cart",)
    assert test.screens[0].confidence is Confidence.HIGH
    assert "CartScreen" in test.page_objects


def test_navigation_helper_is_medium_confidence(tmp_path) -> None:
    path = write(tmp_path, "test_nav.py", """
def test_flow(driver, app):
    app.navigate_to("cart")
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert test.sequence == ("cart",)
    assert test.screens[0].confidence is Confidence.MEDIUM


def test_analytics_tag_literal_resolves_to_its_screen(tmp_path) -> None:
    path = write(tmp_path, "test_tag.py", """
def test_flow(driver, app):
    app.navigate_to("PDP")
""")
    assert python_parser(tmp_path).parse_file(path).tests[0].sequence == ("product_detail",)


def test_unresolvable_test_falls_back_to_a_low_confidence_name_guess(tmp_path) -> None:
    path = write(tmp_path, "test_guess.py", """
def test_cart_something(driver):
    driver.tap(100, 200)
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert test.sequence == ()          # excluded from coverage
    assert test.uncertain_screens[0].screen_id == "cart"


def test_assertions_attach_to_the_screen_in_scope(tmp_path) -> None:
    path = write(tmp_path, "test_assert.py", """
from pages import CartScreen, OrderReviewScreen

def test_flow(driver):
    cart = CartScreen(driver)
    assert cart.item_count() == 1
    review = OrderReviewScreen(driver)
    assert review.order_total() is not None
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert {a.screen_id for a in test.assertions} == {"cart", "order_review"}


def test_unittest_assertions_are_recognised(tmp_path) -> None:
    path = write(tmp_path, "test_ut.py", """
import unittest
from pages import CartScreen

class CartTests(unittest.TestCase):
    def test_count(self):
        cart = CartScreen(self.driver)
        self.assertEqual(cart.item_count(), 1)
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert test.assertions and test.assertions[0].kind == "assertEqual"
    assert test.test_id.endswith("CartTests::test_count")


def test_pytest_marks_become_tags_and_skip_is_detected(tmp_path) -> None:
    path = write(tmp_path, "test_marks.py", """
import pytest
from pages import CartScreen

@pytest.mark.smoke
@pytest.mark.skip(reason="flaky")
def test_thing(driver):
    CartScreen(driver)
""")
    test = python_parser(tmp_path).parse_file(path).tests[0]
    assert "smoke" in test.tags and test.skipped


def test_syntax_error_is_reported_not_raised(tmp_path) -> None:
    """A broken file must lower the parse rate, not abort the run."""
    path = write(tmp_path, "test_broken.py", "def test_x(:\n    pass\n")
    result = python_parser(tmp_path).parse_file(path)
    assert result.tests == []
    assert result.issues and "syntax error" in result.issues[0].message
    assert result.parse_rate == 0.0


def test_page_objects_yield_a_locator_inventory(tmp_path) -> None:
    path = write(tmp_path, "cart_page.py", """
from appium.webdriver.common.appiumby import AppiumBy

class CartScreen:
    CHECKOUT = "~cart_checkout"
    LINE_ITEM = (AppiumBy.ID, "com.acme.shop:id/cart_line_item")

    def start_checkout(self):
        pass
""")
    page_object = python_parser(tmp_path).parse_file(path).page_objects[0]
    assert page_object.screen_id == "cart"
    assert page_object.locators["CHECKOUT"] == "~cart_checkout"
    assert page_object.locator_strategies() == {"accessibility_id": 1, "resource_id": 1}


# ================================================================ text parser


def test_comments_are_stripped_but_line_numbers_are_preserved() -> None:
    source = "line1\n// CartScreen mentioned\n/* block\ncomment */\nline5"
    stripped = strip_comments(source)
    assert "CartScreen" not in stripped
    assert stripped.count("\n") == source.count("\n")


def test_page_object_named_only_in_a_comment_is_not_coverage(suite) -> None:
    """The Java fixture mentions DeliveryAddressScreen in a TODO comment."""
    java = [t for t in suite.tests if t.language is Language.JAVA]
    assert java, "Java fixture did not parse"
    assert all("delivery_address" not in t.sequence for t in java)


def test_annotation_arguments_are_not_screen_references(suite) -> None:
    """@Test(groups = {"regression", "cart"}) must not register cart coverage."""
    test = next(t for t in suite.tests if t.name == "addToCartUpdatesBadgeCount")
    assert test.sequence == ("home", "product_detail", "cart")
    assert test.sequence[0] != "cart", "group name leaked in as a screen"


def test_js_skip_variants_are_matched(suite) -> None:
    """An unmatched it.skip leaves the previous test's body running to EOF, so that
    test absorbs the skip marker and the real one disappears."""
    js = [t for t in suite.tests if t.language is Language.JAVASCRIPT]
    assert len(js) == 3
    skipped = [t for t in js if t.skipped]
    assert [t.name for t in skipped] == ["handles zero results gracefully"]


def test_java_page_object_reference_is_high_confidence(suite) -> None:
    test = next(t for t in suite.tests if t.name == "emptyCartShowsEmptyState")
    assert test.confidence is Confidence.HIGH


def test_unsupported_suffix_is_reported() -> None:
    parser = TextSuiteParser(TAXONOMY, ".")
    result = parser.parse_file("README.md")
    assert result.issues and "unsupported suffix" in result.issues[0].message


# =========================================================== the real fixture


def test_fixture_suite_parses_cleanly(suite) -> None:
    assert suite.parse_rate == 100.0
    assert suite.issues == []
    assert len(suite.tests) >= 18


def test_all_three_languages_are_represented(suite) -> None:
    assert set(suite.by_language()) == {"python", "java", "javascript"}


def test_every_fixture_test_maps_to_screens(suite) -> None:
    assert suite.mapping_rate == 100.0
    assert suite.unmapped_tests == []


def test_base_page_is_reported_as_unbound(suite) -> None:
    """BasePage is a helper, not a screen. It should parse but stay unbound."""
    unbound = [p.class_name for p in suite.page_objects if not p.is_bound]
    assert "BasePage" in unbound


def test_checkout_happy_path_is_recovered_in_full(suite) -> None:
    test = next(t for t in suite.tests if t.name == "test_search_to_purchase_completes")
    assert test.sequence == (
        "splash", "home", "search", "product_detail", "cart",
        "delivery_address", "payment_method", "order_review", "order_confirmation",
    )
