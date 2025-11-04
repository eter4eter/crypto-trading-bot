import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime
from src.trading.order_tracker import OrderTracker
from src.storage.models import OrderRecord


@pytest.mark.unit
class TestOrderTracker:
    """Тесты OrderTracker"""

    def test_tracker_initialization(self, mock_client):
        """Тест инициализации"""
        tracker = OrderTracker(mock_client)

        assert tracker.client == mock_client
        assert len(tracker.tracking_orders) == 0
        assert tracker.running is False

    def test_track_order(self, mock_client, sample_order_record):
        """Тест добавления ордера на отслеживание"""
        tracker = OrderTracker(mock_client)

        tracker.track_order(sample_order_record)

        assert sample_order_record.order_id in tracker.tracking_orders
        assert tracker.tracking_orders[sample_order_record.order_id] == sample_order_record

    def test_untrack_order(self, mock_client, sample_order_record):
        """Тест удаления ордера из отслеживания"""
        tracker = OrderTracker(mock_client)

        tracker.track_order(sample_order_record)
        assert sample_order_record.order_id in tracker.tracking_orders

        tracker.untrack_order(sample_order_record.order_id)
        assert sample_order_record.order_id not in tracker.tracking_orders

    @pytest.mark.asyncio
    async def test_start_stop_monitoring(self, mock_client):
        """Тест запуска и остановки мониторинга"""
        tracker = OrderTracker(mock_client)

        await tracker.start_monitoring()
        assert tracker.running is True
        assert tracker.monitoring_task is not None

        await tracker.stop_monitoring()
        assert tracker.running is False

    @pytest.mark.asyncio
    async def test_check_orders_empty(self, mock_client):
        """Тест проверки когда нет ордеров"""
        tracker = OrderTracker(mock_client)

        # Не должно выбрасывать исключений
        await tracker._check_orders()

    @pytest.mark.asyncio
    async def test_process_order_filled(self, mock_client, sample_order_record):
        """Тест обработки исполненного ордера"""
        tracker = OrderTracker(mock_client)
        tracker.track_order(sample_order_record)

        api_order = {
            "orderId": sample_order_record.order_id,
            "orderStatus": "Filled",
            "avgPrice": "0.00001096",
            "cumExecQty": "100.0"
        }

        await tracker._process_order_update(sample_order_record, api_order)

        assert sample_order_record.status == "CLOSED"
        assert sample_order_record.close_price == 0.00001096
        assert sample_order_record.pnl is not None
        assert sample_order_record.order_id not in tracker.tracking_orders

    @pytest.mark.asyncio
    async def test_process_order_cancelled(self, mock_client, sample_order_record):
        """Тест обработки отмененного ордера"""
        tracker = OrderTracker(mock_client)
        tracker.track_order(sample_order_record)

        api_order = {
            "orderId": sample_order_record.order_id,
            "orderStatus": "Cancelled"
        }

        await tracker._process_order_update(sample_order_record, api_order)

        assert sample_order_record.status == "CANCELLED"
        assert sample_order_record.order_id not in tracker.tracking_orders

    def test_get_stats(self, mock_client):
        """Тест получения статистики"""
        tracker = OrderTracker(mock_client)

        # Добавляем ордера
        for i in range(3):
            order = Mock()
            order.order_id = f"order_{i}"
            tracker.track_order(order)

        stats = tracker.get_stats()

        assert stats["tracking_orders"] == 3
        assert stats["monitoring_active"] is False
