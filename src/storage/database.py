import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path

from .models import OrderRecord, SignalRecord, DailyStats
from .sql import (
    ORDERS_TABLE,
    SIGNALS_TABLE,
    DAILY_STATS_TABLE,
    ORDERS_STATUS_INDEX,
    ORDERS_PAIR_INDEX,
    SIGNALS_PAIR_INDEX,
)
from ..logger import logger


def adapt_date_iso(val):
    """Adapt datetime.date to ISO 8601 date."""
    return val.isoformat()


def adapt_datetime_iso(val):
    """Adapt datetime.datetime to timezone-naive ISO 8601 date."""
    return val.replace(tzinfo=None).isoformat()


def adapt_datetime_epoch(val):
    """Adapt datetime.datetime to Unix timestamp."""
    return int(val.timestamp())


sqlite3.register_adapter(date, adapt_date_iso)
sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_adapter(datetime, adapt_datetime_epoch)


def convert_date(val):
    """Convert ISO 8601 date to datetime.date object."""
    return date.fromisoformat(val.decode())


def convert_datetime(val):
    """Convert ISO 8601 datetime to datetime.datetime object."""
    return datetime.fromisoformat(val.decode())


def convert_timestamp(val):
    """Convert Unix epoch timestamp to datetime.datetime object."""
    return datetime.fromtimestamp(int(val))


sqlite3.register_converter("date", convert_date)
sqlite3.register_converter("datetime", convert_datetime)
sqlite3.register_converter("timestamp", convert_timestamp)


class Database:

    def __init__(self, db_path: str = "data/trading.db"):
        self.db_path = db_path

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

        logger.info(f"Database initialized at {db_path}")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute(ORDERS_TABLE)
            cursor.execute(SIGNALS_TABLE)
            cursor.execute(DAILY_STATS_TABLE)
            cursor.execute(ORDERS_STATUS_INDEX)
            cursor.execute(ORDERS_PAIR_INDEX)
            cursor.execute(SIGNALS_PAIR_INDEX)

            conn.commit()

    def save_order(self, order: OrderRecord) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                            INSERT INTO orders (
                                pair_name, symbol, order_id, side, quantity, entry_price,
                                take_profit, stop_loss, status, opened_at, closed_at,
                                close_price, pnl, pnl_percent, close_reason, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                order.pair_name, order.symbol, order.order_id, order.side,
                order.quantity, order.entry_price, order.take_profit, order.stop_loss,
                order.status, order.opened_at, order.closed_at, order.close_price,
                order.pnl, order.pnl_percent, order.close_reason, order.created_at
            ))

            conn.commit()

            return cursor.lastrowid

    def update_order(self, order_id: int, **kwargs) -> None:
        fields = []
        values = []
        for key, value in kwargs.items():
            fields.append(f"{key} = ?")
            values.append(value)

        if not fields:
            return

        values.append(order_id)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = f"UPDATE orders SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()

    def get_open_orders(self, pair_name: str | None = None) -> list[OrderRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if pair_name:
                cursor.execute(
                    "SELECT * FROM orders WHERE status = 'OPEN' AND pair_name = ?",
                    (pair_name,)
                )
            else:
                cursor.execute("SELECT * FROM orders WHERE status = 'OPEN'")

            rows = cursor.fetchall()
            return [self._row_to_order(row) for row in rows]

    def save_signal(self, signal: SignalRecord) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO signals (
                    pair_name, action, dominant_change, target_change,
                    target_price, executed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.pair_name, signal.action, signal.dominant_change,
                signal.target_change, signal.target_price, signal.executed,
                signal.created_at
            ))

            conn.commit()
            return cursor.lastrowid

    def get_daily_stats(self, date: str | None = None) -> DailyStats | None:
        """Получение статистики за день"""

        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (date,))
            row = cursor.fetchone()

            if row:
                return DailyStats(**dict(row))
            return None

    def calculate_and_save_daily_stats(self, date: str | None = None) -> None:
        """Расчет и сохранение дневной статистики"""

        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Получаем все закрытые ордера за день
            cursor.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as profitable,
                            SUM(IFNULL(pnl, 0)) as total_pnl,
                            MAX(IFNULL(pnl, 0)) as best,
                            MIN(IFNULL(pnl, 0)) as worst
                        FROM orders
                        WHERE DATE(closed_at) = ? AND status = 'CLOSED'
                    """, (date,))

            row = cursor.fetchone()

            is_n = row is None
            total_trades = int(row[0]) if not is_n and row[0] is not None else 0
            profitable_trades = int(row[1]) if not is_n and row[1] is not None else 0
            total_pnl = float(row[2]) if not is_n and row[2] is not None else 0.0
            best_trade = float(row[3]) if not is_n and row[3] is not None else 0.0
            worst_trade = float(row[4]) if not is_n and row[4] is not None else 0.0

            win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0.0

            # Сохраняем или обновляем
            cursor.execute("""
                        INSERT OR REPLACE INTO daily_stats
                        (date, total_trades, profitable_trades, total_pnl, win_rate, best_trade, worst_trade)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (date, total_trades, profitable_trades, total_pnl, win_rate, best_trade, worst_trade))

            conn.commit()

    def get_statistics_summary(self, days: int = 7) -> dict:
        """Получение сводной статистики за период"""

        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Общая статистика
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as profitable,
                    SUM(IFNULL(pnl, 0)) as total_pnl,
                    AVG(IFNULL(pnl_percent, 0)) as avg_pnl_percent,
                    MAX(IFNULL(pnl, 0)) as best,
                    MIN(IFNULL(pnl, 0)) as worst
                FROM orders
                WHERE DATE(closed_at) >= ? AND status = 'CLOSED'
            """, (start_date,))

            row = cursor.fetchone()

            is_n = row is None
            total_trades = int(row[0]) if not is_n and row[0] is not None else 0
            profitable_trades = int(row[1]) if not is_n and row[1] is not None else 0
            total_pnl = float(row[2]) if not is_n and row[2] is not None else 0.0
            avg_pnl_percent = float(row[3]) if not is_n and row[3] is not None else 0.0
            best_trade = float(row[4]) if not is_n and row[4] is not None else 0.0
            worst_trade = float(row[5]) if not is_n and row[5] is not None else 0.0

            win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0.0

            return {
                "period_days": days,
                "total_trades": total_trades,
                "profitable_trades": profitable_trades,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_percent": round(avg_pnl_percent, 2),
                "win_rate": round(win_rate, 2),
                "best_trade": round(best_trade, 2),
                "worst_trade": round(worst_trade, 2)
            }

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> OrderRecord:
        """Преобразование строки БД в OrderRecord"""
        return OrderRecord(
            id=row["id"],
            pair_name=row["pair_name"],
            symbol=row["symbol"],
            order_id=row["order_id"],
            side=row["side"],
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            take_profit=row["take_profit"],
            stop_loss=row["stop_loss"],
            status=row['status'],
            opened_at=row["opened_at"] if row["opened_at"] else None,
            # opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            closed_at=row["closed_at"] if row["closed_at"] else None,
            # closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            close_price=row["close_price"],
            pnl=row["pnl"],
            pnl_percent=row["pnl_percent"],
            close_reason=row["close_reason"],
            # created_at=datetime.fromisoformat(row["created_at"])
            created_at=row["created_at"]
        )
