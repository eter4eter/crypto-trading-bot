import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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

    def _validate_timeframe(self) -> bool:
        """Валидация timeframe"""
        valid_seconds = ["1s", "3s", "5s", "10s", "15s", "30s"]
        valid_minutes = ["1", "3", "5", "15", "30", "60", "120", "240", "360", "720"]
        valid_others = ["D", "W", "M"]

        return self.timeframe in valid_seconds + valid_minutes + valid_others

    def is_spot(self) -> bool:
        """Проверка на спотовую торговлю"""
        return self.leverage == 1

    def is_futures(self) -> bool:
        """Проверка на фьючерсную торговлю"""
        return self.leverage > 1

    def get_market_category(self) -> str:
        """Получение категории рынка для Bybit API"""
        return "spot" if self.is_spot() else "linear"

    def get_timeframe_seconds(self) -> int:
        """Конвертация timeframe в секунды"""
        if self.timeframe.endswith("s"):
            # Секунды: "1s", "5s", "30s"
            return int(self.timeframe[:-1])
        elif self.timeframe == "D":
            return 86400  # 24 часа
        elif self.timeframe == "W":
            return 604800  # 7 дней
        elif self.timeframe == "M":
            return 2592000  # 30 дней (приблизительно)
        else:
            # Минуты: "1", "5", "15", "60", ...
            return int(self.timeframe) * 60

    def should_take_signal(self, signal_action: str) -> bool:
        def should_take_signal(self, signal_action: str) -> bool:
            """
            Проверка, следует ли брать сигнал с учетом direction

            Args:
                signal_action: "BUY" или "SELL"

            Returns:
                True если сигнал подходит под direction
            """
            if self.direction == 0:
                # Любое направление
                return True
            elif self.direction == 1:
                # Только лонг
                return signal_action == "BUY"
            elif self.direction == -1:
                # Только шорт
                return signal_action == "SELL"

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
            return "SELL" if action == "BUY" else "BUY"


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

    # global settings
    max_stop_loss_streak: int

    # db
    database_path: str = "data/trading.db"

    # trade pairs
    pairs: list[PairConfig] = field(default_factory=list)

    # telegram
    telegram: TelegramConfig = None

    @classmethod
    def load(cls, config_path: str = "config/config.json") -> "Config":

        if not Path(config_path).exists():
            raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        api_key = os.getenv("BYBIT_API_KEY") or data.get("api", {}).get("api_key", "")
        api_secret = os.getenv("BYBIT_API_SECRET") or data.get("api", {}).get("api_secret", "")

        if not api_key or not api_secret:
            raise ValueError("api_key или api_secret не найдены в конфигурации или в окружении")

        testnet = os.getenv("BYBIT_TESTNET", data.get("api", {}).get("testnet", "")) == "true"

        pairs = []
        for pair_data in data.get("pairs", [])[:13]:
            pairs.append(PairConfig(**pair_data))

        if not pairs:
            raise ValueError("Не найдены пары для торговли")

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

        return cls(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            max_stop_loss_streak=data.get("global", {}).get("max_stop_loss_streak"),
            database_path=data.get("global", {}).get("database_path", "data/trading.db"),
            pairs=pairs,
            telegram=telegram,
        )

