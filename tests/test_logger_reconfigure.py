import logging
import unittest
from io import StringIO
from contextlib import redirect_stdout

from src.logger import setup_logger, get_app_logger


class TestLoggerReconfigure(unittest.TestCase):
    def test_reconfigure_changes_level_globally(self):
        # Инициализация с INFO
        setup_logger(level="INFO", console=True)
        log = get_app_logger()

        buf1 = StringIO()
        with redirect_stdout(buf1):
            log.debug("debug-hidden")
            log.info("info-visible")
        out1 = buf1.getvalue()
        self.assertIn("info-visible", out1)
        self.assertNotIn("debug-hidden", out1)

        # Переинициализация с DEBUG
        setup_logger(level="DEBUG", console=True)
        buf2 = StringIO()
        with redirect_stdout(buf2):
            log.debug("debug-now-visible")
        out2 = buf2.getvalue()
        self.assertIn("debug-now-visible", out2)


if __name__ == "__main__":
    unittest.main()
