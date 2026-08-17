"""Loads config/config.yaml into a plain dict, with app_path resolved
to an absolute path so it works regardless of the working directory
pytest is invoked from."""

from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"
_TEST_DATA_FILE = _CONFIG_DIR / "test_data.yaml"


def load_config() -> dict:
    with open(_CONFIG_FILE, "r") as f:
        config = yaml.safe_load(f)

    app_path = (_CONFIG_DIR / ".." / config["app_path"]).resolve()
    config["app_path"] = str(app_path)

    return config


def load_test_data() -> dict:
    with open(_TEST_DATA_FILE, "r") as f:
        return yaml.safe_load(f)
