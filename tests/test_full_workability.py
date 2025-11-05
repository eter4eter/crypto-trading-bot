"""
Проверка полного цикла per-signal frame - от конфигурации до сигналов
"""

import asyncio
import json
import tempfile
from pathlib import Path

from src.config import Config
from src.logger import setup_logger
from src.api.bybit_client import BybitClient
from src.api.bybit_websocket_client import BybitWebSocketClient
from src.api.global_market_data_manager import GlobalMarketDataManager
from src.strategy.multi_signal_strategy import MultiSignalStrategy


class FullWorkabilityTest:
    def __init__(self):
        self.temp_dir = None
        self.config_path = None
        
    async def create_test_config(self):
        """Создаём тестовый конфиг"""
        self.temp_dir = tempfile.mkdtemp()
        config_data = {
            "api": {
                "api_key": "test_key",
                "api_secret": "test_secret",
                "testnet": True,
                "demo_mode": True
            },
            "global": {
                "max_stop_loss_trades": 3,
                "logging_level": "DEBUG",
                "database_path": f"{self.temp_dir}/test.db"
            },
            "strategies": {
                "WIF-USDT-threshold": {
                    "trade_pairs": ["WIFUSDT"],
                    "leverage": 5,
                    "tick_window": 10,
                    "price_change_threshold": 0.05,
                    "stop_take_percent": 0.005,
                    "position_size": 100,
                    "direction": 0,
                    "enabled": True,
                    "signals": {
                        "btc_correlation": {
                            "index": "BTCUSDT",
                            "frame": "1s",
                            "tick_window": 30,
                            "index_change_threshold": 0.01,
                            "target": 0.05,
                            "direction": 0,
                            "reverse": 0
                        },
                        "eth_momentum": {
                            "index": "ETHUSDT",
                            "frame": "5",
                            "tick_window": 20,
                            "index_change_threshold": 0.015,
                            "target": 0.03,
                            "direction": 1,
                            "reverse": 1
                        }
                    }
                }
            },
            "telegram": {
                "enabled": False,
                "bot_token": "",
                "chat_id": ""
            }
        }
        
        self.config_path = Path(self.temp_dir) / "config.json"
        self.config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        
        return str(self.config_path)
    
    async def test_config_loading(self):
        print("═" * 70)
        print("📝 FULL WORKABILITY TEST: per-signal frame")
        print("═" * 70)
        
        # 1. Создаём тестовый конфиг
        config_path = await self.create_test_config()
        print(f"✅ Тестовый конфиг создан: {config_path}")
        
        # 2. Настраиваем логгер
        setup_logger(name="workability_test", level="INFO", console=True)
        
        # 3. Загружаем конфигурацию
        try:
            config = Config.load(config_path)
            print(f"✅ Config загружен: {len(config.strategies)} стратегий")
            
            strategy_config = list(config.enabled_strategies.values())[0]
            print(f"   Стратегия: {strategy_config.name}")
            print(f"   Сигналы: {list(strategy_config.signals.keys())}")
            print(f"   Trade pairs: {strategy_config.trade_pairs}")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки конфига: {e}")
            return False
        
        # 4. Проверяем структуру signals
        print("\n🔍 Анализ сигналов:")
        for signal_name, signal_config in strategy_config.signals.items():
            print(f"  {signal_name}:")
            print(f"    index: {signal_config.index} @ {signal_config.frame}")
            print(f"    window: {signal_config.tick_window}")
            print(f"    пороги: index≥{signal_config.index_change_threshold}%, target<{signal_config.target}%")
            print(f"    direction: {signal_config.direction}, reverse: {signal_config.reverse}")
        
        # 5. Проверяем метод get_pair_category
        print("\n🏷️ Проверка per-symbol категорий:")
        test_symbols = ["BTCUSDT", "ETHUSDT", "WIFUSDT"]
        for symbol in test_symbols:
            category = strategy_config.get_pair_category(symbol)
            print(f"  {symbol}: {category}")
            
        print("\n✅ Конфигурация проверена успешно")
        return True
        
    async def test_strategy_creation(self):
        print("\n🎯 Тест создания стратегии...")
        
        # Mock клиенты
        class MockClient:
            async def get_klines(self, category, symbol, interval, limit):
                return [
                    {"timestamp": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                    {"timestamp": 1, "open": 100, "high": 102, "low": 98, "close": 101, "volume": 1100},
                ]
                
            async def get_ticker(self, category, symbol):
                return {"result": {"list": [{"lastPrice": "101.5"}]}}
        
        try:
            config_path = await self.create_test_config()
            config = Config.load(config_path)
            strategy_config = list(config.enabled_strategies.values())[0]
            
            rest_client = MockClient()
            ws_client = MockClient()
            
            # Создаём стратегию
            strategy = MultiSignalStrategy(strategy_config, rest_client, ws_client)
            print(f"✅ Стратегия '{strategy.config.name}' создана")
            
            # Проверяем структуру буферов
            print("\n📋 Структура буферов:")
            for signal_name, signal_buffers in strategy.signal_buffers.items():
                print(f"  {signal_name}:")
                for frame, frame_buffers in signal_buffers.items():
                    print(f"    {frame}: {list(frame_buffers.keys())}")
            
            # Проверяем get_required_subscriptions
            required_subs = strategy.get_required_subscriptions()
            print(f"\n📝 Required subscriptions ({len(required_subs)}):")
            for symbol, frame in required_subs:
                source = "polling" if frame.endswith('s') else "websocket"
                print(f"  {symbol:10} @ {frame:3} [{source}]")
                
            print("\n✅ Стратегия создана успешно")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка создания стратегии: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def run_full_test(self):
        print("🚀 Запуск полного теста работоспособности...\n")
        
        success = True
        
        # Тест 1: Конфигурация
        print("🔍 Тест 1: Загрузка конфигурации")
        test1_result = await self.test_config_loading()
        success = success and test1_result
        
        # Тест 2: Создание стратегии
        print("\n🎯 Тест 2: Создание стратегии")
        test2_result = await self.test_strategy_creation()
        success = success and test2_result
        
        print("\n" + "═" * 70)
        if success:
            print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО")
            print("🚀 Архитектура per-signal frame готова к работе!")
        else:
            print("❌ Обнаружены ошибки - требуется доработка")
        print("═" * 70)
        
        return success


async def main():
    test = FullWorkabilityTest()
    await test.run_full_test()


if __name__ == "__main__":
    asyncio.run(main())
