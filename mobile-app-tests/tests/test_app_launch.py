import allure

from src.pages.home_page import HomePage


@allure.title("App launches and home screen is visible")
def test_app_launches_and_home_screen_visible(driver, config, logger):
    with allure.step("Launch app and wait for home screen"):
        home = HomePage(driver, timeout=config["explicit_wait"])
        loaded = home.is_loaded()
        logger.info("Home screen loaded: %s", loaded)

    with allure.step("Assert home tab is visible"):
        assert loaded, "Home tab was not visible after app launch"
