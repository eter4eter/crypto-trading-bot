import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

DEFAULT_LOG_NAME = "trading_bot"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = "logs"
DEFAULT_LOG_FILE = "trading_bot.log"
DEFAULT_ERR_FILE = "errors.log"


def setup_logger(
    name: Optional[str] = None,
    level: Optional[str] = None,
    log_dir: Optional[str] = None,
    log_file: Optional[str] = None,
    err_file: Optional[str] = None,
    console: bool = True,
    propagate: bool = False,
) -> logging.Logger:
    """
    Конфигурация логгера с параметрами из конфигурации приложения и/или окружения.

    Приоритет источников настроек (от большего к меньшему):
    1) Параметры функции
    2) Переменные окружения: LOG_NAME, LOG_LEVEL, LOG_DIR, LOG_FILE, LOG_ERR_FILE, LOG_CONSOLE
    3) Значения по умолчанию (DEFAULT_*)
    """

    log_name = name or os.getenv("LOG_NAME", DEFAULT_LOG_NAME)
    log_level_name = (level or os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    dir_path = Path(log_dir or os.getenv("LOG_DIR", DEFAULT_LOG_DIR))
    file_name = log_file or os.getenv("LOG_FILE", DEFAULT_LOG_FILE)
    err_name = err_file or os.getenv("LOG_ERR_FILE", DEFAULT_ERR_FILE)

    enable_console = console if console is not None else os.getenv("LOG_CONSOLE", "1") == "1"

    dir_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(log_name)
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = propagate

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        dir_path / file_name,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        dir_path / err_name,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    return logger


# Глобальный логгер, может быть переопределен вызовом setup_logger() в entrypoint
logger = setup_logger()
