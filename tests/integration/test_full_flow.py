import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime


@pytest.mark.integration
class TestFullFlow:
    """Тесты полного flow от сигнала до закрытия"""

    @pytest.mark.asyncio
    async def test_signal_to_order_flow(self, temp_config_file, temp_db, mock_client):
        """Тест потока: сигнал → ордер → позиция"""
        from src.config import Config
        from src.storage.database import Database
        from src.notifications.telegram_notifier import TelegramNotifier
        from src.trading.order_tracker import OrderTracker
        from src.trading.position_manager import PositionManager
        from src.strategy.correlation_strategy import CorrelationStrategy, Signal

        # Загружаем компоненты
        config = Config.load(temp_config_file)
        database = Database(temp_db)
        notifier = TelegramNotifier(config.telegram)
        tracker = OrderTracker(mock_client)
        pm = PositionManager(config, mock_client, database, notifier, tracker)

        # Настраиваем mock
        mock_client.get_wallet_balance.return_value = {
            "list": [{"totalEquity": "10000.00"}]
        }
        mock_client.set_leverage.return_value = True
        mock_client.place_market_order.return_value = {
            "orderId": "test_order_123"
        }

        # Инициализируем
        await pm.initialize()

        # Создаем сигнал
        signal = Signal(
            action="buy",
            target_price=0.00001075,
            dominant_change=1.23,
            target_change=0.75
        )

        # Исполняем
        pair = config.pairs[0]
        success = await pm.execute_signal(pair, signal)

        # Проверки
        assert success is True
        assert pair.name in pm.open_positions

        # Проверяем БД
        open_orders = database.get_open_orders(pair.name)
        assert len(open_orders) == 1
        assert open_orders[0].status == "OPEN"

        # Проверяем трекер
        assert len(tracker.tracking_orders) == 1

    @pytest.mark.asyncio
    async def test_strategy_generates_valid_signal(self, temp_config_file, mock_client):
        """Тест генерации валидного сигнала стратегией"""
        from src.config import Config
        from src.strategy.correlation_strategy import CorrelationStrategy

        config = Config.load(temp_config_file)
        pair = config.pairs[0]

        strategy = CorrelationStrategy(pair, mock_client)

        # Настраиваем mock для заполнения буфера
        prices_btc = [50000, 50100, 50200, 50300, 50600]  # +1.2%
        prices_pepe = [0.00001000, 0.00001020, 0.00001030, 0.00001050, 0.00001070]  # +0.7%

        mock_client.get_ticker.side_effect = [
            price for pair in zip(prices_btc, prices_pepe) for price in pair
        ]

        # Заполняем буфер
        for _ in range(5):
            await strategy.update_ticks()

        # Проверяем сигнал
        signal = await strategy.check_signal()

        assert signal.action == "Buy"
        assert signal.dominant_change > 1.0
        assert abs(signal.target_change) < 0.8

    @pytest.mark.asyncio
    async def test_complete_trade_cycle(self, temp_config_file, temp_db, mock_client):
        """Тест полного цикла сделки"""
        from src.config import Config
        from src.storage.database import Database
        from src.notifications.telegram_notifier import TelegramNotifier
        from src.trading.order_tracker import OrderTracker
        from src.trading.position_manager import PositionManager
        from src.strategy.correlation_strategy import Signal

        config = Config.load(temp_config_file)
        database = Database(temp_db)
        notifier = TelegramNotifier(config.telegram)
        tracker = OrderTracker(mock_client)
        pm = PositionManager(config, mock_client, database, notifier, tracker)

        # Setup
        mock_client.get_wallet_balance.return_value = {
            "list": [{"totalEquity": "10000.00"}]
        }
        mock_client.set_leverage.return_value = True
        mock_client.place_market_order.return_value = {"orderId": "order_123"}

        await pm.initialize()

        # 1. Открываем позицию
        signal = Signal("Buy", 0.00001075, 1.23, 0.75)
        pair = config.pairs[0]

        await pm.execute_signal(pair, signal)
        assert len(pm.open_positions) == 1

        # 2. Симулируем закрытие позиции
        mock_client.get_position.return_value = None  # Позиция закрыта
        mock_client.get_order_history.return_value = [{
            "orderId": "order_123",
            "orderStatus": "Filled",
            "avgPrice": "0.00001096"  # Закрылась по TP
        }]

        # 3. Проверяем позиции
        await pm.check_positions()

        # Позиция должна быть закрыта
        assert len(pm.open_positions) == 0
        assert pm.profitable_trades == 1

        # Проверяем БД
        import sqlite3
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, pnl FROM orders WHERE order_id = ?", ("order_123",))
            row = cursor.fetchone()

        assert row[0] == "CLOSED"
        assert row[1] > 0  # Прибыль
