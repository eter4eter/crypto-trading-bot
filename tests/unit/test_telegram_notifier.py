import pytest
from unittest.mock import AsyncMock, Mock, patch
from src.notifications.telegram_notifier import TelegramNotifier
from src.config import TelegramConfig


@pytest.mark.unit
class TestTelegramNotifier:
    """Тесты Telegram notifier"""

    def test_notifier_disabled(self):
        """Тест отключенного notifier"""
        config = TelegramConfig(enabled=False)
        notifier = TelegramNotifier(config)

        assert notifier.enabled is False

    def test_notifier_enabled_with_credentials(self):
        """Тест включенного notifier с учетными данными"""
        config = TelegramConfig(
            enabled=True,
            bot_token="test_token",
            chat_id="test_chat_id"
        )
        notifier = TelegramNotifier(config)

        assert notifier.enabled is True
        assert "test_token" in notifier.api_url

    def test_notifier_disabled_without_token(self):
        """Тест notifier без токена"""
        config = TelegramConfig(
            enabled=True,
            bot_token="",
            chat_id="test_chat_id"
        )
        notifier = TelegramNotifier(config)

        assert notifier.enabled is False

    @pytest.mark.asyncio
    async def test_send_message_disabled(self):
        """Тест отправки когда отключено"""
        config = TelegramConfig(enabled=False)
        notifier = TelegramNotifier(config)

        # Не должно выбрасывать исключений
        await notifier.send_message("Test message")

    @pytest.mark.asyncio
    @patch('aiohttp.ClientSession')
    async def test_send_message_success(self, mock_session_class):
        """Тест успешной отправки"""
        config = TelegramConfig(
            enabled=True,
            bot_token="test_token",
            chat_id="test_chat_id"
        )
        notifier = TelegramNotifier(config)

        # Mock aiohttp
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_session = AsyncMock()
        mock_session.post.return_value = mock_response
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        mock_session_class.return_value = mock_session

        await notifier.send_message("Test message")

        mock_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_signal_disabled(self):
        """Тест уведомления о сигнале когда отключено"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_signals=False  # Отключено
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_signal(
            pair_name="TEST",
            side="Buy",
            entry_price=1.23,
            quantity=0.75,
            take_profit=0.00001075,
            stop_loss=0.00001075,
        )

        notifier.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_signal_content(self):
        """Тест содержимого уведомления о сигнале"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_signals=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_signal(
            pair_name="TEST",
            side="Buy",
            entry_price=1.23,
            quantity=0.75,
            take_profit=0.00001075,
            stop_loss=0.00001075,
        )

        notifier.send_message.assert_called_once()
        message = notifier.send_message.call_args[0][0]

        assert "Buy" in message
        assert "BTC-PEPE" in message
        assert "1.23" in message or "+1.23" in message
        assert "0.75" in message or "+0.75" in message

    @pytest.mark.asyncio
    async def test_notify_trade_opened(self):
        """Тест уведомления об открытии позиции"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_trades=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_trade_opened(
            pair_name="BTC-PEPE",
            side="Buy",
            entry_price=0.00001075,
            quantity=100.0,
            take_profit=0.00001096,
            stop_loss=0.00001064
        )

        notifier.send_message.assert_called_once()
        message = notifier.send_message.call_args[0][0]

        assert "открыта" in message.lower() or "opened" in message.lower()
        assert "BTC-PEPE" in message

    @pytest.mark.asyncio
    async def test_notify_trade_closed_profit(self):
        """Тест уведомления о закрытии с прибылью"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_trades=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_trade_closed(
            pair_name="BTC-PEPE",
            pnl=2.15,
            pnl_percent=2.0,
            close_reason="TP",
            duration_seconds=165
        )

        message = notifier.send_message.call_args[0][0]

        assert "✅" in message or "profit" in message.lower()
        assert "2.15" in message or "+2.15" in message
        assert "TP" in message

    @pytest.mark.asyncio
    async def test_notify_trade_closed_loss(self):
        """Тест уведомления о закрытии с убытком"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_trades=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_trade_closed(
            pair_name="BTC-PEPE",
            pnl=-1.50,
            pnl_percent=-1.0,
            close_reason="SL",
            duration_seconds=90
        )

        message = notifier.send_message.call_args[0][0]

        assert "❌" in message or "loss" in message.lower()
        assert "-1.50" in message or "1.50" in message
        assert "SL" in message

    @pytest.mark.asyncio
    async def test_notify_error(self):
        """Тест уведомления об ошибке"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_errors=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        await notifier.notify_error("Test error message")

        message = notifier.send_message.call_args[0][0]

        assert "⚠️" in message or "error" in message.lower()
        assert "Test error message" in message

    @pytest.mark.asyncio
    async def test_notify_daily_report(self):
        """Тест дневного отчета"""
        config = TelegramConfig(
            enabled=True,
            bot_token="token",
            chat_id="chat",
            notify_daily_report=True
        )
        notifier = TelegramNotifier(config)
        notifier.send_message = AsyncMock()

        stats = {
            "total_trades": 10,
            "profitable_trades": 7,
            "win_rate": 70.0,
            "total_pnl": 25.50,
            "best_trade": 5.00,
            "worst_trade": -2.00
        }

        await notifier.notify_daily_report(stats)

        message = notifier.send_message.call_args[0][0]

        assert "отчет" in message.lower() or "report" in message.lower()
        assert "10" in message
        assert "70" in message or "70.0" in message
