from dataclasses import dataclass


@dataclass
class Kline:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    confirm: bool = False # True когда свеча закрылась


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]
