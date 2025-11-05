import pytest

from unittest.mock import Mock
from src.trading.position_manager import PositionManager


@pytest.mark.unit
class TestPositionManager:
    """Тесты менеджера позиций"""

    @pytest.fixture
    def position_manager(self, sample_config_dict, mock_client, temp_db):
        """Менеджер позиций для тестов"""
        from src.config import Config
        from src.storage.database import Database
        from src.notifications.telegram_notifier import TelegramNotifier
        from src.trading.order_tracker import OrderTracker
        import tempfile

        # Создаем временный конфиг файл
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            import json
            json.dump(sample_config_dict, f)
            config_path = f.name

        config = Config.load(config_path)
        database = Database(temp_db)
        notifier = TelegramNotifier(config.telegram)
        tracker = OrderTracker(mock_client)

        pm = PositionManager(config, mock_client, database, notifier, tracker)

        yield pm

        import os
        os.unlink(config_path)

    @pytest.mark.asyncio
    async def test_initialize(self, position_manager, mock_client):
        """Тест инициализации"""
        mock_client.get_wallet_balance.return_value = {
            "list": [{"totalEquity": "10000.00"}]
        }
        mock_client.set_leverage.return_value = True

        await position_manager.initialize()

        assert position_manager.wallet_balance == 10000.0
        mock_client.set_leverage.assert_called()

    @pytest.mark.asyncio
    async def test_update_wallet_balance_success(self, position_manager, mock_client):
        """Тест обновления баланса"""
        mock_client.get_wallet_balance.return_value = {
            "list": [{"totalEquity": "12345.67"}]
        }

        await position_manager._update_wallet_balance()

        assert position_manager.wallet_balance == 12345.67

    @pytest.mark.asyncio
    async def test_update_wallet_balance_failure(self, position_manager, mock_client):
        """Тест неудачного обновления баланса"""
        old_balance = position_manager.wallet_balance
        mock_client.get_wallet_balance.return_value = None

        await position_manager._update_wallet_balance()

        # Баланс не изменился
        assert position_manager.wallet_balance == old_balance

    @pytest.mark.asyncio
    async def test_open_position_success(self, position_manager, mock_client, sample_signal):
        """Тест успешного открытия позиции"""
        from src.config import PairConfig

        position_manager.wallet_balance = 10000.0

        mock_client.place_market_order.return_value = {
            "orderId": "order_123"
        }

        pair = position_manager.config.pairs[0]

        success = await position_manager._open_position(pair, sample_signal)

        assert success is True
        assert "TEST-PAIR" in position_manager.open_positions
        assert position_manager.total_trades == 1

        order = position_manager.open_positions["TEST-PAIR"]
        assert order.status == "OPEN"
        assert order.side == "Buy"

    @pytest.mark.asyncio
    async def test_open_position_insufficient_balance(self, position_manager, mock_client, sample_signal):
        """Тест открытия с недостаточным балансом"""
        position_manager.wallet_balance = 0.0

        pair = position_manager.config.pairs[0]

        success = await position_manager._open_position(pair, sample_signal)

        assert success is False
        assert len(position_manager.open_positions) == 0

    @pytest.mark.asyncio
    async def test_open_position_size_too_small(self, position_manager, mock_client, sample_signal):
        """Тест открытия со слишком маленьким размером"""
        position_manager.wallet_balance = 30.0  # 10% = 3 USDT < 5 USDT

        pair = position_manager.config.pairs[0]

        success = await position_manager._open_position(pair, sample_signal)

        assert success is False

    @pytest.mark.asyncio
    async def test_open_position_calculates_tp_sl_correctly(self, position_manager, mock_client, sample_signal):
        """Тест правильного расчета TP/SL"""
        position_manager.wallet_balance = 10000.0

        mock_client.place_market_order.return_value = {"orderId": "order_123"}

        pair = position_manager.config.pairs[0]
        sample_signal.action = "Buy"
        sample_signal.target_price = 0.00001000

        await position_manager._open_position(pair, sample_signal)

        # Проверяем вызов place_market_order
        call_args = mock_client.place_market_order.call_args[1]

        # TP должен быть на 2% выше
        expected_tp = 0.00001000 * 1.02
        assert float(call_args["take_profit"]) == pytest.approx(expected_tp, rel=1e-6)

        # SL должен быть на 1% ниже
        expected_sl = 0.00001000 * 0.99
        assert float(call_args["stop_loss"]) == pytest.approx(expected_sl, rel=1e-6)

    @pytest.mark.asyncio
    async def test_execute_signal_checks_stop_loss_streak(self, position_manager, sample_signal):
        """Тест проверки лимита stop-loss"""
        pair = position_manager.config.pairs[0]

        # Устанавливаем максимальную серию
        position_manager.stop_loss_streak = 5
        position_manager.config.max_stop_loss_trades = 3

        success = await position_manager.execute_signal(pair, sample_signal)

        assert success is False

    @pytest.mark.asyncio
    async def test_execute_signal_checks_existing_position(self, position_manager, sample_signal):
        """Тест проверки существующей позиции"""
        pair = position_manager.config.pairs[0]

        # Добавляем существующую позицию
        position_manager.open_positions["TEST-PAIR"] = Mock()

        success = await position_manager.execute_signal(pair, sample_signal)

        assert success is False

    def test_has_position(self, position_manager):
        """Тест проверки наличия позиции"""
        assert position_manager.has_position("TEST-PAIR") is False

        position_manager.open_positions["TEST-PAIR"] = Mock()

        assert position_manager.has_position("TEST-PAIR") is True

    def test_get_win_rate(self, position_manager):
        """Тест расчета винрейта"""
        # Нет сделок
        assert position_manager.get_win_rate() == 0.0

        # 3 из 5 прибыльных
        position_manager.total_trades = 5
        position_manager.profitable_trades = 3

        assert position_manager.get_win_rate() == 60.0

    def test_get_stats(self, position_manager):
        """Тест получения статистики"""
        position_manager.total_trades = 10
        position_manager.profitable_trades = 7
        position_manager.stop_loss_streak = 2
        position_manager.max_stop_loss_streak = 3

        stats = position_manager.get_stats()

        assert stats["total_trades"] == 10
        assert stats["profitable_trades"] == 7
        assert "70.0%" in stats["win_rate"]
        assert stats["stop_loss_streak"] == 2
        assert stats["max_stop_loss_streak"] == 3
