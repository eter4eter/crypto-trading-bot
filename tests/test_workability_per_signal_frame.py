"""
Проверка работоспособности per-signal frame архитектуры
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.api.common import Kline
from src.api.global_market_data_manager import GlobalMarketDataManager
from src.strategy.multi_signal_strategy import MultiSignalStrategy
from src.config import StrategyConfig, SignalConfig


class WorkabilityTest:
    def __init__(self):
        self.rest_client = MagicMock()
        self.ws_client = MagicMock()
        
        # Mock responses
        self.rest_client.get_klines = AsyncMock(return_value=[
            Kline(timestamp=0, open=50000, high=50100, low=49900, close=50000, volume=100, confirm=True),
            Kline(timestamp=1, open=50000, high=50200, low=49800, close=50050, volume=110, confirm=True),
        ])
        
        self.rest_client.get_ticker = AsyncMock(return_value={
            "result": {"list": [{"lastPrice": "50075", "highPrice24h": "51000", "lowPrice24h": "49000", "volume24h": "10000"}]}
        })
        
        self.ws_client.subscribe_kline = AsyncMock(return_value=True)
        
    async def create_test_strategy(self):
        """Создаём тестовую стратегию с разными frame"""
        signals = {
            "btc_1s": SignalConfig(
                index="BTCUSDT",
                frame="1s", 
                tick_window=5,
                index_change_threshold=0.01,
                target=0.05,
                direction=0,
                reverse=0
            ),
            "eth_5min": SignalConfig(
                index="ETHUSDT",
                frame="5",
                tick_window=3, 
                index_change_threshold=0.02,
                target=0.04,
                direction=1,
                reverse=1
            )
        }
        
        config = StrategyConfig(
            name="workability-test",
            trade_pairs=["WIFUSDT"],
            leverage=2,
            tick_window=10,
            price_change_threshold=0.05,
            stop_take_percent=0.005,
            position_size=50,
            direction=0,
            signals=signals,
            enabled=True
        )
        
        return MultiSignalStrategy(config, self.rest_client, self.ws_client)
    
    async def create_manager(self):
        """Создаём менеджер рынковых данных"""
        return GlobalMarketDataManager(
            rest_client=self.rest_client,
            ws_client=self.ws_client
        )
    
    async def test_full_workflow(self):
        print("═" * 60)
        print("📝 SMOKE TEST: per-signal frame архитектура")
        print("═" * 60)
        
        # 1. Создаём компоненты
        manager = await self.create_manager()
        strategy = await self.create_test_strategy()
        
        print(f"✅ Менеджер создан")
        print(f"✅ Стратегия '{strategy.config.name}' создана")
        
        # 2. Прогрузка исторических данных
        print("
📅 Прогрузка исторических данных...")
        history_loaded = await strategy.preload_history()
        print(f"✅ История загружена: {history_loaded}")
        
        # 3. Регистрация стратегии
        print("
📝 Регистрация стратегии...")
        manager.register_strategy(strategy.config, strategy._on_kline_data)
        
        subs_count = len(manager.subscriptions)
        print(f"✅ Подписок создано: {subs_count}")
        
        # 4. Проверяем ключи
        print("
🔍 Ключи подписок:")
        for (symbol, frame, category), subs in manager.subscriptions.items():
            sources = {s.source_type for s in subs}
            print(f"  {symbol:10} @ {frame:3} [{category:6}] - {sources}")
        
        # 5. Проверяем буферы
        print("
📋 Структура буферов:")
        status = strategy.get_status()
        for signal_name, signal_data in status["buffers_status"].items():
            print(f"  {signal_name}:")
            for frame, frame_data in signal_data.items():
                print(f"    {frame}: {frame_data}")
        
        # 6. Симуляция поступления kline
        print("
📊 Симуляция kline...")
        
        captured_signals = []
        def capture_signal(sig):
            captured_signals.append(sig)
        
        strategy.set_strategy_callback(capture_signal)
        
        # Создаём сигнал btc_correlation (1s frame)
        btc_kline = Kline(timestamp=2, open=50050, high=50150, low=50000, close=50100, volume=200, confirm=True)
        wif_1s_kline = Kline(timestamp=2, open=2.0, high=2.05, low=1.98, close=2.001, volume=1000, confirm=True)
        
        await strategy._on_kline_data("BTCUSDT", btc_kline)
        await strategy._on_kline_data("WIFUSDT", wif_1s_kline)
        
        await asyncio.sleep(0.1)
        
        print(f"✅ Обработано kline. Сигналов сгенерировано: {len(captured_signals)}")
        
        if captured_signals:
            sig = captured_signals[-1]
            print(f"   Последний сигнал: {sig.signal_name} -> {sig.action}")
            print(f"   Index change: {sig.index_change:+.3f}%")
            print(f"   Target change: {sig.target_change:+.3f}%")
        
        print("
✅ SMOKE TEST ПРОЙДЕН УСПЕШНО")
        return True


async def main():
    test = WorkabilityTest()
    await test.test_full_workflow()


if __name__ == "__main__":
    asyncio.run(main())
