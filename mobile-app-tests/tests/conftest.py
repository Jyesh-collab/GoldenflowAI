import sys
from pathlib import Path

import allure
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config_reader import load_config, load_test_data  # noqa: E402
from src.driver.driver_factory import create_driver  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402
from src.utils.screenshot import save_screenshot  # noqa: E402


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def test_data():
    return load_test_data()


@pytest.fixture(scope="session")
def logger():
    return get_logger("mobile-app-tests")


@pytest.fixture
def driver(config, logger):
    logger.info("Starting Appium session for app: %s", config["app_path"])
    drv = create_driver(config)
    yield drv
    logger.info("Quitting Appium session")
    drv.quit()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        driver_fixture = item.funcargs.get("driver")
        if driver_fixture is not None:
            path = save_screenshot(driver_fixture, item.name)
            if path:
                allure.attach.file(
                    path, name="failure-screenshot", attachment_type=allure.attachment_type.PNG
                )
