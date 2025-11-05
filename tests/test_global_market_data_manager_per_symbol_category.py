import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.api.global_market_data_manager import GlobalMarketDataManager, SubscriptionRequest
from src.api.common import Kline


class TestGlobalMarketDataManagerPerSymbolCategory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()
        self.manager = GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client,
        )

        class Sig:
            def __init__(self, index, frame):
                self.index = index
                self.frame = frame
        class StrategyCfg:
            def __init__(self):
                self.name = "MIXED-STRAT"
                self.signals = {
                    "sig_ws": Sig("BTCUSDT", "1"),   # WS
                    "sig_poll": Sig("ETHUSDT", "5s"), # polling
                }
                self.trade_pairs = ["WIFUSDT", "PEPEUSDT"]
            def get_pair_category(self, symbol: str) -> str:
                if symbol in ("BTCUSDT", "WIFUSDT"):
                    return "linear"
                return "spot"
        self.strategy_cfg = StrategyCfg()
        self.callback = AsyncMock()

    async def test_register_creates_keys_with_categories(self):
        self.manager.register_strategy(self.strategy_cfg, self.callback)
        keys = set(self.manager.subscriptions.keys())
        # Проверим наличие разных категорий в ключах
        self.assertIn(("BTCUSDT", "1", "linear"), keys)
        self.assertIn(("ETHUSDT", "5s", "spot"), keys)
        self.assertIn(("WIFUSDT", "1", "linear"), keys)
        self.assertIn(("PEPEUSDT", "1", "spot"), keys)

    async def test_ws_subscribe_uses_category(self):
        self.manager.register_strategy(self.strategy_cfg, self.callback)
        calls = []
        async def fake_subscribe_kline(category, symbol, interval, callback):
            calls.append((category, symbol, interval))
            return True
        self.ws_client.subscribe_kline = AsyncMock(side_effect=fake_subscribe_kline)
        await self.manager._start_websocket_subscriptions()
        self.assertIn(("linear", "BTCUSDT", "1"), calls)
        # WIFUSDT@1 linear тоже должен быть среди подписок
        self.assertIn(("linear", "WIFUSDT", "1"), calls)

    async def test_polling_grouped_by_frame_and_category(self):
        self.manager.register_strategy(self.strategy_cfg, self.callback)
        # Сформируем группу polling из текущих подписок менеджера
        groups = {}
        for subs in self.manager.subscriptions.values():
            for s in subs:
                if s.source_type == "polling":
                    groups.setdefault((s.frame, s.market_category), []).append(s)
        self.assertIn(("5s", "spot"), groups)

    async def test_distribute_polling_respects_category(self):
        frame = "5s"
        category = "spot"
        subs = [
            SubscriptionRequest("MIXED-STRAT", "ETHUSDT", frame, category, self.callback, "polling"),
            SubscriptionRequest("MIXED-STRAT", "PEPEUSDT", frame, category, self.callback, "polling"),
        ]
        kline = Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True)
        await self.manager._distribute_polling_data("ETHUSDT", frame, category, kline, subs)
        self.callback.assert_awaited()


if __name__ == "__main__":
    unittest.main()
}