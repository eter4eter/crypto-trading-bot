import pytest
from src.strategy.correlation_strategy import CorrelationStrategy, Signal
from src.config import PairConfig


@pytest.mark.unit
class TestSignal:
    """Тесты Signal с НОВЫМ полем slippage_ok"""

    def test_signal_with_slippage(self):
        """NEW: Тест Signal с проверкой проскальзывания"""
        signal = Signal(
            action="buy",
            target_price=0.00001075,
            dominant_change=1.23,
            target_change=0.75,
            slippage_ok=True  # NEW
        )

        assert signal.slippage_ok is True


@pytest.mark.unit
class TestCorrelationStrategy:
    """Тесты стратегии с ИСПРАВЛЕНИЯМИ"""

    @pytest.fixture
    def pair_config_spot(self):
        """Spot конфигурация"""
        return PairConfig(
            name="SPOT-TEST",
            dominant_pair="BTCUSDT",
            target_pair="ETHUSDT",
            tick_window=5,
            timeframe="5",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=1,  # spot
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

    @pytest.fixture
    def pair_config_futures(self):
        """Futures конфигурация"""
        return PairConfig(
            name="FUTURES-TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=20,
            timeframe="15",
            dominant_threshold=1.5,
            target_max_threshold=1.0,
            direction=1,  # только long
            reverse=0,
            price_change_threshold=1.0,
            position_size_percent=15.0,
            leverage=5,  # futures 5x
            take_profit_percent=3.0,
            stop_loss_percent=1.5
        )

    @pytest.mark.asyncio
    async def test_preload_history_uses_kline_attributes(
            self,
            pair_config_spot,
            mock_client,
            mock_ws_client
    ):
        strategy = CorrelationStrategy(
            pair_config_spot,
            mock_client,
            mock_ws_client
        )

        # mock_client.get_klines возвращает list[Kline]
        success = await strategy.preload_history()

        assert success is True
        assert len(strategy.dominant_closes) == 5  # n-1

        # ВАЖНО: Проверяем что можем обратиться к последней цене
        assert strategy.last_dominant_close > 0
        assert strategy.last_target_close > 0

    @pytest.mark.asyncio
    async def test_preload_history_tick_window_zero(
            self,
            mock_client,
            mock_ws_client
    ):
        """NEW: Тест с tick_window=0"""
        config = PairConfig(
            name="ZERO-WINDOW",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=0,  # только последняя свеча
            timeframe="5",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=1,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        strategy = CorrelationStrategy(config, mock_client, mock_ws_client)

        # Загружаем только 2 свечи (последняя закрытая + текущая)
        from src.api.common import Kline
        mock_client.get_klines.return_value = [
            Kline(1, 50000.0, 50100.0, 49900.0, 50050.0, 1000),
            Kline(2, 50050.0, 50150.0, 49950.0, 50100.0, 1100)
        ]

        success = await strategy.preload_history()

        assert success is True
        assert strategy.last_dominant_close == 50050.0  # предпоследняя

    @pytest.mark.asyncio
    async def test_reset_buffers_is_async(self, pair_config_spot, mock_client, mock_ws_client):
        """ИСПРАВЛЕНИЕ: reset_buffers теперь async"""
        strategy = CorrelationStrategy(pair_config_spot, mock_client, mock_ws_client)

        # Заполняем буфер
        strategy.dominant_closes.extend([50000, 50100, 50200])
        strategy.target_closes.extend([0.00001, 0.00001, 0.00001])

        # ВАЖНО: await для async метода
        await strategy.reset_buffers()

        assert len(strategy.dominant_closes) == 5  # после preload_history

    @pytest.mark.asyncio
    async def test_check_signal_with_direction_filter(
            self,
            pair_config_futures,  # direction=1 (только long)
            mock_client,
            mock_ws_client
    ):
        """NEW: Тест фильтрации по direction"""
        strategy = CorrelationStrategy(pair_config_futures, mock_client, mock_ws_client)

        # Заполняем буфер для SELL сигнала
        strategy.dominant_closes.extend([
            50600, 50500, 50400, 50300, 50200, 50100, 50000,
            49900, 49800, 49700, 49600, 49500, 49400, 49300,
            49200, 49100, 49000, 48900, 48800, 48700  # -3.76% падение
        ])

        strategy.target_closes.extend([
            0.00001100, 0.00001095, 0.00001090, 0.00001085,
            0.00001080, 0.00001075, 0.00001070, 0.00001065,
            0.00001060, 0.00001055, 0.00001050, 0.00001045,
            0.00001040, 0.00001035, 0.00001030, 0.00001025,
            0.00001020, 0.00001015, 0.00001010, 0.00001005  # -0.86% падение
        ])

        # Callback для перехвата сигнала
        captured_signal = None

        async def capture_signal(signal):
            nonlocal captured_signal
            captured_signal = signal

        strategy.set_signal_callback(capture_signal)

        # Проверяем сигнал
        await strategy._check_signal_async()

        # SELL сигнал должен быть отфильтрован (direction=1, только long)
        assert captured_signal is None

    @pytest.mark.asyncio
    async def test_check_signal_with_reverse(self, mock_client, mock_ws_client):
        """NEW: Тест reverse логики"""
        config = PairConfig(
            name="REVERSE-TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=5,
            timeframe="5",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,
            reverse=1,  # обратная логика
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=5,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        strategy = CorrelationStrategy(config, mock_client, mock_ws_client)

        # BTC растет
        strategy.dominant_closes.extend([50000, 50100, 50200, 50300, 50600])  # +1.2%
        strategy.target_closes.extend([0.00001000, 0.00001020, 0.00001030, 0.00001050, 0.00001070])  # +0.7%

        captured_signal = None

        async def capture_signal(signal):
            nonlocal captured_signal
            captured_signal = signal

        strategy.set_signal_callback(capture_signal)
        await strategy._check_signal_async()

        # С reverse=1, BTC растет -> должен быть SELL
        assert captured_signal is not None
        assert captured_signal.action == "Sell"  # reverse от Buy

    @pytest.mark.asyncio
    async def test_check_signal_slippage_check(self, pair_config_spot, mock_client, mock_ws_client):
        """NEW: Тест проверки проскальзывания"""
        strategy = CorrelationStrategy(pair_config_spot, mock_client, mock_ws_client)

        # Устанавливаем signal_price для проверки slippage
        strategy.signal_price = 0.00001075

        # Заполняем буфер
        strategy.dominant_closes.extend([50000, 50100, 50200, 50300, 50600])
        strategy.target_closes.extend([0.00001000, 0.00001020, 0.00001030, 0.00001050, 0.00001070])

        # Текущая цена сильно отличается от signal_price
        strategy.target_closes[-1] = 0.00001150  # +6.9% от signal_price > 0.5% threshold

        captured_signal = None

        async def capture_signal(signal):
            nonlocal captured_signal
            captured_signal = signal

        strategy.set_signal_callback(capture_signal)
        await strategy._check_signal_async()

        # Сигнал должен быть сгенерирован, но slippage_ok = False
        # Callback НЕ вызовется из-за проверки slippage_ok в _check_signal_async
        assert captured_signal is None  # из-за проскальзывания
