import pytest
import asyncio
import tempfile
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock


@pytest.fixture
def event_loop():
    """Основной event loop для async тестов"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db():
    """Временная база данных"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def sample_strategies_config():
    """Конфигурация с новым форматом strategies/signals согласно ТЗ"""
    return {
        "api": {
            "api_key": "test_key",
            "api_secret": "test_secret",
            "testnet": True,
            "demo_mode": True,
            "logging_level": "DEBUG"
        },
        "global": {
            "max_stop_loss_streak": 3,
            "database_path": "test.db"
        },
        "strategies": {
            "WIF-USDT-test": {
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
                        "frame": "1",
                        "tick_window": 30,
                        "index_change_threshold": 1.0,
                        "target": 0.75,
                        "direction": 0,
                        "reverse": 0
                    },
                    "eth_momentum": {
                        "index": "ETHUSDT",
                        "frame": "5",
                        "tick_window": 20,
                        "index_change_threshold": 1.5,
                        "target": 1.0,
                        "direction": 1,
                        "reverse": 1
                    }
                }
            },
            "BTC-scalping-test": {
                "trade_pairs": ["BTCUSDT"],
                "leverage": 3,
                "tick_window": 0,
                "price_change_threshold": 0.02,
                "stop_take_percent": 0.008,
                "position_size": 200,
                "direction": 1,
                "enabled": True,
                "signals": {
                    "momentum_signal": {
                        "index": "BTCUSDT",
                        "frame": "5s",
                        "tick_window": 0,
                        "index_change_threshold": 0.5,
                        "target": 0.8,
                        "direction": 1,
                        "reverse": 0
                    }
                }
            }
        },
        "telegram": {
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
            "notify_signals": True,
            "notify_trades": True,
            "notify_errors": True,
            "notify_daily_report": True
        }
    }


@pytest.fixture
def temp_strategies_config_file(sample_strategies_config):
    """Временный конфиг файл с форматом strategies"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_strategies_config, f)
        config_path = f.name
    yield config_path
    if os.path.exists(config_path):
        os.unlink(config_path)


@pytest.fixture
def mock_kline():
    """Мок Kline объект"""
    from src.api.common import Kline
    return Kline(
        timestamp=int(datetime.now().timestamp() * 1000),
        open=50000.0,
        high=50100.0,
        low=49900.0,
        close=50050.0,
        volume=1234.5,
        confirm=True,
    )


@pytest.fixture
def mock_klines_sequence():
    """Последовательность klines для тестирования tick_window"""
    from src.api.common import Kline
    klines = []
    base_price = 50000.0
    
    # Создаем последовательность с ростом 1% на каждой свече
    for i in range(35):  # 30 истории + 5 новых
        price = base_price * (1.01 ** i)  # Рост 1% на каждой свече
        klines.append(Kline(
            timestamp=int((datetime.now().timestamp() + i * 300) * 1000),
            open=price,
            high=price * 1.002,
            low=price * 0.998,
            close=price,
            volume=1000.0 + i,
            confirm=True
        ))
    
    return klines


@pytest.fixture
def mock_bybit_client(mock_klines_sequence):
    """Мок Bybit REST API клиент"""
    client = AsyncMock()
    
    # Возвращаем Kline объекты
    client.get_klines.return_value = mock_klines_sequence[:30]  # 30 свечей
    
    client.get_wallet_balance.return_value = {
        "list": [{"totalEquity": "10000.00"}]
    }
    client.set_leverage.return_value = True
    client.place_market_order.return_value = {
        "orderId": "test_order_123"
    }
    client.get_position.return_value = None
    client.close.return_value = None
    client.get_stats.return_value = {
        "request_count": 10,
        "error_count": 0,
        "error_rate": "0.0%"
    }
    
    return client


@pytest.fixture
def mock_ws_client():
    """Мок WebSocket клиент"""
    ws_client = AsyncMock()
    ws_client.connect.return_value = None
    ws_client.subscribe_kline.return_value = None
    ws_client.close.return_value = None
    ws_client.get_stats.return_value = {
        "connected": True,
        "messages_received": 100,
        "active_subscriptions": 2,
    }
    return ws_client


@pytest.fixture
def sample_signal_result():
    """Пример SignalResult для нового формата"""
    from src.strategy.multi_signal_strategy import SignalResult
    return SignalResult(
        signal_name="btc_correlation",
        strategy_name="WIF-USDT-test",
        action="Buy",
        index_pair="BTCUSDT",
        target_pairs=["WIFUSDT"],
        target_price=1.0567,
        index_change=1.23,
        target_change=0.75,
        triggered=True,
        slippage_ok=True,
        timestamp=datetime.now(),
    )


def pytest_configure(config):
    """Конфигурация маркеров"""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "slow: slow tests")
    config.addinivalue_line("markers", "strategy: strategy tests")
    config.addinivalue_line("markers", "config: configuration tests")
