import pytest
from datetime import datetime, timedelta
from src.storage.database import Database
from src.storage.models import OrderRecord, SignalRecord, DailyStats


@pytest.mark.unit
class TestDatabase:
    """Тесты базы данных"""

    def test_database_initialization(self, temp_db):
        """Тест инициализации БД"""
        db = Database(temp_db)

        assert db.db_path == temp_db

        # Проверяем что таблицы созданы
        import sqlite3
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = [row[0] for row in cursor.fetchall()]

        assert "orders" in tables
        assert "signals" in tables
        assert "daily_stats" in tables

    def test_save_order(self, temp_db):
        """Тест сохранения ордера"""
        db = Database(temp_db)

        order = OrderRecord(
            pair_name="TEST-PAIR",
            symbol="PEPEUSDT",
            order_id="order_123",
            side="Buy",
            quantity=100.0,
            entry_price=0.00001075,
            take_profit=0.00001096,
            stop_loss=0.00001064,
            status="OPEN",
            opened_at=datetime.now()
        )

        order_id = db.save_order(order)

        assert order_id > 0

    def test_update_order(self, temp_db):
        """Тест обновления ордера"""
        db = Database(temp_db)

        order = OrderRecord(
            pair_name="TEST-PAIR",
            symbol="PEPEUSDT",
            order_id="order_123",
            side="Buy",
            quantity=100.0,
            entry_price=0.00001075,
            status="OPEN",
            opened_at=datetime.now()
        )

        order_id = db.save_order(order)

        # Обновляем
        db.update_order(
            order_id,
            status="CLOSED",
            close_price=0.00001096,
            pnl=2.10,
            pnl_percent=2.0,
            close_reason="TP"
        )

        # Проверяем
        import sqlite3
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, pnl, close_reason FROM orders WHERE id = ?", (order_id,))
            row = cursor.fetchone()

        assert row[0] == "CLOSED"
        assert row[1] == 2.10
        assert row[2] == "TP"

    def test_get_open_orders_all(self, temp_db):
        """Тест получения всех открытых ордеров"""
        db = Database(temp_db)

        # Создаем открытые ордера
        for i in range(3):
            order = OrderRecord(
                pair_name=f"PAIR-{i}",
                symbol="PEPEUSDT",
                order_id=f"order_{i}",
                side="Buy",
                quantity=100.0,
                entry_price=0.00001075,
                status="OPEN",
                opened_at=datetime.now()
            )
            db.save_order(order)

        # Создаем закрытый
        closed = OrderRecord(
            pair_name="CLOSED-PAIR",
            symbol="PEPEUSDT",
            order_id="closed_order",
            side="Buy",
            quantity=100.0,
            entry_price=0.00001075,
            status="CLOSED",
            opened_at=datetime.now(),
            closed_at=datetime.now()
        )
        db.save_order(closed)

        # Получаем открытые
        open_orders = db.get_open_orders()

        assert len(open_orders) == 3
        for order in open_orders:
            assert order.status == "OPEN"

    def test_get_open_orders_by_pair(self, temp_db):
        """Тест получения открытых ордеров по паре"""
        db = Database(temp_db)

        # Создаем ордера для разных пар
        for pair in ["PAIR-A", "PAIR-B", "PAIR-A"]:
            order = OrderRecord(
                pair_name=pair,
                symbol="PEPEUSDT",
                order_id=f"order_{pair}",
                side="Buy",
                quantity=100.0,
                entry_price=0.00001075,
                status="OPEN",
                opened_at=datetime.now()
            )
            db.save_order(order)

        # Получаем для PAIR-A
        orders = db.get_open_orders("PAIR-A")

        assert len(orders) == 2
        for order in orders:
            assert order.pair_name == "PAIR-A"

    def test_save_signal(self, temp_db):
        """Тест сохранения сигнала"""
        db = Database(temp_db)

        signal = SignalRecord(
            pair_name="TEST-PAIR",
            action="Buy",
            dominant_change=1.23,
            target_change=0.75,
            target_price=0.00001075,
            executed=True
        )

        signal_id = db.save_signal(signal)

        assert signal_id > 0

    def test_calculate_daily_stats(self, temp_db):
        """Тест расчета дневной статистики"""
        db = Database(temp_db)

        today = datetime.now().strftime('%Y-%m-%d')

        # Создаем закрытые ордера за сегодня
        orders = [
            (100.0, 2.0),  # Прибыльный
            (-50.0, -1.0),  # Убыточный
            (150.0, 3.0),  # Прибыльный
        ]

        for pnl, pnl_percent in orders:
            order = OrderRecord(
                pair_name="TEST",
                symbol="PEPEUSDT",
                order_id=f"order_{pnl}",
                side="Buy",
                quantity=100.0,
                entry_price=0.00001000,
                status="CLOSED",
                opened_at=datetime.now(),
                closed_at=datetime.now(),
                pnl=pnl,
                pnl_percent=pnl_percent
            )
            db.save_order(order)

        # Рассчитываем статистику
        db.calculate_and_save_daily_stats(today)

        # Получаем
        stats = db.get_daily_stats(today)

        assert stats is not None
        assert stats.total_trades == 3
        assert stats.profitable_trades == 2
        assert stats.win_rate == pytest.approx(66.67, rel=0.01)
        assert stats.total_pnl == 200.0
        assert stats.best_trade == 150.0
        assert stats.worst_trade == -50.0

    def test_get_statistics_summary(self, temp_db):
        """Тест получения сводной статистики"""
        db = Database(temp_db)

        # Создаем ордера за последние дни
        for days_ago in range(5):
            date = datetime.now() - timedelta(days=days_ago)

            order = OrderRecord(
                pair_name="TEST",
                symbol="PEPEUSDT",
                order_id=f"order_{days_ago}",
                side="Buy",
                quantity=100.0,
                entry_price=0.00001000,
                status="CLOSED",
                opened_at=date,
                closed_at=date,
                pnl=100.0 if days_ago % 2 == 0 else -50.0,
                pnl_percent=2.0 if days_ago % 2 == 0 else -1.0
            )
            db.save_order(order)

        # Получаем статистику за 7 дней
        summary = db.get_statistics_summary(days=7)

        assert summary["period_days"] == 7
        assert summary["total_trades"] == 5
        assert summary["profitable_trades"] == 3
        assert summary["total_pnl"] > 0
