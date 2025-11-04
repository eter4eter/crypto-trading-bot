"""
Конфигурация согласно техническому заданию
Поддержка структуры strategies с блоками signals
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal


@dataclass
class SignalConfigTZ:
    """Конфигурация сигнала согласно ТЗ"""
    index: str                          # "BTC-USDT" - базовая пара
    frame: str                          # "1s", "5s", "1m", "5m", "1h", "D", "W", "M"
    tick_window: int                    # размер окна для накопления
    index_change_threshold: float       # порог изменения базовой пары (%)
    target: float                       # целевое значение показателя
    direction: Literal[-1, 0, 1]        # -1, 0, 1 - направление движения показателя
    reverse: Literal[0, 1]              # 0, 1 - логика инверсии

    def __post_init__(self):
        assert self.index, "index не может быть пустым"
        assert self.frame, "frame не может быть пустым"
        assert self.tick_window >= 0, "tick_window должен быть >= 0"
        assert self.index_change_threshold > 0, "index_change_threshold должен быть > 0"
        assert self.direction in [-1, 0, 1], "direction должен быть -1, 0 или 1"
        assert self.reverse in [0, 1], "reverse должен быть 0 или 1"

    def get_timeframe_seconds(self) -> int:
        """Получить timeframe в секундах"""
        if self.frame.endswith("s"):
            return int(self.frame[:-1])  # "30s" -> 30
        elif self.frame == "D":
            return 86400  # 1 день
        elif self.frame == "W":
            return 604800  # 1 неделя
        elif self.frame == "M":
            return 2592000  # 1 месяц
        else:
            return int(self.frame) * 60  # Минуты в секунды


@dataclass
class StrategyConfigTZ:
    """Конфигурация стратегии согласно ТЗ"""
    name: str
    trade_pairs: List[str]              # ["WIF-USDT-SWAP"] - пары для торговли
    leverage: int                       # плечо (1 = spot, >1 = futures)
    tick_window: int                    # основной размер окна
    price_change_threshold: float       # максимальное проскальзывание (%)
    stop_take_percent: float            # размер тейка/стопа (%)
    position_size: int                  # размер позиции
    direction: Literal[-1, 0, 1]        # -1=short, 0=both, 1=long
    signals: Dict[str, SignalConfigTZ]  # блок сигналов
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
        """
        if self.direction == 0:
            return True  # Любое направление
        elif self.direction == 1:
            return signal_action == "Buy"  # Только лонг
        elif self.direction == -1:
            return signal_action == "Sell"  # Только шорт
        return False


@dataclass
class GlobalConfigTZ:
    """Глобальные настройки согласно ТЗ"""
    max_stop_loss_trades: int
    mode: str                           # "real" или "demo"
    log_file: str                       # путь к файлу логов
    request_interval: int               # интервал запросов (сек)
    restart: int                        # автоперезапуск (1/0)
    
    def __post_init__(self):
        assert self.mode in ["real", "demo"], "mode должен быть 'real' или 'demo'"
        assert self.max_stop_loss_trades > 0, "max_stop_loss_trades должен быть > 0"
        assert self.request_interval > 0, "request_interval должен быть > 0"


@dataclass
class TelegramConfigTZ:
    """Конфигурация Telegram"""
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notify_signals: bool = True
    notify_trades: bool = True
    notify_errors: bool = True
    notify_daily_report: bool = True


@dataclass
class ConfigTZ:
    """Основная конфигурация согласно ТЗ"""
    global_settings: GlobalConfigTZ
    strategies: Dict[str, StrategyConfigTZ]
    api_credentials: Dict[str, str]     # API ключи
    telegram: TelegramConfigTZ = field(default_factory=TelegramConfigTZ)
    
    @classmethod
    def load_from_tz_format(cls, config_path: str) -> "ConfigTZ":
        """Загрузка конфигурации в формате ТЗ"""
        
        if not Path(config_path).exists():
            raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")
        
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Global settings
        global_data = data.get("global", {})
        global_config = GlobalConfigTZ(
            max_stop_loss_trades=global_data.get("max_stop_loss_trades", 2),
            mode=global_data.get("mode", "demo"),
            log_file=global_data.get("log_file", "logs/trading_log.txt"),
            request_interval=global_data.get("request_interval", 1),
            restart=global_data.get("restart", 1)
        )
        
        # API credentials 
        api_credentials = data.get("api_credentials", {})
        api_credentials["api_key"] = os.getenv("BYBIT_API_KEY") or api_credentials.get("api_key", "")
        api_credentials["api_secret"] = os.getenv("BYBIT_API_SECRET") or api_credentials.get("api_secret", "")
        api_credentials["testnet"] = str(os.getenv("BYBIT_TESTNET", api_credentials.get("testnet", "true"))).lower() == "true"
        api_credentials["demo_mode"] = str(os.getenv("BYBIT_DEMO_MODE", api_credentials.get("demo_mode", "true"))).lower() == "true"
        
        # Strategies
        strategies = {}
        strategies_data = data.get("strategies", {})
        
        for strategy_name, strategy_data in strategies_data.items():
            # Парсим сигналы
            signals = {}
            signals_data = strategy_data.get("signals", {})
            
            for signal_name, signal_data in signals_data.items():
                signals[signal_name] = SignalConfigTZ(**signal_data)
            
            # Создаем стратегию
            strategy_data_copy = strategy_data.copy()
            strategy_data_copy["signals"] = signals
            strategy_data_copy["name"] = strategy_name
            
            strategies[strategy_name] = StrategyConfigTZ(**strategy_data_copy)
        
        # Telegram
        telegram_data = data.get("telegram", {})
        telegram = TelegramConfigTZ(
            enabled=telegram_data.get("enabled", False),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or telegram_data.get("bot_token", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID") or telegram_data.get("chat_id", ""),
            notify_signals=telegram_data.get("notify_signals", True),
            notify_trades=telegram_data.get("notify_trades", True),
            notify_errors=telegram_data.get("notify_errors", True),
            notify_daily_report=telegram_data.get("notify_daily_report", True)
        )
        
        return cls(
            global_settings=global_config,
            strategies=strategies,
            api_credentials=api_credentials,
            telegram=telegram
        )
    
    @property
    def enabled_strategies(self) -> Dict[str, StrategyConfigTZ]:
        """Получить только включенные стратегии"""
        return {name: strategy for name, strategy in self.strategies.items() if strategy.enabled}
