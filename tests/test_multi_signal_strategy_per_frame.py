import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from collections import deque
from dataclasses import dataclass
from typing import Literal

from src.api.common import Kline
from src.strategy.multi_signal_strategy import MultiSignalStrategy, SignalResult


class MockRestClient:
    def __init__(self):
        self.get_klines_calls = []
        self.get_ticker_calls = []

    async def get_klines(self, category, symbol, interval, limit):
        self.get_klines_calls.append((category, symbol, interval, limit))
        # Возвращаем 2 свечи для прогрева
        return [
            Kline(timestamp=0, open=100, high=101, low=99, close=100, volume=1000, confirm=True),
            Kline(timestamp=1, open=100, high=102, low=98, close=101, volume=1100, confirm=True),
        ]

    async def get_ticker(self, category, symbol):
        self.get_ticker_calls.append((category, symbol))
        return {
            "result": {
                "list": [{
                    "lastPrice": "101.5",
                    "highPrice24h": "105",
                    "lowPrice24h": "95",
                    "volume24h": "10000"
                }]
            }
        }


@dataclass
class MockSignalConfig:
    index: str
    frame: str
    tick_window: int
    index_change_threshold: float
    target: float
    direction: Literal[-1, 0, 1]
    reverse: Literal[0, 1]


@dataclass
class MockStrategyConfig:
    name: str
    trade_pairs: list[str]
    leverage: int
    tick_window: int
    price_change_threshold: float
    stop_take_percent: float
    position_size: int
    direction: Literal[-1, 0, 1]
    signals: dict[str, MockSignalConfig]
    enabled: bool = True

    def get_market_category(self) -> str:
        return "spot" if self.leverage == 1 else "linear"

    def get_pair_category(self, symbol: str) -> str:
        # Пример смешанной категоризации
        if symbol == "ETHUSDT" and self.name == "spot-demo":
            return "spot"
        return self.get_market_category()

    def should_take_signal(self, signal_action: str) -> bool:
        if self.direction == 0:
            return True
        elif self.direction == 1:
            return signal_action == "Buy"
        elif self.direction == -1:
            return signal_action == "Sell"
        return False


class TestMultiSignalStrategyPerSignalFrame(pytest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rest_client = MockRestClient()
        self.ws_client = MagicMock()
        
        # Стратегия с разными frame
        self.strategy_config = MockStrategyConfig(
            name="test-multi-frame",
            trade_pairs=["WIFUSDT"],
            leverage=2,
            tick_window=10,
            price_change_threshold=0.05,
            stop_take_percent=0.005,
            position_size=50,
            direction=0,
            signals={
                "btc_1s": MockSignalConfig(
                    index="BTCUSDT",
                    frame="1s",
                    tick_window=5,
                    index_change_threshold=0.01,
                    target=0.05,
                    direction=0,
                    reverse=0
                ),
                "eth_5min": MockSignalConfig(
                    index="ETHUSDT",
                    frame="5",
                    tick_window=3,
                    index_change_threshold=0.02,
                    target=0.04,
                    direction=1,
                    reverse=1
                )
            }
        )
        
        self.strategy = MultiSignalStrategy(self.strategy_config, self.rest_client, self.ws_client)

    async def test_buffer_structure_per_signal_frame(self):
        """Проверяем, что буферы созданы по (signal_name -> frame -> symbol)"""
        self.assertIn("btc_1s", self.strategy.signal_buffers)
        self.assertIn("eth_5min", self.strategy.signal_buffers)
        
        # btc_1s: frame="1s"
        btc_buf = self.strategy.signal_buffers["btc_1s"]
        self.assertIn("1s", btc_buf)
        self.assertIn("BTCUSDT", btc_buf["1s"])  # index
        self.assertIn("WIFUSDT", btc_buf["1s"])   # target
        
        # eth_5min: frame="5"
        eth_buf = self.strategy.signal_buffers["eth_5min"]
        self.assertIn("5", eth_buf)
        self.assertIn("ETHUSDT", eth_buf["5"])   # index
        self.assertIn("WIFUSDT", eth_buf["5"])    # target

    async def test_preload_history_calls_correct_frames(self):
        """Проверяем, что preload_history вызывает get_klines с правильными frame"""
        await self.strategy.preload_history()
        
        calls = self.rest_client.get_klines_calls
        # Ожидаем вызовы:
        # - BTCUSDT @ 1s (btc_1s signal index)
        # - WIFUSDT @ 1s (btc_1s signal target)
        # - ETHUSDT @ 5 (eth_5min signal index)
        # - WIFUSDT @ 5 (eth_5min signal target)
        
        call_keys = {(symbol, interval) for (category, symbol, interval, limit) in calls}
        self.assertIn(("BTCUSDT", "1s"), call_keys)
        self.assertIn(("WIFUSDT", "1s"), call_keys)
        self.assertIn(("ETHUSDT", "5"), call_keys)
        self.assertIn(("WIFUSDT", "5"), call_keys)

    async def test_kline_updates_correct_buffers(self):
        """Проверяем, что kline обновляют правильные буферы"""
        await self.strategy.preload_history()
        
        # BTC kline - должен обновить буфер btc_1s["1s"]["BTCUSDT"]
        btc_kline = Kline(timestamp=2, open=101, high=103, low=100, close=102, volume=1200, confirm=True)
        await self.strategy._on_kline_data("BTCUSDT", btc_kline)
        
        self.assertEqual(self.strategy.signal_buffers["btc_1s"]["1s"]["BTCUSDT"][-1], 102.0)
        
        # ETH kline - должен обновить буфер eth_5min["5"]["ETHUSDT"]
        eth_kline = Kline(timestamp=3, open=3000, high=3020, low=2980, close=3010, volume=500, confirm=True)
        await self.strategy._on_kline_data("ETHUSDT", eth_kline)
        
        self.assertEqual(self.strategy.signal_buffers["eth_5min"]["5"]["ETHUSDT"][-1], 3010.0)

    async def test_signal_generation_with_different_frames(self):
        """Проверяем генерацию сигнала при разных frame"""
        await self.strategy.preload_history()
        
        captured_signals = []
        def capture_signal(sig: SignalResult):
            captured_signals.append(sig)
            
        self.strategy.set_strategy_callback(capture_signal)
        
        # Создаём условия для сигнала btc_1s:
        # - BTCUSDT рост на 1.2% (> 0.01% threshold)
        # - WIFUSDT рост на 0.03% (< 0.05% target)
        btc_impulse = Kline(timestamp=2, open=100, high=102, low=100, close=101.2, volume=1000, confirm=True)
        wif_follow = Kline(timestamp=2, open=10, high=10.1, low=9.9, close=10.003, volume=500, confirm=True)
        
        await self.strategy._on_kline_data("BTCUSDT", btc_impulse)
        await self.strategy._on_kline_data("WIFUSDT", wif_follow)
        
        # Проверяем, что сигнал сгенерирован
        await asyncio.sleep(0.1)  # Даём время на обработку
        
        self.assertGreater(len(captured_signals), 0)
        sig = captured_signals[-1]
        self.assertEqual(sig.signal_name, "btc_1s")
        self.assertEqual(sig.strategy_name, "test-multi-frame")
        self.assertEqual(sig.index_pair, "BTCUSDT")
        self.assertIn("WIFUSDT", sig.target_pairs)

    def test_get_required_subscriptions(self):
        """Проверяем, что get_required_subscriptions возвращает правильные (symbol, frame) пары"""
        subs = self.strategy.get_required_subscriptions()
        
        # Ожидаем:
        # - (BTCUSDT, 1s) для btc_1s
        # - (WIFUSDT, 1s) для btc_1s
        # - (ETHUSDT, 5) для eth_5min
        # - (WIFUSDT, 5) для eth_5min
        
        expected = {("BTCUSDT", "1s"), ("WIFUSDT", "1s"), ("ETHUSDT", "5"), ("WIFUSDT", "5")}
        actual = set(subs)
        
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    pytest.main([__file__])
