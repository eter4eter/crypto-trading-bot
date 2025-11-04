import pytest
from src.config import Config, PairConfig


@pytest.mark.unit
class TestPairConfig:
    """Тесты PairConfig с новыми полями"""

    def test_valid_pair_config_with_new_params(self):
        """Тест с НОВЫМИ параметрами"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=30,
            timeframe="5",  # NEW
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,  # NEW
            reverse=0,  # NEW
            price_change_threshold=0.5,  # NEW
            position_size_percent=10.0,
            leverage=1,  # NEW: spot
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        assert config.name == "TEST"
        assert config.timeframe == "5"
        assert config.direction == 0
        assert config.reverse == 0
        assert config.leverage == 1
        assert config.is_spot() is True

    def test_tick_window_zero(self):
        """NEW: Тест tick_window=0"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=0,  # NEW: использовать только последнюю свечу
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

        assert config.tick_window == 0

    def test_futures_config(self):
        """NEW: Тест фьючерсной конфигурации"""
        config = PairConfig(
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
            leverage=5,  # NEW: futures 5x
            take_profit_percent=3.0,
            stop_loss_percent=1.5
        )

        assert config.is_futures() is True
        assert config.leverage == 5
        assert config.get_market_category() == "linear"

    def test_spot_requires_direction_zero(self):
        """NEW: Спот требует direction=0"""
        with pytest.raises(AssertionError, match="direction должен быть 0"):
            PairConfig(
                name="INVALID",
                dominant_pair="BTCUSDT",
                target_pair="PEPEUSDT",
                tick_window=30,
                timeframe="5",
                dominant_threshold=1.0,
                target_max_threshold=0.8,
                direction=1,  # ОШИБКА для spot
                reverse=0,
                price_change_threshold=0.5,
                position_size_percent=10.0,
                leverage=1,  # spot
                take_profit_percent=2.0,
                stop_loss_percent=1.0
            )

    def test_invalid_direction(self):
        """NEW: Невалидный direction"""
        with pytest.raises(AssertionError, match="direction должен быть"):
            PairConfig(
                name="TEST",
                dominant_pair="BTCUSDT",
                target_pair="PEPEUSDT",
                tick_window=30,
                timeframe="5",
                dominant_threshold=1.0,
                target_max_threshold=0.8,
                direction=2,  # ОШИБКА: должен быть -1, 0 или 1
                reverse=0,
                price_change_threshold=0.5,
                position_size_percent=10.0,
                leverage=5,
                take_profit_percent=2.0,
                stop_loss_percent=1.0
            )

    def test_should_take_signal_any_direction(self):
        """NEW: Тест should_take_signal с direction=0"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=30,
            timeframe="5",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,  # любое направление
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=5,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        assert config.should_take_signal("Buy") is True
        assert config.should_take_signal("Sell") is True

    def test_should_take_signal_long_only(self):
        """NEW: Тест direction=1 (только long)"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=30,
            timeframe="5",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=1,  # только long
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=5,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        assert config.should_take_signal("Buy") is True
        assert config.should_take_signal("Sell") is False

    def test_apply_reverse_logic(self):
        """NEW: Тест reverse логики"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=30,
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

        # Reverse меняет направление
        assert config.apply_reverse_logic("Buy") == "Sell"
        assert config.apply_reverse_logic("Sell") == "Buy"

    def test_get_timeframe_seconds(self):
        """NEW: Тест конвертации timeframe"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=30,
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

        assert config.get_timeframe_seconds() == 300  # 5 минут = 300 секунд
