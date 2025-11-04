import pytest

from unittest.mock import Mock, AsyncMock, patch
from src.api.bybit_client import BybitClient, async_wrap, retry


@pytest.mark.unit
class TestAsyncWrap:
    """Тесты async_wrap декоратора"""

    @pytest.mark.asyncio
    async def test_async_wrap_with_functools_partial(self):
        """ИСПРАВЛЕНИЕ: Проверяем functools.partial вместо lambda"""
        from concurrent.futures import ThreadPoolExecutor

        class TestClass:
            def __init__(self):
                self._executor = ThreadPoolExecutor(max_workers=1)

            @async_wrap
            def sync_method(self, x, y, z = 10):
                return x + y + z

        obj = TestClass()

        try:
            result = await obj.sync_method(5, 3, z=2)
            assert result == 10
        finally:
            obj._executor.shutdown(wait=True)


@pytest.mark.unit
class TestRetryDecorator:
    """Тесты retry декоратора"""

    @pytest.mark.asyncio
    async def test_retry_success_first_attempt(self):
        """Успех с первой попытки"""
        mock_func = AsyncMock(return_value="success")

        @retry(max_attempts=3, delay=0.01)
        async def test_func(self):
            return await mock_func()

        result = await test_func(Mock())

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_success_after_failures(self):
        """Успех после нескольких неудач"""
        mock_func = AsyncMock()
        mock_func.side_effect = [
            Exception("Error 1"),
            Exception("Error 2"),
            "success"
        ]

        @retry(max_attempts=3, delay=0.01)
        async def test_func(self):
            return await mock_func()

        result = await test_func(Mock())

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_all_attempts_fail(self):
        """Все попытки неудачны"""
        mock_func = AsyncMock(side_effect=ValueError("Persistent error"))

        @retry(max_attempts=3, delay=0.01)
        async def test_func(self):
            return await mock_func()

        with pytest.raises(ValueError, match="Persistent error"):
            await test_func(Mock())

        assert mock_func.call_count == 3


@pytest.mark.unit
class TestBybitClientSingleton:
    """Тесты ИСПРАВЛЕННОГО Singleton pattern"""

    def test_singleton_initialization(self):
        """ИСПРАВЛЕНИЕ: Проверяем _initialized флаг"""
        # Сбрасываем singleton перед тестом
        BybitClient._instance = None
        BybitClient._initialized = False

        client1 = BybitClient("key1", "secret1", testnet=True)
        assert BybitClient._initialized is True

        # Второй раз должен вернуть тот же instance
        client2 = BybitClient("key2", "secret2", testnet=False)

        assert client1 is client2
        # Проверяем что ключи НЕ изменились (из-за _initialized)
        assert client2.api_key == "key1"
        assert client2.api_secret == "secret1"

    def test_singleton_reset(self):
        """Тест сброса singleton для изоляции тестов"""
        BybitClient._instance = None
        BybitClient._initialized = False

        client = BybitClient("new_key", "new_secret", testnet=True)
        assert client.api_key == "new_key"


@pytest.mark.unit
class TestBybitClient:
    """Тесты Bybit клиента"""

    def setup_method(self):
        """Setup для каждого теста"""
        BybitClient._instance = None
        BybitClient._initialized = False

    def test_client_initialization(self):
        """Тест инициализации"""
        client = BybitClient("test_key", "test_secret", testnet=True)

        assert client.api_key == "test_key"
        assert client.api_secret == "test_secret"
        assert client.testnet is True
        assert client.request_count == 0
        assert client.error_count == 0

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_get_ticker_price_success(self, mock_http):
        """Тест получения цены тикера"""
        mock_session = Mock()
        mock_session.get_tickers.return_value = {
            "retCode": 0,
            "result": {
                "list": [{
                    "lastPrice": "50000.50"
                }]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            price = await client.get_ticker("spot", "BTCUSDT")

            assert price == 50000.50
            assert client.request_count == 1
            mock_session.get_tickers.assert_called_once()
        finally:
            await client.close()

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_get_ticker_price_error(self, mock_http):
        """Тест ошибки получения цены"""
        mock_session = Mock()
        mock_session.get_tickers.return_value = {
            "retCode": 10001,
            "retMsg": "API error"
        }
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            price = await client.get_ticker("spot", "BTCUSDT")
            assert price is None
            assert client.request_count == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_set_leverage_success(self, mock_http):
        """Тест установки плеча"""
        mock_session = Mock()
        mock_session.set_leverage.return_value = {"retCode": 0}
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            success = await client.set_leverage("linear", "PEPEUSDT", 5)

            assert success is True
            mock_session.set_leverage.assert_called_once_with(
                category="linear",
                symbol="PEPEUSDT",
                buyLeverage="5",
                sellLeverage="5"
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_place_market_order_success(self, mock_http):
        """Тест размещения ордера"""
        mock_session = Mock()
        mock_session.place_order.return_value = {
            "retCode": 0,
            "result": {
                "orderId": "order_123",
                "orderLinkId": "link_123"
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            result = await client.place_market_order(
                category="linear",
                symbol="PEPEUSDT",
                side="Buy",
                qty="100.0",
                take_profit="0.00001096",
                stop_loss="0.00001064"
            )

            assert result is not None
            assert result["orderId"] == "order_123"

            call_args = mock_session.place_order.call_args[1]
            assert call_args["side"] == "Buy"
            assert call_args["qty"] == "100.0"
            assert call_args["takeProfit"] == "0.00001096"
            assert call_args["stopLoss"] == "0.00001064"
        finally:
            await client.close()

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_get_wallet_balance(self, mock_http):
        """Тест получения баланса"""
        mock_session = Mock()
        mock_session.get_wallet_balance.return_value = {
            "retCode": 0,
            "result": {
                "list": [{
                    "totalEquity": "10500.75",
                    "totalAvailableBalance": "10500.75"
                }]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            result = await client.get_wallet_balance()

            assert result is not None
            assert result["list"][0]["totalEquity"] == "10500.75"
        finally:
            await client.close()

    def test_get_stats(self):
        """Тест получения статистики"""
        client = BybitClient("test_key", "test_secret")

        client.request_count = 100
        client.error_count = 5

        stats = client.get_stats()

        assert stats["request_count"] == 100
        assert stats["error_count"] == 5
        assert "5.00%" in stats["error_rate"]

    @pytest.mark.asyncio
    @patch('src.api.bybit_client.HTTP')
    async def test_get_klines_returns_kline_objects(self, mock_http):
        """NEW: Проверяем что get_klines возвращает Kline objects"""
        mock_session = Mock()
        mock_session.get_kline.return_value = {
            "retCode": 0,
            "result": {
                "list": [
                    ["1729900800000", "50000", "50100", "49900", "50050", "1234.5", "12345"],
                    ["1729900500000", "50050", "50150", "49950", "50100", "987.3", "9873"]
                ]
            }
        }
        mock_http.return_value = mock_session

        client = BybitClient("test_key", "test_secret")

        try:
            klines = await client.get_klines("spot", "BTCUSDT", "5", limit=2)

            assert len(klines) == 2

            # ВАЖНО: Проверяем что это Kline objects
            from src.api.common import Kline
            assert isinstance(klines[0], Kline)
            assert klines[0].close == 50050.0
            assert klines[1].close == 50100.0

        finally:
            await client.close()


@pytest.mark.unit
class TestBybitWebSocketClient:
    """NEW: Тесты WebSocket клиента"""

    @pytest.mark.asyncio
    async def test_subscribe_kline(self, mock_ws_client):
        """Тест подписки на kline"""
        from src.api.bybit_websocket_client import BybitWebSocketClient

        ws_client = BybitWebSocketClient("key", "secret", testnet=True)
        callback = AsyncMock()

        # Mock WebSocket
        with patch('src.api.bybit_websocket_client.WebSocket') as mock_ws:
            mock_instance = Mock()
            mock_ws.return_value = mock_instance

            ws_client.subscribe_kline("linear", "PEPEUSDT", "5", callback)

            # Проверяем что WebSocket создан
            mock_ws.assert_called_once()

            # Проверяем что kline_stream вызван
            mock_instance.kline_stream.assert_called_once()
