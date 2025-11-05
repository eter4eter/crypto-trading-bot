import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.api.global_market_data_manager import GlobalMarketDataManager, SubscriptionRequest
from src.api.common import Kline


class TestGlobalMarketDataManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Моки REST и WS клиентов
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()

        # Категория рынка (не важно для тестов логики)
        self.manager = GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client,
            market_category="linear",
        )

        # Простейший StrategyConfig-стаб
        class Sig:
            def __init__(self, index, frame):
                self.index = index
                self.frame = frame
        class StrategyCfg:
            def __init__(self):
                self.name = "TEST-STRAT"
                self.signals = {
                    "sig_1": Sig("BTCUSDT", "1"),       # WS
                    "sig_2": Sig("ETHUSDT", "5s"),     # polling
                }
                self.trade_pairs = ["WIFUSDT", "PEPEUSDT"]
            def get_market_category(self):
                return "linear"
        self.strategy_cfg = StrategyCfg()

        # Коллбек стратегии
        self.strategy_callback = AsyncMock()

    async def test_register_strategy_creates_subscriptions(self):
        # Регистрируем стратегию
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)

        # Должны появиться ключи BTCUSDT@1, ETHUSDT@5s, WIFUSDT@1, WIFUSDT@5s, PEPEUSDT@1, PEPEUSDT@5s
        keys = set(self.manager.subscriptions.keys())
        expected = {
            ("BTCUSDT", "1"),
            ("ETHUSDT", "5s"),
            ("WIFUSDT", "1"),
            ("WIFUSDT", "5s"),
            ("PEPEUSDT", "1"),
            ("PEPEUSDT", "5s"),
        }
        self.assertTrue(expected.issubset(keys))

        # Проверяем тип источника
        for (symbol, frame), subs in self.manager.subscriptions.items():
            for s in subs:
                if frame.endswith("s"):
                    self.assertEqual(s.source_type, "polling")
                else:
                    self.assertEqual(s.source_type, "websocket")

    async def test_start_initializes_ws_and_polling(self):
        # Регистрируем стратегию и запускаем менеджер
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)

        # Подменяем ws subscribe на корутину
        async def fake_subscribe_kline(category, symbol, interval, callback):
            return True
        self.ws_client.subscribe_kline = AsyncMock(side_effect=fake_subscribe_kline)

        await self.manager.start()

        # Проверяем, что были запрошены WS подписки хотя бы для BTCUSDT@1
        self.ws_client.subscribe_kline.assert_any_await(
            category="linear", symbol="BTCUSDT", interval="1", callback=self.manager._ws_callback
        )

        # Проверяем, что созданы polling задачи
        self.assertGreaterEqual(len(self.manager.polling_tasks), 1)

        await self.manager.stop()

    async def test_ws_callback_distributes_to_registered_callbacks(self):
        # Регистрируем стратегию и запускаем менеджер
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)
        # Симулируем активную WS подписку на BTCUSDT@1
        self.manager.active_ws_subscriptions.add(("BTCUSDT", "1"))

        # Подтвержденный kline
        kline = Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True)

        await self.manager._ws_callback("BTCUSDT", kline)

        # Проверяем, что callback стратегии был вызван
        self.strategy_callback.assert_awaited()

    async def test_polling_distributes_data(self):
        # Регистрируем стратегию
        self.manager.register_strategy(self.strategy_cfg, self.strategy_callback)

        # Сформируем группу polling для 5s
        frame = "5s"
        subs = [
            SubscriptionRequest("TEST-STRAT", "ETHUSDT", frame, self.strategy_callback, "polling"),
            SubscriptionRequest("TEST-STRAT", "WIFUSDT", frame, self.strategy_callback, "polling"),
        ]

        # Мокаем REST get_ticker -> возвращает dict в формате WS
        self.rest_client.get_ticker = AsyncMock(return_value={
            "result": {"list": [{"lastPrice": "1", "highPrice24h": "1", "lowPrice24h": "1", "volume24h": "0"}]}
        })

        # Выполним один шаг polling цикла (без запуска бесконечного цикла)
        await self.manager._distribute_polling_data(
            symbol="ETHUSDT",
            frame=frame,
            kline=Kline(timestamp=0, open=1, high=1, low=1, close=1, volume=0, confirm=True),
            subscriptions=subs,
        )

        # Ожидаем, что callback был вызван
        self.strategy_callback.assert_awaited()


if __name__ == "__main__":
    unittest.main()
