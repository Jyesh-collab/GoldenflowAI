# mobile-app-tests

Python + Pytest + Appium (UiAutomator2) automation framework for the Bagisto
Flutter app in `../mobile-app`. Kept intentionally simple: one config file,
one fixtures file, minimal Page Object Model, HTML + Allure reporting.

## Layout

```text
mobile-app-tests/
├── config/
│   ├── config.yaml            # device/app/appium server settings
│   ├── test_data.yaml         # sample product + guest checkout data (see below)
│   └── config_reader.py       # loads config.yaml/test_data.yaml
├── src/
│   ├── driver/driver_factory.py   # builds the Appium UiAutomator2 session
│   ├── pages/                     # Page Object Model
│   │   ├── base_page.py           # find/click/type_by_hint/scroll (see "Locators" below)
│   │   ├── home_page.py           # home tab bar (Home/Categories/Cart/Account)
│   │   ├── categories_page.py     # Categories tab's category chip row
│   │   ├── category_grid_page.py  # category product grid
│   │   ├── product_page.py        # product detail / add to cart / open cart
│   │   ├── cart_page.py           # cart / proceed to checkout
│   │   ├── checkout_page.py       # guest billing form, shipping/payment, place order
│   │   └── thankyou_page.py       # order confirmation
│   └── utils/
│       ├── logger.py              # console + per-run log file
│       └── screenshot.py          # timestamped screenshot capture
├── tests/
│   ├── conftest.py            # config/test_data/driver/logger fixtures + failure screenshot hook
│   ├── test_app_launch.py     # smoke test
│   └── test_checkout_flow.py  # open app → product → add to cart → guest checkout
├── reports/                   # gitignored: report.html, allure-results/, screenshots/
└── logs/                      # gitignored: run_<timestamp>.log
```

## Test data

`config/test_data.yaml` holds the sample data used by `test_checkout_flow.py`
so the flow runs against the same product and guest address every time:

- `product` — a "simple" product from Bagisto's default demo catalog
  (`el-air-fryer`, in the seeded "Household" category, price `$122.50`) with
  no configurable/bundle/customizable options, so it can be added to cart
  with no extra selection required.
- `guest_checkout` — fake guest customer/address details for the checkout
  form. `state` must be a valid subdivision of `country`.

Edit this file (not the test) to point at different sample data.

### Locators: what actually works on this app

Everything here was verified against real `uiautomator dump` output and the
backend's request log on a running emulator — not just the Dart source,
which turned out to be a poor predictor of the actual accessibility tree:

- **Most content-desc values are merged.** Flutter frequently joins several
  Semantics/Text nodes into one content-desc with embedded newlines — e.g. a
  product card is `"{name}\n{price}\n{rating}\n{reviews}"`, a payment row is
  `"{title}\n{title}"`. Exact `ACCESSIBILITY_ID` matching only works for
  labels that *aren't* merged with anything else (e.g. "Add to Cart", "Pay
  Now", "Place Order"); merged ones need `click_containing`/
  `by_description_contains` instead.
- **Form field labels are the native `hint` attribute, not content-desc at
  all.** Billing form fields, Country/State selectors, etc. are located via
  `type_by_hint`/`click_by_hint`. These scan elements by class
  (`EditText`/`View`) and filter by the `hint` attribute in Python rather
  than using an XPath attribute predicate (`//*[@hint="..."]`) — the latter
  was intermittently unable to find fields that unquestionably existed.
- **A freshly-appeared field (e.g. a bottom sheet's search box) doesn't
  necessarily have input focus yet just because it's displayed.**
  `send_keys()` into it can silently type nothing — not dropped characters,
  zero characters, repeatably — so `type_by_hint` taps the field first (the
  same thing a real user would do) before typing.
- **The Home screen's category carousel is unreliable** — it's a curated
  subset of categories (observed: Mens/Womens/Casual Wear/Formal Wear only)
  and doesn't include every category. Use the **Categories tab** instead
  (`categories_page.py`): its chip row reliably has every top-level category
  as a plain, un-merged content-desc, and tapping one navigates straight
  into that category's product grid.
- **The cart page's item name has zero accessibility exposure** — no
  content-desc, no `text` attribute at all. `cart_page.has_item_priced()`
  asserts on the line-item price instead, which *is* exposed.
- **No click mechanism reliably registers with Flutter's gesture arena
  except real input-device taps.** `element.click()`, a W3C actions
  `tap()`, and UiAutomator2's own `mobile: clickGesture` (by elementId or
  by coordinate) were all observed to silently no-op on at least one
  button (the cart's "Pay Now") — the wait succeeds, no exception is
  raised, but the app never navigates, confirmed by the backend's request
  log showing zero further API calls. Only `adb shell input tap` (real
  input-device event injection) was recognized every time, so `click()`
  issues that via Appium's `mobile: shell` (same device connection as the
  session, falling back to a plain `adb` subprocess if unavailable) rather
  than any Appium gesture API.
- **This store's active payment methods are all online gateways** (Stripe,
  Razorpay, PayU, PhonePe, PayPal Smart Button/Standard) **plus "Money
  Transfer" — there is no Cash On Delivery.** Money Transfer is the only one
  that completes without an external redirect/SDK, so it's what
  `checkout_page.select_payment_method()` defaults to.

If the app adds real `Semantics`/`key` values to more screens later, or the
backend's active payment methods change, prefer switching the corresponding
locators/defaults over patching around them.

### A note on backend latency

The dev backend's GraphQL calls have been observed taking **2–7+ seconds
each** (see `../mobile-app/bagisto-backend/bagisto/storage/logs/laravel.log`
— `grep "API Request"` shows `duration_ms` per call), and screens like
checkout chain several of them before they finish rendering. `explicit_wait`
in `config.yaml` is set to 45s to accommodate this — a shorter value causes
flaky "element not found" failures that are actually just slow page loads,
not missing elements. The backend has also been observed intermittently
returning outright errors ("The server encountered an error" / "A network
error occurred") if its Docker containers aren't running or under sustained
load; if a run fails on a step that screenshots one of those error screens,
run `docker compose ps` in `../mobile-app/bagisto-backend` to check the
containers are up.

### A note on emulator health

Most of the debugging effort behind the "tap before type" and
class-scan-not-XPath fixes above turned out to trace back to the emulator
itself, not the app or the test code: an AVD left running for days
accumulates memory pressure that can destabilize UiAutomator2's on-device
instrumentation, and low host disk space can silently prevent the emulator
from actually cold-booting (it'll report success while quietly resuming a
stale, days-old snapshot instead — check `adb shell uptime`; it should read
minutes, not days, right after a boot). If runs start failing
unpredictably at different, seemingly-unrelated steps with no consistent
pattern, check `adb shell uptime`, `adb shell dumpsys meminfo` (free RAM),
and `df -h` (host disk space) before assuming it's a locator regression.
`pytest.ini`'s `--reruns 2 --reruns-delay 5` is a pragmatic safety net for
this class of environment flakiness, not a substitute for fixing it — a
healthy environment should pass without needing a rerun.

## Prerequisites

- Android emulator running (or a physical device connected via `adb`), matching
  `device_name` in `config/config.yaml` (`adb devices` to check).
- The debug APK built: from `../mobile-app`, run `./scripts/build_local.sh debug`
  (produces `app-debug-local.apk`, which `config.yaml` points at by default).
- An Appium server running with the UiAutomator2 driver installed:

  ```bash
  appium driver list --installed   # confirm uiautomator2 is there
  appium                           # starts the server on 127.0.0.1:4723
  ```
- Python 3.12 (recommended over newer/bleeding-edge versions for wheel
  compatibility with the test-tooling packages below).

## Setup

```bash
cd mobile-app-tests
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the tests

With the emulator booted and the Appium server running:

```bash
source .venv/bin/activate
pytest
```

This runs everything under `tests/`, and (per `pytest.ini`) always writes:

- `reports/report.html` — self-contained HTML report, open directly in a browser.
- `reports/allure-results/` — raw Allure results.

To view the Allure report (requires the [Allure CLI](https://allurereport.org/docs/gettingstarted-installation/)):

```bash
allure serve reports/allure-results                                    # live server, opens a browser
# or, for a static copy:
allure generate reports/allure-results -o reports/allure-report --clean
open reports/allure-report/index.html
```

Always pass `reports/allure-results` explicitly — the Allure CLI defaults to
looking for `./allure-results` in your current directory when no path is
given, which won't exist and will silently serve a blank report.

On any test failure, a screenshot is automatically saved to
`reports/screenshots/` and attached to the Allure report (see the
`pytest_runtest_makereport` hook in `tests/conftest.py`).

## Switching to the ngrok/remote APK

`config.yaml`'s `app_path` defaults to the local-backend flavor
(`app-debug-local.apk`) since it doesn't depend on the ngrok tunnel being up.
To test against `app-debug-remote.apk` instead, just change `app_path` in
`config/config.yaml` — nothing else in the framework needs to change.

## Extending the framework

- **More pages**: add a new `src/pages/<name>_page.py` subclassing `BasePage`,
  same pattern as `home_page.py`.
- **More tests**: add `tests/test_*.py` files; the `driver`, `config`, and
  `logger` fixtures are already available to any test via `conftest.py`.
- **CI**: not wired up yet — there's no existing CI config in this repo to
  hook into, so this is a deliberate follow-up rather than part of this
  initial framework.
