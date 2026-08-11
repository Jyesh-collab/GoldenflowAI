"""Builds an Appium UiAutomator2 session from the loaded config dict."""

from appium import webdriver
from appium.options.android import UiAutomator2Options


def create_driver(config: dict) -> webdriver.Remote:
    options = UiAutomator2Options()
    options.platform_name = config["platform_name"]
    options.device_name = config["device_name"]
    options.automation_name = config["automation_name"]
    options.app = config["app_path"]
    options.new_command_timeout = config["new_command_timeout"]

    driver = webdriver.Remote(config["appium_server_url"], options=options)
    driver.implicitly_wait(config["implicit_wait"])
    return driver
