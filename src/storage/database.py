import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from .models import OrderRecord, SignalRecord, DailyStats
from .sql import (
    ORDERS_TABLE, SIGNALS_TABLE, DAILY_STATS_TABLE, ORDERS_STATUS_INDEX, ORDERS_PAIR_INDEX, SIGNALS_PAIR_INDEX,
)
from ..logger import get_app_logger

logger = get_app_logger()

def _fix_datetime_field(val):
    # Исправляет opened_at, closed_at, created_at: поддержка str, int, float, datetime
    if val is None or isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val)
    if isinstance(val, str):
        # ISO8601
        try:
            if len(val) > 10 and ("T" in val or ":" in val):
                return datetime.fromisoformat(val)
            else:
                return datetime.strptime(val, "%Y-%m-%d")
        except Exception:
            pass
    return None

# ...[rest unchanged above]...

class Database:
    # ...[init and _init_db unchanged]...

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> OrderRecord:
        """Преобразование строки БД в OrderRecord (robust datetime)."""
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
            opened_at=_fix_datetime_field(row["opened_at"]),
            closed_at=_fix_datetime_field(row["closed_at"]),
            close_price=row["close_price"],
            pnl=row["pnl"],
            pnl_percent=row["pnl_percent"],
            close_reason=row["close_reason"],
            created_at=_fix_datetime_field(row["created_at"])
        )

# ...[остальной код без изменений]...
