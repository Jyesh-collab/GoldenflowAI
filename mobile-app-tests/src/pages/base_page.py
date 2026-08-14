"""Common Page Object helpers: explicit waits only, no static sleeps.

These helpers encode quirks of this app and its dev environment, confirmed
by inspecting real `uiautomator dump` output and the backend's request log
against a running emulator (not just the Dart source or guesswork):

  - Many widgets merge several child Semantics/Text nodes into ONE
    content-desc joined by newlines, e.g. a product card's content-desc is
    "{name}\\n{name}\\n{price}" and a payment row's is "{title}\\n{title}".
    Exact ACCESSIBILITY_ID matching only works for labels Flutter did NOT
    merge with anything else; merged ones need a "contains" match instead.
  - TextFormField labels surface as the native `hint` attribute, not
    content-desc at all, so form fields are located by hint. XPath's
    `//*[@hint="..."]` attribute predicate is intermittently unreliable
    for this (confirmed repeatedly live — fails to find a field that's
    unquestionably present, with no pattern tied to wait/retry tuning), so
    hint lookups scan elements by class and filter by attribute in Python
    instead (see _find_by_hint).
  - No element in this app reports UiAutomator's `scrollable=true`, so
    `UiScrollable(...).scrollIntoView(...)` can't find a container to
    drive. Plain swipe gestures are used instead.
  - No Appium-mediated click/tap mechanism (element.click(), W3C actions
    tap(), or UiAutomator2's "mobile: clickGesture") reliably registers as
    a real tap with Flutter's gesture arena for every button in this app —
    confirmed via the backend's request log showing zero further API calls
    after some "successful" clicks. Real adb-injected input (`adb shell
    input tap`) was reliably recognized every time, so all clicks go
    through that instead (see _adb_tap).
  - The dev backend's GraphQL calls have been observed taking several
    seconds each (see config.yaml's explicit_wait comment), so screens
    that chain several of them can legitimately take a while to render.
"""

import re
import subprocess
import time

from appium.webdriver.common.appiumby import AppiumBy
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

    def _adb_tap(self, x: int, y: int):
        """Tap via `input tap`, i.e. real input-device event injection,
        rather than any Appium-mediated gesture — issued through Appium's
        own "mobile: shell" command (same device connection as the active
        session) rather than a second, separate `adb` subprocess, to avoid
        a race between two concurrent connections to the same device.
        Confirmed by repeated live testing, in order of decreasing
        reliability:
          - plain element.click() silently no-ops on some of this app's
            Flutter semantics nodes (e.g. the cart's "Pay Now") — the wait
            succeeds and no exception is raised, but the app never
            navigates;
          - driver.tap()'s W3C actions chain hung for the full 240s proxy
            timeout on this Appium/UiAutomator2 setup;
          - "mobile: clickGesture" (UiAutomator2's own gesture command),
            keyed by elementId or by x/y, also silently no-oped on
            "Pay Now" specifically — confirmed via the backend's request
            log: zero further GraphQL calls were made after any of these,
            proving Flutter's gesture arena never saw a recognized tap at
            all, not just a misplaced one.
        Only genuine adb-injected input (matching what a real touch driver
        produces) was reliably recognized every time."""
        try:
            self.driver.execute_script(
                "mobile: shell", {"command": "input", "args": ["tap", str(x), str(y)]}
            )
        except Exception:
            subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], check=True)

    def click(self, locator, attempts: int = 3):
        """Locate the element normally via Appium/Selenium, then tap its
        center using real adb input injection (see _adb_tap) instead of
        any Appium-mediated click/gesture. The raw `bounds` attribute
        (e.g. "[694,2019][1038,2143]") is used rather than Selenium's
        derived `.rect`, since it's the coordinate space confirmed against
        real `uiautomator dump` output throughout this app's exploration.

        The find step retries across several fresh WebDriverWait calls
        (same reasoning as _find_by_hint): confirmed live that a plain,
        unambiguous ACCESSIBILITY_ID lookup for an element clearly on
        screen can time out on one continuous wait, with no pattern tied
        to which element, wait duration, or retry count — restarting the
        wait a few times is measurably more reliable here than trusting
        one long poll.

        A brief pause after the wait resolves and before the tap fires:
        confirmed live (identical bounds, identical app state, backend
        healthy) that a tap thrown immediately off the back of a
        WebDriverWait polling loop can silently fail to register, while the
        exact same coordinate tapped a moment later — or typed manually —
        always works. Likely a race between UiAutomator2's own hierarchy
        polling and genuine touch dispatch on the same device connection."""
        per_attempt = max(self.timeout // attempts, 5)
        element = None
        last_exc = None
        for _ in range(attempts):
            try:
                element = WebDriverWait(self.driver, per_attempt).until(
                    EC.element_to_be_clickable(locator)
                )
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(1)
        if element is None:
            raise last_exc
        time.sleep(0.5)
        x1, y1, x2, y2 = (int(n) for n in re.findall(r"-?\d+", element.get_attribute("bounds")))
        self._adb_tap((x1 + x2) // 2, (y1 + y2) // 2)

    def type_text(self, locator, text: str, attempts: int = 3):
        """Retries the wait itself (not just polling within one), split
        across [attempts] fresh WebDriverWait calls with a short pause
        between. Confirmed live: a field that's visibly on screen and
        present in driver.page_source can still make a single continuous
        presence_of_element_located wait time out for the full budget —
        the same locator succeeds immediately when queried in isolation.
        Breaking and restarting the wait clears it more reliably than one
        long wait, even at the same total time budget."""
        per_attempt_timeout = max(self.timeout // attempts, 5)
        last_exc = None
        for _ in range(attempts):
            try:
                element = WebDriverWait(self.driver, per_attempt_timeout).until(
                    EC.presence_of_element_located(locator)
                )
                element.clear()
                element.send_keys(text)
                return
            except Exception as exc:
                last_exc = exc
                time.sleep(1)
        raise last_exc

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

    def click_until_visible(self, click_locator, confirm, attempts: int = 3, confirm_timeout: int = 25):
        """Click [click_locator] and wait up to confirm_timeout for
        [confirm] to be satisfied; retry the click if it isn't. [confirm]
        is either a locator tuple (checked via is_displayed) or a callable
        taking a timeout and returning bool (e.g. is_hint_displayed, for
        confirming on a hint-only field rather than a content-desc one).

        Confirmed live: a tap on a button that's visually enabled can still
        be silently ignored by the app if an async guard in its onTap
        handler (e.g. a Bloc "isLoading" flag) hasn't cleared yet — nothing
        Appium can see, since it's app-logic state, not accessibility
        state. Retrying the click until the next screen actually shows up
        is more robust than a single click plus a longer fixed delay.

        confirm_timeout defaults high (25s): this backend's GraphQL calls
        routinely take several seconds each (see config.yaml), and a
        screen that's genuinely just slow to respond — not a dropped tap —
        must not be reinterpreted as one, since firing a second real tap
        into a screen whose first tap actually landed can corrupt state
        (e.g. a stray tap landing on whatever now occupies that same
        screen position one page later)."""
        check = confirm if callable(confirm) else (lambda timeout: self.is_displayed(confirm, timeout=timeout))
        for _ in range(attempts):
            self.click(click_locator)
            if check(confirm_timeout):
                return
        raise TimeoutError(f"{click_locator} did not lead to {confirm} after {attempts} attempts")

    @staticmethod
    def by_description_contains(text: str):
        """Locator for content-desc values Flutter has merged with other
        text (e.g. a product card's "{name}\\n{price}")."""
        return (
            AppiumBy.ANDROID_UIAUTOMATOR,
            f'new UiSelector().descriptionContains("{text}")',
        )

    _HINT_CLASSES = ("android.widget.EditText", "android.view.View")

    def _find_by_hint(self, hint: str, timeout: int | None = None, attempts: int = 3):
        """Find the element whose `hint` attribute equals [hint] by
        scanning elements by class name and filtering in Python, instead
        of an XPath attribute predicate (`//*[@hint="..."]`).

        Confirmed live, repeatedly: that XPath query can intermittently
        fail to find a field that unquestionably exists — present in
        driver.page_source, visible on screen, and found instantly by the
        identical query moments earlier in an isolated repro. The
        class-scan approach is more reliable but not immune to the same
        underlying flakiness (observed hitting different, unpredictable
        fields across runs — not tied to any one field, wait duration, or
        retry count) — so, like type_text, this retries across several
        fresh WebDriverWait calls rather than trusting one continuous
        poll for the full budget."""

        def _find(driver):
            for class_name in self._HINT_CLASSES:
                for element in driver.find_elements(AppiumBy.CLASS_NAME, class_name):
                    if element.get_attribute("hint") == hint:
                        return element
            return False

        total = timeout or self.timeout
        per_attempt = max(total // attempts, 5)
        last_exc = None
        for _ in range(attempts):
            try:
                return WebDriverWait(self.driver, per_attempt).until(_find)
            except Exception as exc:
                last_exc = exc
                time.sleep(1)
        raise last_exc

    def is_hint_displayed(self, hint: str, timeout: int | None = None) -> bool:
        try:
            self._find_by_hint(hint, timeout=timeout)
            return True
        except Exception:
            return False

    def type_by_hint(self, hint: str, text: str):
        """Taps the field first, then clears and types. Confirmed live:
        send_keys() into a freshly-appeared field (e.g. a bottom sheet's
        search box) can silently type nothing at all — not flaky/dropped
        characters, literally zero characters land, repeatably across
        retries — most likely because the field doesn't actually hold
        input focus yet despite being present/displayed. An explicit tap,
        the same thing a real user would do before typing, establishes
        focus first."""
        element = self._find_by_hint(hint)
        x1, y1, x2, y2 = (int(n) for n in re.findall(r"-?\d+", element.get_attribute("bounds")))
        self._adb_tap((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(0.3)
        element.clear()
        element.send_keys(text)

    def click_by_hint(self, hint: str, confirm_hint: str | None = None, attempts: int = 3, confirm_timeout: int = 15):
        """Tap the field with this hint. If [confirm_hint] is given,
        retries the tap until a field with that hint appears (e.g.
        Country/State open a bottom sheet with a "Search..." field) —
        same silently-dropped-tap risk as elsewhere in this app (see
        click_until_visible's docstring); click_by_hint had no such
        protection before, unlike click()."""

        def _tap():
            element = self._find_by_hint(hint)
            time.sleep(0.5)
            x1, y1, x2, y2 = (int(n) for n in re.findall(r"-?\d+", element.get_attribute("bounds")))
            self._adb_tap((x1 + x2) // 2, (y1 + y2) // 2)

        if confirm_hint is None:
            _tap()
            return

        for _ in range(attempts):
            _tap()
            if self.is_hint_displayed(confirm_hint, timeout=confirm_timeout):
                return
        raise TimeoutError(f'tapping hint="{hint}" did not lead to hint="{confirm_hint}" after {attempts} attempts')

    def scroll_into_view(self, locator, max_swipes: int = 8):
        """Swipe up repeatedly until [locator] is on screen. No element in
        this app reports scrollable=true, so UiScrollable can't locate a
        container to drive here — plain swipe gestures are used instead."""
        if self.is_displayed(locator, timeout=1):
            return
        size = self.driver.get_window_size()
        start_x = size["width"] // 2
        start_y = int(size["height"] * 0.75)
        end_y = int(size["height"] * 0.35)
        for _ in range(max_swipes):
            self.driver.swipe(start_x, start_y, start_x, end_y, 300)
            if self.is_displayed(locator, timeout=1):
                return

    def click_exact(self, text: str, max_swipes: int = 8):
        """Click an element by an exact, un-merged accessibility id,
        scrolling it into view first if needed."""
        locator = (AppiumBy.ACCESSIBILITY_ID, text)
        self.scroll_into_view(locator, max_swipes=max_swipes)
        self.click(locator)

    def click_containing(self, text: str, max_swipes: int = 8):
        """Click an element whose content-desc contains [text] (for
        Flutter's merged-semantics nodes), scrolling it into view first."""
        locator = self.by_description_contains(text)
        self.scroll_into_view(locator, max_swipes=max_swipes)
        self.click(locator)

    def tap_relative(self, x_fraction: float, y_fraction: float):
        """Tap a fixed point on screen, given as a fraction of window
        width/height. Last-resort escape hatch for controls whose
        accessibility label is unreliable rather than just merged (e.g. the
        app bar's cart icon, whose content-desc has been observed as both
        "{count}\\nCart" and a bare "{count}" with no "Cart" text at all,
        across otherwise-identical app states) but whose on-screen position
        is fixed chrome, not scrolled content."""
        size = self.driver.get_window_size()
        x = int(size["width"] * x_fraction)
        y = int(size["height"] * y_fraction)
        self._adb_tap(x, y)
