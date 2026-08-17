"""Simple console + per-run file logger.

All loggers created via get_logger() share one log file per test run
(logs/run_<timestamp>.log), created the first time get_logger() is called.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_run_log_file: Path | None = None


def _get_run_log_file() -> Path:
    global _run_log_file
    if _run_log_file is None:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _run_log_file = _LOG_DIR / f"run_{timestamp}.log"
    return _run_log_file


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(_get_run_log_file())
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
