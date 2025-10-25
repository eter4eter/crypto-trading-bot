from dataclasses import dataclass
from datetime import datetime
from typing import Literal



@dataclass
class OrderRecord:
    id: int | None = None
    pair_name: str = ""
    symbol: str = ""
    order_id: str = ""
    side: Literal["buy", "sell"] = ""
    quantity: float = 0
    entry_price: float = 0
    take_profit: float | None = None
    stop_loss: float | None = None
    status: Literal["OPEN", "CLOSED", "PENDING", "CANCELLED"] = "PENDING"
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    close_price: float | None = None
    pnl: float | None = None
    pnl_percent: float | None = None
    close_reason: Literal["TP", "SL", "MANUAL"] | None = None
    created_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


@dataclass
class SignalRecord:
    id: int | None = None
    pair_name: str = ""
    action: Literal["BUY", "SELL", "NONE"] = ""
    dominant_change: float = 0
    target_change: float = 0
    target_price: float = 0
    executed: bool = False
    created_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


@dataclass
class DailyStats:
    date: str
    total_trades: int = 0
    profitable_trades: int = 0
    total_pnl: float = 0
    win_rate: float = 0
    best_trade: float = 0
    worst_trade: float = 0
