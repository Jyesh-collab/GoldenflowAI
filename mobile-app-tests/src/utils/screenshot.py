"""Timestamped screenshot capture, saved under reports/screenshots/."""

from datetime import datetime
from pathlib import Path

_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "screenshots"


def save_screenshot(driver, name: str) -> str:
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = _SCREENSHOT_DIR / f"{safe_name}_{timestamp}.png"
    driver.save_screenshot(str(path))
    return str(path)
