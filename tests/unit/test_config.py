import pytest
from src.config import Config, PairConfig, StrategyConfig, SignalConfig


@pytest.mark.unit
@pytest.mark.config
class TestSignalConfig:
    """Тесты SignalConfig (новый класс для ТЗ)"""
    
    def test_valid_signal_config(self):
        """Тест валидной конфигурации сигнала"""
        signal = SignalConfig(
            index="BTCUSDT",
            frame="1",
            tick_window=30,
            index_change_threshold=1.0,
            target=0.8,
            direction=0,
            reverse=0
        )
        
        assert signal.index == "BTCUSDT"
        assert signal.frame == "1"
        assert signal.tick_window == 30
        assert signal.index_change_threshold == 1.0
        assert signal.target == 0.8
        assert signal.direction == 0
        assert signal.reverse == 0
        
    def test_signal_tick_window_zero(self):
        """Тест tick_window=0 для сигнала"""
        signal = SignalConfig(
            index="BTCUSDT",
            frame="5s",
            tick_window=0,  # Последняя свеча
            index_change_threshold=0.5,
            target=0.3,
            direction=1,
            reverse=1
        )
        
        assert signal.tick_window == 0
        
    def test_invalid_direction_signal(self):
        """Тест невалидного direction для сигнала"""
        with pytest.raises(AssertionError, match="direction должен быть -1, 0 или 1"):
            SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=30,
                index_change_threshold=1.0,
                target=0.8,
                direction=2,  # ОШИБКА
                reverse=0
            )


@pytest.mark.unit
@pytest.mark.config            
class TestStrategyConfig:
    """Тесты StrategyConfig (новый класс для ТЗ)"""
    
    def test_valid_strategy_config(self):
        """Тест валидной конфигурации стратегии"""
        signals = {
            "test_signal": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=30,
                index_change_threshold=1.0,
                target=0.8,
                direction=0,
                reverse=0
            )
        }
        
        strategy = StrategyConfig(
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
        
        assert strategy.name == "test-strategy"
        assert strategy.trade_pairs == ["WIFUSDT"]
        assert strategy.leverage == 5
        assert len(strategy.signals) == 1
        assert strategy.is_futures() is True
        
    def test_empty_signals_error(self):
        """Тест ошибки при пустом signals"""
        with pytest.raises(AssertionError, match="должен быть минимум один сигнал"):
            StrategyConfig(
                name="test-strategy",
                trade_pairs=["WIFUSDT"],
                leverage=5,
                tick_window=10,
                price_change_threshold=0.05,
                stop_take_percent=0.01,
                position_size=100,
                direction=0,
                signals={},  # Пустой
                enabled=True
            )
            
    def test_spot_strategy_direction_zero_required(self):
        """Тест: спот стратегия требует direction=0"""
        signals = {
            "test_signal": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=30,
                index_change_threshold=1.0,
                target=0.8,
                direction=0,
                reverse=0
            )
        }
        
        with pytest.raises(AssertionError, match="direction должен быть 0"):
            StrategyConfig(
                name="invalid-spot",
                trade_pairs=["ETHUSDT"],
                leverage=1,  # spot
                tick_window=10,
                price_change_threshold=0.05,
                stop_take_percent=0.01,
                position_size=100,
                direction=1,  # ОШИБКА для spot
                signals=signals,
                enabled=True
            )


@pytest.mark.unit
class TestPairConfig:
    """Тесты PairConfig с новыми полями"""

    def test_valid_pair_config_with_new_params(self):
        """Тест с новыми параметрами"""
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

        assert config.name == "TEST"
        assert config.timeframe == "5"
        assert config.direction == 0
        assert config.reverse == 0
        assert config.leverage == 1
        assert config.is_spot() is True

    def test_polling_timeframe(self):
        """Тест polling timeframe"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=10,
            timeframe="5s",  # Polling интервал
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=5,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        assert config.uses_polling() is True
        assert config.uses_websocket() is False
        assert config.get_polling_interval_seconds() == 5


@pytest.mark.unit
@pytest.mark.config
class TestConfig:
    """Тесты Config с поддержкой strategies"""

    def test_load_config_with_strategies(self, temp_strategies_config_file):
        """Тест загрузки конфигурации с strategies"""
        config = Config.load(temp_strategies_config_file)
        
        assert config.api_key == "test_key"
        assert config.testnet is True
        assert len(config.strategies) == 2  # WIF-USDT-test и BTC-scalping-test
        
        # Проверяем первую стратегию
        wif_strategy = config.strategies["WIF-USDT-test"]
        assert wif_strategy.name == "WIF-USDT-test"
        assert wif_strategy.trade_pairs == ["WIFUSDT"]
        assert wif_strategy.leverage == 5
        assert len(wif_strategy.signals) == 2
        
        # Проверяем сигналы
        btc_signal = wif_strategy.signals["btc_correlation"]
        assert btc_signal.index == "BTCUSDT"
        assert btc_signal.frame == "1"
        assert btc_signal.tick_window == 30
        assert btc_signal.index_change_threshold == 1.0
        assert btc_signal.target == 0.75
        assert btc_signal.direction == 0
        assert btc_signal.reverse == 0
        
        eth_signal = wif_strategy.signals["eth_momentum"]
        assert eth_signal.index == "ETHUSDT"
        assert eth_signal.frame == "5"
        assert eth_signal.tick_window == 20
        assert eth_signal.direction == 1
        assert eth_signal.reverse == 1
        
    def test_load_config_with_tick_window_zero(self, temp_strategies_config_file):
        """Тест загрузки стратегии с tick_window=0"""
        config = Config.load(temp_strategies_config_file)
        
        btc_strategy = config.strategies["BTC-scalping-test"]
        assert btc_strategy.tick_window == 0
        
        momentum_signal = btc_strategy.signals["momentum_signal"]
        assert momentum_signal.tick_window == 0
        assert momentum_signal.frame == "5s"  # Polling интервал
        
    def test_enabled_strategies_property(self, temp_strategies_config_file):
        """Тест enabled_strategies property"""
        config = Config.load(temp_strategies_config_file)
        
        enabled = config.enabled_strategies
        assert len(enabled) == 2  # Обе стратегии enabled=True
        assert "WIF-USDT-test" in enabled
        assert "BTC-scalping-test" in enabled
        
    def test_strategy_should_take_signal(self):
        """Тест метода should_take_signal для StrategyConfig"""
        signals = {
            "test_signal": SignalConfig(
                index="BTCUSDT",
                frame="1",
                tick_window=30,
                index_change_threshold=1.0,
                target=0.8,
                direction=0,
                reverse=0
            )
        }
        
        # Любое направление (direction=0)
        strategy = StrategyConfig(
            name="any-direction",
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
        
        assert strategy.should_take_signal("Buy") is True
        assert strategy.should_take_signal("Sell") is True
        
        # Только LONG (direction=1)
        strategy.direction = 1
        assert strategy.should_take_signal("Buy") is True
        assert strategy.should_take_signal("Sell") is False
        
        # Только SHORT (direction=-1)
        strategy.direction = -1
        assert strategy.should_take_signal("Buy") is False
        assert strategy.should_take_signal("Sell") is True


@pytest.mark.unit
class TestPairConfig:
    """Тесты PairConfig (старый формат для обратной совместимости)"""

    def test_valid_pair_config_with_new_params(self):
        """Тест с новыми параметрами"""
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

        assert config.name == "TEST"
        assert config.timeframe == "5"
        assert config.direction == 0
        assert config.reverse == 0
        assert config.leverage == 1
        assert config.is_spot() is True

    def test_tick_window_zero(self):
        """Тест tick_window=0"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=0,
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

    def test_polling_timeframe(self):
        """Тест polling timeframe"""
        config = PairConfig(
            name="TEST",
            dominant_pair="BTCUSDT",
            target_pair="PEPEUSDT",
            tick_window=10,
            timeframe="5s",
            dominant_threshold=1.0,
            target_max_threshold=0.8,
            direction=0,
            reverse=0,
            price_change_threshold=0.5,
            position_size_percent=10.0,
            leverage=5,
            take_profit_percent=2.0,
            stop_loss_percent=1.0
        )

        assert config.uses_polling() is True
        assert config.uses_websocket() is False
        assert config.get_polling_interval_seconds() == 5

    def test_should_take_signal_directions(self):
        """Тест should_take_signal с разными direction"""
        # direction=0 (любое направление)
        config = PairConfig(
            name="ANY", dominant_pair="BTCUSDT", target_pair="PEPEUSDT",
            tick_window=30, timeframe="5", dominant_threshold=1.0, target_max_threshold=0.8,
            direction=0, reverse=0, price_change_threshold=0.5, position_size_percent=10.0,
            leverage=5, take_profit_percent=2.0, stop_loss_percent=1.0
        )
        assert config.should_take_signal("Buy") is True
        assert config.should_take_signal("Sell") is True
        
        # direction=1 (только long)
        config.direction = 1
        assert config.should_take_signal("Buy") is True
        assert config.should_take_signal("Sell") is False
        
        # direction=-1 (только short)
        config.direction = -1
        assert config.should_take_signal("Buy") is False
        assert config.should_take_signal("Sell") is True

    def test_apply_reverse_logic(self):
        """Тест apply_reverse_logic"""
        config = PairConfig(
            name="REVERSE", dominant_pair="BTCUSDT", target_pair="PEPEUSDT",
            tick_window=30, timeframe="5", dominant_threshold=1.0, target_max_threshold=0.8,
            direction=0, reverse=1, price_change_threshold=0.5, position_size_percent=10.0,
            leverage=5, take_profit_percent=2.0, stop_loss_percent=1.0
        )
        
        # reverse=1 меняет направление
        assert config.apply_reverse_logic("Buy") == "Sell"
        assert config.apply_reverse_logic("Sell") == "Buy"
        
        # reverse=0 не меняет
        config.reverse = 0
        assert config.apply_reverse_logic("Buy") == "Buy"
        assert config.apply_reverse_logic("Sell") == "Sell"
