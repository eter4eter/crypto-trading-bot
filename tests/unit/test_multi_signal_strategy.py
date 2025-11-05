"""
Тесты для MultiSignalStrategy согласно ТЗ
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from collections import deque

from src.config import StrategyConfig, SignalConfig
from src.strategy.multi_signal_strategy import MultiSignalStrategy, SignalResult
from src.api.common import Kline


@pytest.fixture
def strategy_config_with_signals():
    """Конфигурация стратегии с двумя сигналами"""
    signals = {
        "btc_signal": SignalConfig(
            index="BTCUSDT",
            frame="1",
            tick_window=5,
            index_change_threshold=1.0,
            target=0.8,
            direction=0,
            reverse=0
        ),
        "eth_reverse": SignalConfig(
            index="ETHUSDT",
            frame="5",
            tick_window=0,  # Последняя свеча
            index_change_threshold=1.5,
            target=1.0,
            direction=1,  # Только рост
            reverse=1     # Инверсия
        )
    }
    
    return StrategyConfig(
        name="test-strategy",
        trade_pairs=["WIFUSDT"],
        leverage=5,
        tick_window=10,
        price_change_threshold=0.05,
        stop_take_percent=0.01,
        position_size=100,
        direction=0,
        signals=signals,
        enabled=True
    )


@pytest.mark.unit
@pytest.mark.strategy
class TestMultiSignalStrategy:
    
    @pytest.mark.asyncio
    async def test_initialization(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест инициализации стратегии"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        assert strategy.config.name == "test-strategy"
        assert len(strategy.config.signals) == 2
        assert "btc_signal" in strategy.signal_buffers
        assert "eth_reverse" in strategy.signal_buffers
        
        # Проверяем размеры буферов
        assert strategy.signal_buffers["btc_signal"]["index_prices"].maxlen == 5
        assert strategy.signal_buffers["eth_reverse"]["index_prices"].maxlen == 2  # tick_window=0
        
    @pytest.mark.asyncio
    async def test_preload_history(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест предзагрузки исторических данных"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        success = await strategy.preload_history()
        assert success is True
        
        # Проверяем что буферы заполнены
        assert len(strategy.signal_buffers["btc_signal"]["index_prices"]) == 4  # n-1 для tick_window=5
        assert len(strategy.signal_buffers["eth_reverse"]["index_prices"]) == 1  # предпоследняя для tick_window=0
        
        # Проверяем вызовы API
        assert mock_bybit_client.get_klines.call_count >= 4  # 2 signals * 2 pairs (index + target)
    
    @pytest.mark.asyncio
    async def test_signal_generation_btc_correlation(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест генерации сигнала по BTC корреляции"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        # Мок каллбэка
        callback_called = False
        received_signal = None
        
        def test_callback(signal_result):
            nonlocal callback_called, received_signal
            callback_called = True
            received_signal = signal_result
            
        strategy.set_strategy_callback(test_callback)
        
        # Заполняем буферы для btc_signal
        btc_buffer = strategy.signal_buffers["btc_signal"]
        wif_buffer = btc_buffer["target_prices"]["WIFUSDT"]
        
        # Начальные цены
        initial_btc = 50000.0
        initial_wif = 1.0
        
        # Заполняем буферы (tick_window=5, нужно 5 значений)
        for i in range(5):
            btc_buffer["index_prices"].append(initial_btc + i * 10)  # Медленный рост
            wif_buffer.append(initial_wif + i * 0.001)  # Медленный рост
        
        # Добавляем сильное изменение BTC (+2%) для срабатывания
        final_btc = initial_btc * 1.02  # +2% > threshold 1.0%
        final_wif = initial_wif * 1.005  # +0.5% < target 0.8%
        
        btc_buffer["index_prices"].append(final_btc)
        wif_buffer.append(final_wif)
        
        # Проверяем сигнал
        await strategy._check_signal("btc_signal", strategy.config.signals["btc_signal"])
        
        # Проверяем что сигнал сгенерирован
        assert strategy.signals_generated == 1
    
    @pytest.mark.asyncio 
    async def test_tick_window_zero_logic(self, mock_bybit_client, mock_ws_client):
        """Тест логики tick_window=0 (последняя свеча)"""
        signals = {
            "last_candle_signal": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=0,  # Только последняя свеча
                index_change_threshold=1.0,
                target=0.5,
                direction=0,
                reverse=0
            )
        }
        
        config = StrategyConfig(
            name="tick-window-zero",
            trade_pairs=["ETHUSDT"],
            leverage=3,
            tick_window=0,
            price_change_threshold=0.1,
            stop_take_percent=0.01,
            position_size=50,
            direction=0,
            signals=signals,
            enabled=True
        )
        
        strategy = MultiSignalStrategy(config, mock_bybit_client, mock_ws_client)
        
        # Заполняем буфер 2 значениями
        buffer = strategy.signal_buffers["last_candle_signal"]
        
        # Первая свеча
        buffer["index_prices"].append(50000.0)
        buffer["target_prices"]["ETHUSDT"].append(3000.0)
        
        # Вторая свеча с сильным ростом BTC (+1.5%)
        buffer["index_prices"].append(50750.0)  # +1.5% > 1.0% threshold
        buffer["target_prices"]["ETHUSDT"].append(3010.0)  # +0.33% < 0.5% target
        
        # Мок каллбэка
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        # Проверяем сигнал
        await strategy._check_signal("last_candle_signal", signals["last_candle_signal"])
        
        # Проверяем что сигнал сгенерирован
        assert len(signals_received) == 1
        assert signals_received[0].action == "Buy"
        assert abs(signals_received[0].index_change - 1.5) < 0.01
        
    @pytest.mark.asyncio
    async def test_direction_filtering(self, mock_bybit_client, mock_ws_client):
        """Тест фильтрации по direction"""
        # Стратегия только для LONG (direction=1)
        signals = {
            "long_only": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=0,
                index_change_threshold=1.0,
                target=0.5,
                direction=1,  # Только рост index
                reverse=0
            )
        }
        
        config = StrategyConfig(
            name="long-only-test",
            trade_pairs=["ETHUSDT"],
            leverage=2,
            tick_window=0,
            price_change_threshold=0.1,
            stop_take_percent=0.01,
            position_size=100,
            direction=1,  # Стратегия только LONG
            signals=signals,
            enabled=True
        )
        
        strategy = MultiSignalStrategy(config, mock_bybit_client, mock_ws_client)
        
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        buffer = strategy.signal_buffers["long_only"]
        
        # Тест 1: BTC падает (-1.5%), direction=1 -> сигнал НЕ должен пройти
        buffer["index_prices"].extend([50000.0, 49250.0])  # -1.5%
        buffer["target_prices"]["ETHUSDT"].extend([3000.0, 3005.0])  # +0.17%
        
        await strategy._check_signal("long_only", signals["long_only"])
        assert len(signals_received) == 0  # Нет сигнала из-за direction
        
        # Очищаем буферы
        buffer["index_prices"].clear()
        buffer["target_prices"]["ETHUSDT"].clear()
        
        # Тест 2: BTC растет (+1.5%), direction=1 -> сигнал должен пройти
        buffer["index_prices"].extend([50000.0, 50750.0])  # +1.5%
        buffer["target_prices"]["ETHUSDT"].extend([3000.0, 3010.0])  # +0.33%
        
        await strategy._check_signal("long_only", signals["long_only"])
        assert len(signals_received) == 1  # Сигнал прошел
        assert signals_received[0].action == "Buy"
        
    @pytest.mark.asyncio
    async def test_reverse_logic(self, mock_bybit_client, mock_ws_client):
        """Тест логики reverse"""
        signals = {
            "reverse_signal": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=0,
                index_change_threshold=1.0,
                target=0.5,
                direction=0,
                reverse=1  # Инверсия
            )
        }
        
        config = StrategyConfig(
            name="reverse-test",
            trade_pairs=["ETHUSDT"],
            leverage=2,
            tick_window=0,
            price_change_threshold=0.1,
            stop_take_percent=0.01,
            position_size=100,
            direction=0,
            signals=signals,
            enabled=True
        )
        
        strategy = MultiSignalStrategy(config, mock_bybit_client, mock_ws_client)
        
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        buffer = strategy.signal_buffers["reverse_signal"]
        
        # BTC растет (+1.5%), но reverse=1 -> должно сгенерировать Sell
        buffer["index_prices"].extend([50000.0, 50750.0])  # +1.5%
        buffer["target_prices"]["ETHUSDT"].extend([3000.0, 3010.0])  # +0.33%
        
        await strategy._check_signal("reverse_signal", signals["reverse_signal"])
        
        assert len(signals_received) == 1
        assert signals_received[0].action == "Sell"  # Инвертировано с Buy на Sell
        
    @pytest.mark.asyncio
    async def test_target_threshold_exceeded(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест превышения target порога"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        buffer = strategy.signal_buffers["btc_signal"]
        wif_buffer = buffer["target_prices"]["WIFUSDT"]
        
        # Заполняем буферы
        for i in range(5):
            buffer["index_prices"].append(50000.0 + i * 10)
            wif_buffer.append(1.0 + i * 0.001)
        
        # BTC +2%, но WIF слишком сильно (+1.5% > target 0.8%)
        buffer["index_prices"].append(51000.0)  # +2%
        wif_buffer.append(1.015)  # +1.5% > 0.8% target
        
        await strategy._check_signal("btc_signal", strategy.config.signals["btc_signal"])
        
        # Сигнал не должен пройти из-за превышения target
        assert len(signals_received) == 0
        assert strategy.signals_generated == 0
        
    @pytest.mark.asyncio
    async def test_no_correlation_rejection(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест отклонения при отсутствии корреляции"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        buffer = strategy.signal_buffers["btc_signal"]
        wif_buffer = buffer["target_prices"]["WIFUSDT"]
        
        # Заполняем буферы
        for i in range(5):
            buffer["index_prices"].append(50000.0 + i * 10)
            wif_buffer.append(1.0 + i * 0.001)
        
        # BTC растет (+2%), но WIF падает (-0.5%) - НЕТ корреляции
        buffer["index_prices"].append(51000.0)   # +2%
        wif_buffer.append(0.995)                 # -0.5%
        
        await strategy._check_signal("btc_signal", strategy.config.signals["btc_signal"])
        
        # Сигнал не должен пройти из-за отсутствия корреляции
        assert len(signals_received) == 0
        assert strategy.signals_generated == 0
        
    @pytest.mark.asyncio
    async def test_multiple_signals_in_strategy(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест обработки нескольких сигналов в одной стратегии"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        signals_received = []
        strategy.set_strategy_callback(lambda sig: signals_received.append(sig))
        
        # Триггерим первый сигнал (btc_signal)
        btc_buffer = strategy.signal_buffers["btc_signal"]
        for i in range(5):
            btc_buffer["index_prices"].append(50000.0 + i * 10)
            btc_buffer["target_prices"]["WIFUSDT"].append(1.0 + i * 0.001)
            
        # Сильное изменение BTC
        btc_buffer["index_prices"].append(51000.0)  # +2%
        btc_buffer["target_prices"]["WIFUSDT"].append(1.005)  # +0.5%
        
        await strategy._check_signal("btc_signal", strategy.config.signals["btc_signal"])
        
        # Триггерим второй сигнал (eth_reverse) - он с tick_window=0
        eth_buffer = strategy.signal_buffers["eth_reverse"]
        
        # ETH растет (+2% > 1.5% threshold), direction=1, reverse=1 -> Sell
        eth_buffer["index_prices"].extend([3000.0, 3060.0])  # +2%
        eth_buffer["target_prices"]["WIFUSDT"].extend([1.0, 1.005])  # +0.5%
        
        await strategy._check_signal("eth_reverse", strategy.config.signals["eth_reverse"])
        
        # Оба сигнала должны сработать
        assert len(signals_received) == 2
        assert strategy.signals_generated == 2
        
        # Проверяем разные действия
        actions = [sig.action for sig in signals_received]
        assert "Buy" in actions  # От btc_signal (reverse=0)
        assert "Sell" in actions  # От eth_reverse (reverse=1)
        
    @pytest.mark.asyncio
    async def test_status_reporting(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест отчетов о статусе стратегии"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        status = strategy.get_status()
        
        assert status["name"] == "test-strategy"
        assert status["signals_count"] == 2
        assert status["signals_generated"] == 0
        assert status["trade_pairs"] == ["WIFUSDT"]
        assert status["leverage"] == 5
        assert "buffers_status" in status
        assert "btc_signal" in status["buffers_status"]
        assert "eth_reverse" in status["buffers_status"]
        
    @pytest.mark.asyncio
    async def test_reset_buffers(self, strategy_config_with_signals, mock_bybit_client, mock_ws_client):
        """Тест сброса буферов"""
        strategy = MultiSignalStrategy(
            strategy_config_with_signals,
            mock_bybit_client,
            mock_ws_client
        )
        
        # Заполняем буферы
        btc_buffer = strategy.signal_buffers["btc_signal"]
        btc_buffer["index_prices"].extend([50000, 50100, 50200])
        btc_buffer["target_prices"]["WIFUSDT"].extend([1.0, 1.001, 1.002])
        
        # Проверяем что буферы заполнены
        assert len(btc_buffer["index_prices"]) == 3
        assert len(btc_buffer["target_prices"]["WIFUSDT"]) == 3
        
        # Сбрасываем буферы
        await strategy.reset_buffers()
        
        # Проверяем что буферы очищены и перезаполнены историей
        # После reset_buffers() должна вызываться preload_history()
        assert mock_bybit_client.get_klines.call_count > 0  # Проверяем что API вызвался
