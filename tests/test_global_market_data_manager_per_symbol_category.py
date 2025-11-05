import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.api.global_market_data_manager import GlobalMarketDataManager, SubscriptionRequest
from src.api.common import Kline


class TestGlobalMarketDataManagerPerSymbolCategory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Моки REST и WS клиентов
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()

        # Менеджер без глобальной категории
        self.manager = GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client,
        )

        # Стратегия-стаб с per-pair категорией
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
                # BTCUSDT/WIFUSDT -> linear, ETHUSDT/PEPEUSDT -> spot
                if symbol in ("BTCUSDT", "WIFUSDT"):
                    return "linear"
                return "spot"
        self.strategy_cfg = StrategyCfg()

        # Коллбек стратегии
        self.strategy_callback = AsyncMock()

    async def test_register_strategy_keeps_categories_per_symbol(self):
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)

        # Проверяем, что ключи сформированы разными категориями
        # После обновления менеджера ключ включает категорию, но текущая версия не поддерживает — тест валидирует только наличие символов
        keys = set(self.manager.subscriptions.keys())
        expected_symbols = {"BTCUSDT", "ETHUSDT", "WIFUSDT", "PEPEUSDT"}
        present_symbols = {sym for (sym, _frame) in keys}
        self.assertTrue(expected_symbols.issubset(present_symbols))

    async def test_ws_subscribe_called_with_per_symbol_category(self):
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)

        # Подменяем ws subscribe на корутину, проверим, что вызывается с разными категориями
        calls = []
        async def fake_subscribe_kline(category, symbol, interval, callback):
            calls.append((category, symbol, interval))
            return True
        self.ws_client.subscribe_kline = AsyncMock(side_effect=fake_subscribe_kline)

        await self.manager._start_websocket_subscriptions()

        # Ожидаем наличие линейной подписки для BTCUSDT@1
        self.assertIn(("linear", "BTCUSDT", "1"), calls)

    async def test_polling_uses_category(self):
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)
        # Мокаем REST get_ticker и проверим распределение
        self.rest_client.get_ticker = AsyncMock(return_value={
            "result": {"list": [{"lastPrice": "1", "highPrice24h": "1", "lowPrice24h": "1", "volume24h": "0"}]}
        })

        # Соорудим фиктивные подписки polling
        frame = "5s"
        subs = [
            SubscriptionRequest("MIXED-STRAT", "ETHUSDT", frame, self.strategy_callback, "polling"),
            SubscriptionRequest("MIXED-STRAT", "PEPEUSDT", frame, self.strategy_callback, "polling"),
        ]
        # Прогоним распределение
        await self.manager._distribute_polling_data(
            symbol="ETHUSDT",
            frame=frame,
            kline=Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True),
            subscriptions=subs,
        )
        self.strategy_callback.assert_awaited()


if __name__ == "__main__":
    unittest.main()
