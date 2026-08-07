"""Browse funnel tests.

Three tests walk essentially the same path. Phase 3 should surface this as
over-testing - effort concentrated on a low-value journey while checkout failure
recovery has no coverage at all.
"""

import pytest

from pages.browse_pages import (
    CategoryListScreen,
    HomeScreen,
    ProductDetailScreen,
    ProductListScreen,
    SearchScreen,
    SplashScreen,
)


@pytest.mark.smoke
def test_category_browse_reaches_product_detail(driver):
    SplashScreen(driver).continue_to_home()

    home = HomeScreen(driver)
    home.open_category()

    categories = CategoryListScreen(driver)
    categories.open_category()

    plp = ProductListScreen(driver)
    plp.open_product()

    pdp = ProductDetailScreen(driver)
    assert pdp.title() is not None


def test_category_browse_with_filter(driver):
    SplashScreen(driver).continue_to_home()
    HomeScreen(driver).open_category()
    CategoryListScreen(driver).open_category()

    plp = ProductListScreen(driver)
    plp.apply_filter()
    plp.open_product()

    assert ProductDetailScreen(driver).title() is not None


def test_category_browse_second_product(driver):
    SplashScreen(driver).continue_to_home()
    HomeScreen(driver).open_category()
    CategoryListScreen(driver).open_category()
    ProductListScreen(driver).open_product(index=1)
    assert ProductDetailScreen(driver).price() is not None


@pytest.mark.smoke
def test_search_results_open_product(driver):
    home = HomeScreen(driver)
    home.open_search()

    search = SearchScreen(driver)
    search.search_for("jacket")
    assert search.result_count() >= 0
    search.open_result()

    assert ProductDetailScreen(driver).title() is not None
