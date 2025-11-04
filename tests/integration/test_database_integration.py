import pytest
from datetime import datetime, timedelta
from src.storage.database import Database
from src.storage.models import OrderRecord, SignalRecord


@pytest.mark.integration
class TestDatabaseIntegration:
    """Интеграционные тесты БД"""

    def test_order_lifecycle_in_db(self, temp_db):
        """Тест полного жизненного цикла ордера в БД"""
        db = Database(temp_db)

        # 1. Создаем ордер
        order = OrderRecord(
            pair_name="TEST",
            symbol="PEPEUSDT",
            order_id="lifecycle_order",
            side="Buy",
            quantity=100.0,
            entry_price=0.00001000,
            take_profit=0.00001020,
            stop_loss=0.00000990,
            status="PENDING",
            created_at=datetime.now()
        )

        order_id = db.save_order(order)
        assert order_id > 0

        # 2. Обновляем на OPEN
        db.update_order(order_id, status="OPEN", opened_at=datetime.now())

        # 3. Проверяем в открытых
        open_orders = db.get_open_orders()
        assert len(open_orders) == 1
        assert open_orders[0].status == "OPEN"

        # 4. Закрываем с прибылью
        db.update_order(
            order_id,
            status="CLOSED",
            closed_at=datetime.now(),
            close_price=0.00001020,
            pnl=2.0,
            pnl_percent=2.0,
            close_reason="TP"
        )

        # 5. Проверяем что нет в открытых
        open_orders = db.get_open_orders()
        assert len(open_orders) == 0

    def test_signals_and_orders_correlation(self, temp_db):
        """Тест корреляции между сигналами и ордерами"""
        db = Database(temp_db)

        # Сохраняем сигнал
        signal = SignalRecord(
            pair_name="TEST",
            action="Buy",
            dominant_change=1.23,
            target_change=0.75,
            target_price=0.00001075,
            executed=False
        )

        signal_id = db.save_signal(signal)

        # Сохраняем ордер на основе сигнала
        order = OrderRecord(
            pair_name="TEST",
            symbol="PEPEUSDT",
            order_id="signal_order",
            side="Buy",
            quantity=100.0,
            entry_price=0.00001075,
            status="OPEN",
            opened_at=datetime.now()
        )

        order_id = db.save_order(order)

        # Оба должны быть в БД
        assert signal_id > 0
        assert order_id > 0

    def test_daily_stats_calculation_over_multiple_days(self, temp_db):
        """Тест расчета статистики за несколько дней"""
        db = Database(temp_db)

        # Создаем ордера за 3 дня
        for day in range(3):
            date = datetime.now() - timedelta(days=day)

            for trade in range(2):
                order = OrderRecord(
                    pair_name="TEST",
                    symbol="PEPEUSDT",
                    order_id=f"order_day{day}_trade{trade}",
                    side="Buy",
                    quantity=100.0,
                    entry_price=0.00001000,
                    status="CLOSED",
                    opened_at=date,
                    closed_at=date,
                    pnl=100.0 if trade == 0 else -50.0,
                    pnl_percent=2.0 if trade == 0 else -1.0
                )
                db.save_order(order)

        # Рассчитываем статистику за каждый день
        for day in range(3):
            date = (datetime.now() - timedelta(days=day)).strftime('%Y-%m-%d')
            db.calculate_and_save_daily_stats(date)

            stats = db.get_daily_stats(date)
            assert stats is not None
            assert stats.total_trades == 2
            assert stats.profitable_trades == 1
