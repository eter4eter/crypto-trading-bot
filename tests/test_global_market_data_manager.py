from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.api.common import Kline
from src.api.global_market_data_manager import GlobalMarketDataManager, SubscriptionRequest


class TestGlobalMarketDataManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()
        self.manager = GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client,
        )
        # Стратегия-стаб
        class Sig:
            def __init__(self, index: str, frame: str) -> None:
                self.index = index
                self.frame = frame
        class StrategyCfg:
            def __init__(self) -> None:
                self.name = "TEST-STRAT"
                self.signals = {
                    "sig_1": Sig("BTCUSDT", "1"),
                    "sig_2": Sig("ETHUSDT", "5s"),
                }
                self.trade_pairs = ["WIFUSDT", "PEPEUSDT"]
            def get_pair_category(self, symbol: str) -> str:
                return "linear"
        self.strategy_cfg = StrategyCfg()
        self.strategy_callback = AsyncMock()

    async def test_register_strategy_creates_subscriptions(self) -> None:
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)
        keys = set(self.manager.subscriptions.keys())
        expected_syms = {"BTCUSDT", "ETHUSDT", "WIFUSDT", "PEPEUSDT"}
        present_syms = {sym for (sym, _frame, _cat) in keys}
        self.assertTrue(expected_syms.issubset(present_syms))
        for (symbol, frame, category), subs in self.manager.subscriptions.items():
            for s in subs:
                if frame.endswith("s"):
                    self.assertEqual(s.source_type, "polling")
                else:
                    self.assertEqual(s.source_type, "websocket")

    async def test_start_initializes_ws_and_polling(self) -> None:
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)
        async def fake_subscribe_kline(category: str, symbol: str, interval: str, callback: object) -> bool:
            return True
        self.ws_client.subscribe_kline = AsyncMock(side_effect=fake_subscribe_kline)
        await self.manager.start()
        self.ws_client.subscribe_kline.assert_any_await(
            category="linear", symbol="BTCUSDT", interval="1", callback=self.manager._ws_callback
        )
        self.assertGreaterEqual(len(self.manager.polling_tasks), 1)
        await self.manager.stop()

    async def test_ws_callback_distributes_to_registered_callbacks(self) -> None:
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)
        self.manager.active_ws_subscriptions.add(("BTCUSDT", "1", "linear"))
        kline = Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True)
        await self.manager._ws_callback("BTCUSDT", kline)
        self.strategy_callback.assert_awaited()

    async def test_polling_distributes_data(self) -> None:
        frame = "5s"
        category = "linear"
        subs = [
            SubscriptionRequest("TEST-STRAT", "ETHUSDT", frame, category, self.strategy_callback, "polling")
        ]
        kline = Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True)
        await self.manager._distribute_polling_data("ETHUSDT", frame, category, kline, subs)
        self.strategy_callback.assert_awaited()


if __name__ == "__main__":
    unittest.main()
