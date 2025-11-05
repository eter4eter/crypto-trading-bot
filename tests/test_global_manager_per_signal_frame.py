import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.api.global_market_data_manager import GlobalMarketDataManager, SubscriptionRequest
from src.api.common import Kline


class TestGlobalMarketDataManagerPerSignalFrame(pytest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()
        self.manager = GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client,
        )

        # Mock strategy config с разными frame
        class MockSignalConfig:
            def __init__(self, index, frame):
                self.index = index
                self.frame = frame

        class MockStrategyConfig:
            def __init__(self):
                self.name = "multi-frame-test"
                self.signals = {
                    "btc_1s": MockSignalConfig("BTCUSDT", "1s"),
                    "eth_5min": MockSignalConfig("ETHUSDT", "5"),
                }
                self.trade_pairs = ["WIFUSDT"]

            def get_pair_category(self, symbol: str) -> str:
                return "linear"

        self.strategy_config = MockStrategyConfig()
        self.callback = AsyncMock()

    async def test_register_creates_per_signal_frame_subscriptions(self):
        """Проверяем, что register_strategy создаёт подписки для каждого signal.frame"""
        self.manager.register_strategy(self.strategy_config, self.callback)
        
        keys = set(self.manager.subscriptions.keys())
        
        # Ожидаем ключи:
        # btc_1s: (BTCUSDT, 1s, linear) + (WIFUSDT, 1s, linear)
        # eth_5min: (ETHUSDT, 5, linear) + (WIFUSDT, 5, linear)
        expected_keys = {
            ("BTCUSDT", "1s", "linear"),
            ("WIFUSDT", "1s", "linear"),
            ("ETHUSDT", "5", "linear"),
            ("WIFUSDT", "5", "linear"),
        }
        
        self.assertEqual(keys, expected_keys)

    async def test_polling_groups_by_frame_category(self):
        """Проверяем группировку polling по (frame, category)"""
        self.manager.register_strategy(self.strategy_config, self.callback)
        
        # Получаем polling группы
        groups = {}
        for subs in self.manager.subscriptions.values():
            for s in subs:
                if s.source_type == "polling":
                    key = (s.frame, s.market_category)
                    groups.setdefault(key, []).append(s)
        
        # Ожидаем группу ("1s", "linear") с подписками на BTCUSDT и WIFUSDT
        self.assertIn(("1s", "linear"), groups)
        
        # Проверяем, что в группе есть наши символы
        symbols_in_group = {s.symbol for s in groups[("1s", "linear")]}
        self.assertIn("BTCUSDT", symbols_in_group)
        self.assertIn("WIFUSDT", symbols_in_group)

    async def test_ws_subscriptions_per_frame(self):
        """Проверяем WS подписки по frame"""
        self.manager.register_strategy(self.strategy_config, self.callback)
        
        calls = []
        async def mock_subscribe_kline(category, symbol, interval, callback):
            calls.append((category, symbol, interval))
            return True
            
        self.ws_client.subscribe_kline = AsyncMock(side_effect=mock_subscribe_kline)
        
        await self.manager._start_websocket_subscriptions()
        
        # Ожидаем вызовы WebSocket для 5-минутного frame ("5")
        expected_calls = {
            ("linear", "ETHUSDT", "5"),
            ("linear", "WIFUSDT", "5"),
        }
        
        actual_calls = set(calls)
        self.assertTrue(expected_calls.issubset(actual_calls))


if __name__ == "__main__":
    pytest.main([__file__])
