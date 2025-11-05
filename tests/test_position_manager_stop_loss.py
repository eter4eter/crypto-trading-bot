import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from src.trading.position_manager import PositionManager
from src.storage.models import OrderRecord
from src.config import Config


class TestPositionManagerStopLossStreak(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Mock dependencies
        self.config = MagicMock(spec=Config)
        self.config.max_stop_loss_trades = 3
        
        self.client = MagicMock()
        self.database = MagicMock()
        self.notifier = MagicMock()
        self.order_tracker = MagicMock()
        
        self.pm = PositionManager(
            config=self.config,
            client=self.client,
            database=self.database,
            notifier=self.notifier,
            order_tracker=self.order_tracker
        )

    def test_increment_stop_loss_streak(self) -> None:
        """Тест инкремента счетчика SL streak"""
        initial_streak = self.pm.stop_loss_streak
        self.pm._update_stop_loss_streak(increment=True)
        
        self.assertEqual(self.pm.stop_loss_streak, initial_streak + 1)
        self.assertIsNotNone(self.pm.last_stop_loss_time)
        self.assertEqual(self.pm.max_stop_loss_streak, self.pm.stop_loss_streak)

    def test_reset_stop_loss_streak(self) -> None:
        """Тест сброса счетчика SL streak"""
        # Устанавливаем некоторый streak
        self.pm.stop_loss_streak = 2
        self.pm.last_stop_loss_time = datetime.now()
        
        self.pm._update_stop_loss_streak(increment=False)
        
        self.assertEqual(self.pm.stop_loss_streak, 0)
        self.assertIsNone(self.pm.last_stop_loss_time)

    def test_auto_reset_after_24_hours(self) -> None:
        """Тест автосброса SL streak через 24 часа"""
        # Устанавливаем streak с временем 25 часов назад
        self.pm.stop_loss_streak = 5
        self.pm.last_stop_loss_time = datetime.now() - timedelta(hours=25)
        
        # Проверяем автосброс
        self.pm._check_auto_reset_stop_loss_streak()
        
        self.assertEqual(self.pm.stop_loss_streak, 0)
        self.assertIsNone(self.pm.last_stop_loss_time)

    def test_no_auto_reset_before_24_hours(self) -> None:
        """Тест что автосброс НЕ происходит до 24 часов"""
        # Устанавливаем streak с временем 10 часов назад
        initial_streak = 3
        self.pm.stop_loss_streak = initial_streak
        self.pm.last_stop_loss_time = datetime.now() - timedelta(hours=10)
        
        # Проверяем что автосброс НЕ происходит
        self.pm._check_auto_reset_stop_loss_streak()
        
        self.assertEqual(self.pm.stop_loss_streak, initial_streak)
        self.assertIsNotNone(self.pm.last_stop_loss_time)

    def test_no_auto_reset_when_streak_is_zero(self) -> None:
        """Тест что автосброс не срабатывает при streak=0"""
        self.pm.stop_loss_streak = 0
        self.pm.last_stop_loss_time = None
        
        self.pm._check_auto_reset_stop_loss_streak()
        
        self.assertEqual(self.pm.stop_loss_streak, 0)
        self.assertIsNone(self.pm.last_stop_loss_time)


if __name__ == "__main__":
    unittest.main()
