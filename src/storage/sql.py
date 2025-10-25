ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    order_id TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    take_profit REAL,
    stop_loss REAL,
    status TEXT NOT NULL,
    opened_at TIMESTAMP,
    closed_at TIMESTAMP,
    close_price REAL,
    pnl REAL,
    pnl_percent REAL,
    close_reason TEXT,
    created_at TIMESTAMP NOT NULL 
)"""


SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_name TEXT NOT NULL,
    action TEXT NOT NULL,
    dominant_change REAL NOT NULL,
    target_change REAL NOT NULL,
    target_price REAL NOT NULL,
    executed BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL
)"""


DAILY_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_trades INTEGER NOT NULL,
    profitable_trades INTEGER NOT NULL,
    total_pnl REAL NOT NULL,
    win_rate REAL NOT NULL,
    best_trade REAL NOT NULL,
    worst_trade REAL NOT NULL
)"""


ORDERS_STATUS_INDEX = """CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)"""
ORDERS_PAIR_INDEX = """CREATE INDEX IF NOT EXISTS idx_orders_pair ON orders(pair_name)"""
SIGNALS_PAIR_INDEX = """CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair_name)"""
