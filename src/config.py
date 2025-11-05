import logging
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Dict, List

WEBSOCKET_INTERVALS = {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"}
POLLING_INTERVALS = {"1s", "5s", "10s", "15s", "30s"}


@dataclass
class SignalConfig:
    """Конфигурация сигнала"""
    index: str                    # "BTC-USDT" - базовая пара
    frame: str                    # "1s", "5s", "1", "5", "15", "60", "D", "W", "M"
    tick_window: int              # размер окна для накопления
    index_change_threshold: float # порог изменения базовой пары (%)
    target: float                 # целевое значение показателя
    direction: Literal[-1, 0, 1] # -1, 0, 1 - направление движения показателя
    reverse: Literal[0, 1]        # 0, 1 - логика инверсии

    def __post_init__(self):
        assert self.index, "index не может быть пустым"
        assert self.frame, "frame не может быть пустым"
        assert self.tick_window >= 0, "tick_window должен быть >= 0"
        assert self.index_change_threshold > 0, "index_change_threshold должен быть > 0"
        assert self.direction in [-1, 0, 1], "direction должен быть -1, 0 или 1"
        assert self.reverse in [0, 1], "reverse должен быть 0 или 1"


@dataclass
class StrategyConfig:
    """Конфигурация стратегии"""
    name: str
    trade_pairs: list[str]           # ["WIF-USDT-SWAP"] - пары для торговли
    leverage: int                    # плечо (1 = spot, >1 = futures)
    tick_window: int                 # основной размер окна
    price_change_threshold: float    # максимальное проскальзывание (%)
    stop_take_percent: float         # размер тейка/стопа (%)
    position_size: int               # размер позиции
    direction: Literal[-1, 0, 1]     # -1=short, 0=both, 1=long
    signals: dict[str, SignalConfig] # блок сигналов
    enabled: bool = True

    def __post_init__(self):
        assert self.trade_pairs, "trade_pairs не может быть пустым"
        assert self.leverage >= 1, "leverage должен быть >= 1"
        assert len(self.signals) > 0, "должен быть минимум один сигнал"
        assert self.position_size > 0, "position_size должен быть > 0"
        
        # Для спота только direction=0
        if self.leverage == 1:
            assert self.direction == 0, "Для spot (leverage=1) direction должен быть 0"

    def is_spot(self) -> bool:
        return self.leverage == 1

    def is_futures(self) -> bool:
        return self.leverage > 1

    def get_market_category(self) -> str:
        return "spot" if self.is_spot() else "linear"

    def get_pair_category(self, symbol: str) -> str:
        """Категория рынка для конкретной пары.
        По умолчанию возвращает категорию всей стратегии (fallback).
        Переопределите в конфиге при необходимости per-symbol логики.
        """
        return self.get_market_category()

    def should_take_signal(self, signal_action: str) -> bool:
        if self.direction == 0:
            return True
        elif self.direction == 1:
            return signal_action == "Buy"
        elif self.direction == -1:
            return signal_action == "Sell"
        return False


@dataclass
class PairConfig:
    name: str
    dominant_pair: str
    target_pair: str

    # анализ
    tick_window: int    # 0 = использовать только последнюю свечу
    timeframe: str      # "1s", "5s", "1", "5", "15", "60", "D", "W", "M"

    # пороги
    dominant_threshold: float
    target_max_threshold: float

    # направление движения
    direction: Literal[-1, 0, 1]    # -1=short, 0=any, 1=long
    reverse: Literal[0, 1]          # 0=direct, 1=reverse

    # Проскальзывание
    price_change_threshold: float   # % максимального проскальзывания

    # Позиция
    position_size_percent: float
    leverage: int                   # 1 = spot, >1 = futures

    take_profit_percent: float
    stop_loss_percent: float

    enabled: bool = True

    def __post_init__(self):
        """Валидация параметров"""
        assert self.tick_window >= 0, "tick_window должен быть >= 0"
        assert self._validate_timeframe(), f"Invalid timeframe: {self.timeframe}"
        assert 0 < self.dominant_threshold <= 100, "dominant_threshold должен быть 0-100%"
        assert 0 < self.target_max_threshold <= 100, "target_max_threshold должен быть 0-100%"
        assert self.direction in [-1, 0, 1], "direction должен быть -1, 0 или 1"
        assert self.reverse in [0, 1], "reverse должен быть 0 или 1"
        assert 0 < self.position_size_percent <= 100, "position_size_percent должен быть 0-100%"
        assert self.leverage >= 1, "leverage должен быть 1-100"
        assert self.take_profit_percent > 0, "take_profit должен быть > 0"
        assert self.stop_loss_percent > 0, "stop_loss должен быть > 0"

        # Для спота только direction=0
        if self.leverage == 1:
            assert self.direction == 0, "Для spot (leverage=1) direction должен быть 0"

        if self.is_spot():
            if self.leverage != 1:
                raise ValueError(
                    f"[{self.name}] Spot requires leverage=1, got {self.leverage}"
                )
            if self.direction != 0:
                raise ValueError(
                    f"[{self.name}] Spot requires direction=0, got {self.direction}"
                )

        if self.is_futures():
            if self.leverage <= 1:
                raise ValueError(
                    f"[{self.name}] Futures requires leverage > 1, got {self.leverage}"
                )

    def uses_websocket(self) -> bool:
        """Проверка: использует ли пара WebSocket"""
        return self.timeframe in WEBSOCKET_INTERVALS

    def uses_polling(self) -> bool:
        """Проверка: использует ли пара REST API polling"""
        return self.timeframe in POLLING_INTERVALS

    def get_polling_interval_seconds(self) -> int:
        """Получить интервал polling в секундах"""
        if not self.uses_polling():
            return 0

        # Конвертация "30s" -> 30
        return int(self.timeframe.rstrip("s"))

    def _validate_timeframe(self) -> bool:
        """Валидация timeframe"""
        all_intervals = WEBSOCKET_INTERVALS | POLLING_INTERVALS
        return self.timeframe in all_intervals

    def is_spot(self) -> bool:
        """Проверка на спотовую торговлю"""
        return self.leverage == 1

    def is_futures(self) -> bool:
        """Проверка на фьючерсную торговлю"""
        return self.leverage > 1

    def get_market_category(self) -> str:
        """Получение категории рынка для Bybit API"""
        return "spot" if self.is_spot() else "linear"

    def should_take_signal(self, signal_action: str) -> bool:
        """
        Проверка, следует ли брать сигнал с учетом direction

        Args:
            signal_action: "Buy" или "Sell"

        Returns:
            True если сигнал подходит под direction
        """
        if self.direction == 0:
            # Любое направление
            return True
        elif self.direction == 1:
            # Только лонг
            return signal_action == "Buy"
        elif self.direction == -1:
            # Только шорт
            return signal_action == "Sell"

        return False

    def apply_reverse_logic(self, action: str) -> str:
        """
        Применение reverse логики

        Args:
            action: Исходное действие "BUY" или "SELL"

        Returns:
            Финальное действие с учетом reverse
        """
        if self.reverse == 0:
            # Прямая логика
            return action
        else:
            # Обратная логика
            return "Sell" if action == "Buy" else "Buy"

    def get_timeframe_seconds(self) -> int:
        """Получить timeframe в секундах"""
        if self.uses_polling():
            return self.get_polling_interval_seconds()

        # WebSocket интервалы
        if self.timeframe == "D":
            return 86400  # 1 день
        elif self.timeframe == "W":
            return 604800  # 1 неделя
        elif self.timeframe == "M":
            return 2592000  # 1 месяц
        else:
            return int(self.timeframe) * 60  # Минуты в секунды


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notify_signals: bool = True
    notify_trades: bool = True
    notify_errors: bool = True
    notify_daily_report: bool = True


@dataclass
class Config:
    # API
    api_key: str
    api_secret: str
    testnet: bool
    demo_mode: bool

    # global settings
    max_stop_loss_trades: int

    # db
    database_path: str = "data/trading.db"

    # trade pairs (старый формат)
    pairs: list[PairConfig] = field(default_factory=list)
    
    # strategies (новый формат ТЗ)
    strategies: Dict[str, StrategyConfig] = field(default_factory=dict)

    # telegram
    telegram: TelegramConfig = None

    logging_level: str = "INFO"
    log_file: str = ""

    @classmethod
    def load(cls, config_path: str = "../config/config.json") -> "Config":

        if not Path(config_path).exists():
            raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        api_key = os.getenv("BYBIT_API_KEY") or data.get("api", {}).get("api_key", "")
        api_secret = os.getenv("BYBIT_API_SECRET") or data.get("api", {}).get("api_secret", "")

        if not api_key or not api_secret:
            raise ValueError("api_key или api_secret не найдены в конфигурации или в окружении")

        testnet = str(os.getenv("BYBIT_TESTNET", data.get("api", {}).get("testnet", "")).lower()) == "true"
        demo_mode = str(os.getenv("BYBIT_DEMO_MODE", data.get("api", {}).get("demo_mode", "")).lower()) == "true"

        # Загрузка pairs (старый формат)
        pairs = []
        for pair_data in data.get("pairs", [])[:13]:
            pairs.append(PairConfig(**pair_data))

        # Загрузка strategies (новый формат ТЗ)
        strategies = {}
        strategies_data = data.get("strategies", {})
        
        for strategy_name, strategy_data in strategies_data.items():
            # Парсим сигналы
            signals = {}
            signals_data = strategy_data.get("signals", {})
            
            for signal_name, signal_data in signals_data.items():
                signals[signal_name] = SignalConfig(**signal_data)
            
            # Создаем стратегию
            strategy_data_copy = strategy_data.copy()
            strategy_data_copy["signals"] = signals
            strategy_data_copy["name"] = strategy_name
            
            strategies[strategy_name] = StrategyConfig(**strategy_data_copy)

        if not pairs and not strategies:
            raise ValueError("Не найдены пары или стратегии для торговли")

        telegram_data = data.get("telegram", {})
        telegram = TelegramConfig(
            enabled=telegram_data.get("enabled", False),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or telegram_data.get("bot_token", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID") or telegram_data.get("chat_id", ""),
            notify_signals=telegram_data.get("notify_signals", True),
            notify_trades=telegram_data.get("notify_trades", True),
            notify_errors=telegram_data.get("notify_errors", True),
            notify_daily_report=telegram_data.get("notify_daily_report", True)
        )

        log_level = data.get("global", {}).get("logging_level", "INFO")
        if log_level.upper() not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            log_level = "INFO"

        return cls(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            demo_mode=demo_mode,
            max_stop_loss_trades=data.get("global", {}).get("max_stop_loss_trades"),
            database_path=data.get("global", {}).get("database_path", "data/trading.db"),
            pairs=pairs,
            strategies=strategies,
            telegram=telegram,
            logging_level=log_level,
        )

    @property
    def enabled_pairs(self) -> list[PairConfig]:
        """Получить только включенные пары"""
        return [p for p in self.pairs if p.enabled]
        
    @property
    def enabled_strategies(self) -> Dict[str, StrategyConfig]:
        """Получить только включенные стратегии"""
        return {name: strategy for name, strategy in self.strategies.items() if strategy.enabled}
