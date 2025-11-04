import pytest
import asyncio
import tempfile
import json
import os
from datetime import datetime
from unittest.mock import AsyncMock


@pytest.fixture
def event_loop():
    """Event loop для async тестов"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db():
    """Временная БД"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def sample_config_dict():
    """Конфигурация с НОВЫМИ параметрами"""
    return {
        "api": {
            "api_key": "test_key",
            "api_secret": "test_secret",
            "testnet": True
        },
        "global": {
            "max_stop_loss_streak": 3,
            "database_path": "test.db"
        },
        "pairs": [
            {
                "name": "TEST-PAIR",
                "dominant_pair": "BTCUSDT",
                "target_pair": "PEPEUSDT",
                "tick_window": 5,
                "timeframe": "5",
                "dominant_threshold": 1.0,
                "target_max_threshold": 0.8,
                "direction": 0,  # -1=short, 0=any, 1=long
                "reverse": 0,  # 0=direct, 1=reverse
                "price_change_threshold": 0.5,  # slippage check
                "position_size_percent": 10.0,
                "leverage": 1,  # 1=spot, >1=futures
                "take_profit_percent": 2.0,
                "stop_loss_percent": 1.0,
                "enabled": True
            }
        ],
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
def temp_config_file(sample_config_dict):
    """Временный конфиг файл"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_config_dict, f)
        config_path = f.name
    yield config_path
    if os.path.exists(config_path):
        os.unlink(config_path)


@pytest.fixture
def mock_kline():
    """Mock Kline dataclass (NEW)"""
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
def mock_klines_list(mock_kline):
    """Список из 30 klines для истории"""
    from src.api.common import Kline
    klines = []
    for i in range(30):
        klines.append(Kline(
            timestamp=int((datetime.now().timestamp() - i * 300) * 1000),
            open=50000.0 + i,
            high=50100.0 + i,
            low=49900.0 + i,
            close=50050.0 + i,
            volume=1234.5,
            confirm=True,
        ))
    return list(reversed(klines))


@pytest.fixture
def mock_client():
    """Mock клиент с ИСПРАВЛЕННЫМ API"""
    client = AsyncMock()

    # ВАЖНО: теперь get_klines возвращает list[Kline], а не list[dict]
    from src.api.common import Kline
    klines = [
        Kline(
            timestamp=int((datetime.now().timestamp() - i * 300) * 1000),
            open=50000.0 + i,
            high=50100.0 + i,
            low=49900.0 + i,
            close=50050.0 + i,
            volume=1234.5,
            confirm=True,
        )
        for i in range(30)
    ]

    client.get_klines.return_value = list(reversed(klines))
    client.get_wallet_balance.return_value = {
        "list": [{"totalEquity": "10000.00"}]
    }
    client.set_leverage.return_value = True
    client.place_market_order.return_value = {
        "orderId": "test_order_123"
    }
    client.get_position.return_value = None
    client.close.return_value = None

    # NEW: Singleton флаг
    client._initialized = False

    return client


@pytest.fixture
def mock_ws_client():
    """Mock WebSocket клиент (NEW)"""
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
def sample_signal():
    """Signal с НОВЫМИ полями"""
    from src.strategy.correlation_strategy import Signal
    return Signal(
        action="Buy",
        target_price=0.00001075,
        dominant_change=1.23,
        target_change=0.75,
        slippage_ok=True,
        timestamp=datetime.now(),
    )


@pytest.fixture
def sample_order_record():
    """OrderRecord из models.py"""
    from src.storage.models import OrderRecord
    return OrderRecord(
        id=1,
        pair_name="TEST-PAIR",
        symbol="PEPEUSDT",
        order_id="test_order_123",
        side="Buy",
        quantity=100.0,
        entry_price=0.00001075,
        take_profit=0.00001096,
        stop_loss=0.00001064,
        status="OPEN",
        opened_at=datetime.now(),
    )


def pytest_configure(config):
    """Конфигурация маркеров"""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "slow: slow tests")
