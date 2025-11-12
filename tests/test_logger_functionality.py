import logging
import asyncio
from io import StringIO
from src.logger import setup_logger, get_app_logger


def test_logger_initialization():
    """Тест инициализации логгера"""
    # Настройка логгера и проверка работы
    logger = setup_logger(name="test_logger", level="DEBUG", console=False)
    
    # Проверяем, что логгер создался
    assert logger.name == "test_logger"
    assert logger.level == logging.DEBUG
    
    # Проверяем get_app_logger
    same_logger = get_app_logger("test_logger")
    assert same_logger.name == "test_logger"
    assert same_logger is logger


def test_logger_reconfiguration():
    """Тест переконфигурации логгера"""
    # Первая конфигурация
    logger1 = setup_logger(name="reconfig_test", level="INFO", console=False)
    initial_handlers = len(logger1.handlers)
    
    # Переконфигурация
    logger2 = setup_logger(name="reconfig_test", level="DEBUG", console=False)
    
    # Проверяем, что уровень изменился
    assert logger2.level == logging.DEBUG
    assert logger2 is logger1  # тот же объект
    # Хендлеры обновлены (очищены и пересозданы)
    assert len(logger2.handlers) == initial_handlers


if __name__ == "__main__":
    import unittest
    
    class LoggerTests(unittest.TestCase):
        def test_init(self):
            test_logger_initialization()
            
        def test_reconfig(self):
            test_logger_reconfiguration()
    
    unittest.main()
