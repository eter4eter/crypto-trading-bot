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
    console: Optional[bool] = None,
    propagate: bool = True,
) -> logging.Logger:
    """
    Конфигурирует именованный логгер и иерархию модульных логгеров.

    Приоритет источников настроек:
    1) Параметры функции
    2) Переменные окружения: LOG_NAME, LOG_LEVEL, LOG_DIR, LOG_FILE, LOG_ERR_FILE, LOG_CONSOLE
    3) Значения по умолчанию
    """
    log_name = name or os.getenv("LOG_NAME", DEFAULT_LOG_NAME)
    log_level_name = (level or os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    dir_path = Path(log_dir or os.getenv("LOG_DIR", DEFAULT_LOG_DIR))
    file_name = log_file or os.getenv("LOG_FILE", DEFAULT_LOG_FILE)
    err_name = err_file or os.getenv("LOG_ERR_FILE", DEFAULT_ERR_FILE)

    if console is None:
        env_console = os.getenv("LOG_CONSOLE")
        enable_console = True if env_console is None else env_console == "1"
    else:
        enable_console = console

    dir_path.mkdir(parents=True, exist_ok=True)

    # Именованный логгер приложения
    app_logger = logging.getLogger(log_name)
    app_logger.setLevel(log_level)
    app_logger.handlers.clear()
    app_logger.propagate = False  # хэндлеры ставим на корень/на себя

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        app_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        dir_path / file_name,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    app_logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        dir_path / err_name,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    app_logger.addHandler(error_handler)

    # Настроим корневой логгер так, чтобы модульные логгеры с propagate=True наследовали хэндлеры
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    # Пробрасываем только к app_logger через промежуточный Handler
    bridge = logging.Handler()
    bridge.emit = lambda record: app_logger.handle(record)  # type: ignore[attr-defined]
    root_logger.addHandler(bridge)

    return app_logger


def get_app_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or DEFAULT_LOG_NAME)
