# mobile-app-tests

Python + Pytest + Appium (UiAutomator2) automation framework for the Bagisto
Flutter app in `../mobile-app`. Kept intentionally simple: one config file,
one fixtures file, minimal Page Object Model, HTML + Allure reporting.

## Layout

```text
mobile-app-tests/
├── config/
│   ├── config.yaml            # device/app/appium server settings
│   └── config_reader.py       # loads config.yaml, resolves app_path
├── src/
│   ├── driver/driver_factory.py   # builds the Appium UiAutomator2 session
│   ├── pages/                     # Page Object Model
│   │   ├── base_page.py           # find/click/is_displayed/get_text (explicit waits)
│   │   └── home_page.py           # first page object
│   └── utils/
│       ├── logger.py              # console + per-run log file
│       └── screenshot.py          # timestamped screenshot capture
├── tests/
│   ├── conftest.py            # config/driver/logger fixtures + failure screenshot hook
│   └── test_app_launch.py     # the one smoke test
├── reports/                   # gitignored: report.html, allure-results/, screenshots/
└── logs/                      # gitignored: run_<timestamp>.log
```

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
