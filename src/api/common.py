from dataclasses import dataclass


@dataclass
class Kline:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    confirm: bool  # True когда свеча закрылась
