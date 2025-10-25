import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..logger import logger
from ..config import PairConfig
from ..api.bybit_websocket_client import BybitWebSocketClient
from ..api.bybit_client import BybitClient


@dataclass
class Signal:
    action: Literal["BUY", "SELL", "NONE"]
    target_price: float
    dominant_change: float
    target_change: float
    slippage_ok: bool = True    # Проверка проскальзывания
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class CorrelationStrategy:
    """
    Стратегия корреляции двух валют

    Полная реализация логики:
    1. Накопление тиков в скользящих окнах
    2. Триггер от изменения доминирующей пары
    3. Проверка корреляции с целевой парой
    4. Генерация сигнала при соблюдении условий
    """

    def __init__(
            self,
            config: PairConfig,
            rest_client: BybitClient,
            ws_client: BybitWebSocketClient,
    ):
        self.config = config
        self.rest_client = rest_client
        self.ws_client = ws_client

        # буферы свечей (close prices)
        if config.tick_window > 0:
            self.dominant_closes: deque = deque(maxlen=config.tick_window)
            self.target_closes: deque = deque(maxlen=config.tick_window)
        else:
            self.last_dominant_close = 0
            self.last_target_close = 0
            self._prev_dominant = 0
            self._prev_target = 0

        # Для проверки проскальзывания
        self.signal_price = 0

        # Locks
        self.lock = asyncio.Lock()

        self.signals_generated = 0
        self.history_loaded = False

        # Callback для сигналов
        self.signal_callback = None

        market_type = "SPOT" if config.is_spot() else f"FUTURES {config.leverage}x"
        direction_str = {-1: "SHORT only", 0: "BOTH", 1: "LONG only"}[config.direction]
        reverse_str = "REVERSE" if config.reverse == 1 else "DIRECT"

        logger.info(f"═══ Strategy [{config.name}] ═══")
        logger.info(f"  Market: {market_type}")
        logger.info(f"  Dominant: {config.dominant_pair}")
        logger.info(f"  Target: {config.target_pair}")
        logger.info(f"  Timeframe: {config.timeframe}")
        logger.info(f"  Window: {config.tick_window} ({'last only' if config.tick_window == 0 else 'candles'})")
        logger.info(f"  Direction: {direction_str}")
        logger.info(f"  Logic: {reverse_str}")
        logger.info(f"  Max Slippage: {config.price_change_threshold}%")

    async def preload_history(self):
        """
        Предзагрузка исторических данных (n-1 свечей)
        """

        if self.config.tick_window == 0:
            # Для tick_window=0 загружаем только последнюю закрытую свечу
            logger.info(f"[{self.config.name}] Using last candle only (tick_window=0)")

            dominant_klines = await self.rest_client.get_klines(
                category=self.config.get_market_category(),
                symbol=self.config.dominant_pair,
                interval=self.config.timeframe,
                limit=2  # Последняя и текущая
            )

            target_klines = await self.rest_client.get_klines(
                category=self.config.get_market_category(),
                symbol=self.config.target_pair,
                interval=self.config.timeframe,
                limit=2
            )

            if not dominant_klines or not target_klines:
                logger.error(f"[{self.config.name}] Failed to load last candle")
                return False

            # Берем последнюю закрытую свечу (предпоследняя, т.к. последняя - текущая)
            self.last_dominant_close = dominant_klines[-2]['close']
            self.last_target_close = target_klines[-2]['close']

            logger.info(
                f"[{self.config.name}] ✅ Last candle loaded: "
                f"Dominant=${self.last_dominant_close:.2f}, "
                f"Target=${self.last_target_close:.8f}"
            )
        else:
            # Стандартная загрузка n-1 свечей
            logger.info(f"[{self.config.name}] 📥 Loading {self.config.tick_window} candles...")

            dominant_klines = await self.rest_client.get_klines(
                category=self.config.get_market_category(),
                symbol=self.config.dominant_pair,
                interval=self.config.timeframe,
                limit=self.config.tick_window
            )

            target_klines = await self.rest_client.get_klines(
                category=self.config.get_market_category(),
                symbol=self.config.target_pair,
                interval=self.config.timeframe,
                limit=self.config.tick_window
            )

            if not dominant_klines or not target_klines:
                logger.error(f"[{self.config.name}] Failed to load historical data")
                return False

            # Заполняем буферы (n-1 свечей)
            async with self.lock:
                for kline in dominant_klines[:-1]:
                    self.dominant_closes.append(kline.close)

                for kline in target_klines[:-1]:
                    self.target_closes.append(kline.close)

            logger.info(
                f"[{self.config.name}] ✅ History loaded: "
                f"{len(self.dominant_closes)}/{self.config.tick_window} candles"
            )

        self.history_loaded = True
        return True

    async def start(self):
        """Запуск стратегии с подписками на kline streams"""

        # Подписываемся на доминирующую пару (spot)
        self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.dominant_pair,
            interval=self.config.timeframe,
            callback=self._on_dominant_kline
        )

        # Подписываемся на целевую пару (futures)
        self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.target_pair,
            interval=self.config.timeframe,
            callback=self._on_target_kline
        )

        logger.info(f"[{self.config.name}] ✅ Kline subscriptions active")

    async def _on_dominant_kline(self, symbol: str, kline: dict):
        """Callback при новой свече доминирующей пары"""

        # Обрабатываем только закрытые свечи
        if not kline["confirm"]:
            return

        close_price = kline["close"]

        async with self.lock:
            if self.config.tick_window > 0:
                self.dominant_closes.append(close_price)
            else:
                self.last_dominant_close = close_price

        logger.debug(f"[{self.config.name}] 📊 Dominant candle: ${close_price:.2f}")

        # Проверяем сигнал
        await self._check_signal_async()

    async def _on_target_kline(self, symbol: str, kline: dict):
        """Callback при новой свече целевой пары"""

        # Обрабатываем только закрытые свечи
        if not kline["confirm"]:
            return

        close_price = kline["close"]

        async with self.lock:
            if self.config.tick_window > 0:
                self.target_closes.append(close_price)
            else:
                self.last_target_close = close_price

        logger.debug(f"[{self.config.name}] 📊 Target candle: ${close_price:.8f}")

        # Проверяем сигнал
        await self._check_signal_async()

    # async def update_ticks(self) -> bool:
    #     """
    #     Обновление тиков обеих пар
    #
    #     Returns:
    #         True если обе цены получены успешно
    #     """
    #
    #     # Получаем цены параллельно
    #     dominant_task = self.rest_client.get_ticker_price("spot", self.config.dominant_pair)
    #     target_task = self.rest_client.get_ticker_price("linear", self.config.target_pair)
    #
    #     dominant_price, target_price = await asyncio.gather(
    #         dominant_task,
    #         target_task,
    #         return_exceptions=True
    #     )
    #
    #     # Проверяем ошибки
    #     if isinstance(dominant_price, Exception) or dominant_price is None:
    #         logger.error(f"[{self.config.name}] Failed to get {self.config.dominant_pair} price")
    #         return False
    #
    #     if isinstance(target_price, Exception) or target_price is None:
    #         logger.error(f"[{self.config.name}] Failed to get {self.config.target_pair} price")
    #         return False
    #
    #     # Добавляем в буферы
    #     self.dominant_closes.append(dominant_price)
    #     self.target_closes.append(target_price)
    #
    #     self.last_dominant_price = dominant_price
    #     self.last_target_price = target_price
    #
    #     # Отмечаем заполнение буфера
    #     if len(self.dominant_closes) == self.config.tick_window and self.buffer_fills == 0:
    #         self.buffer_fills = 1
    #         logger.info(f"[{self.config.name}] Buffer filled ({self.config.tick_window} ticks)")
    #
    #     logger.debug(
    #         f"[{self.config.name}] Ticks: "
    #         f"BTC=${dominant_price:.2f}, "
    #         f"Target=${target_price:.8f}, "
    #         f"Buffer={len(self.dominant_closes)}/{self.config.tick_window}"
    #     )
    #
    #     return True

    async def _check_signal_async(self):
        """Проверка условий сигнала"""

        if self.config.tick_window > 0:

            # Ждем заполнения буферов
            if len(self.dominant_closes) < self.config.tick_window:
                return
            if len(self.target_closes) < self.config.tick_window:
                return

            # Копируем данные под lock
            async with self.lock:
                dominant_first = self.dominant_closes[0]
                dominant_last = self.dominant_closes[-1]
                target_first = self.target_closes[0]
                target_last = self.target_closes[-1]

        else:
            # tick_window=0: используем только последнюю свечу
            # Сравниваем с предыдущей (которую мы сохранили)
            if not hasattr(self, '_prev_dominant') or not hasattr(self, '_prev_target'):
                # Первый запуск - сохраняем текущие значения
                self._prev_dominant = self.last_dominant_close
                self._prev_target = self.last_target_close
                return

            dominant_first = self._prev_dominant
            dominant_last = self.last_dominant_close
            target_first = self._prev_target
            target_last = self.last_target_close

            # Обновляем предыдущие значения
            self._prev_dominant = dominant_last
            self._prev_target = target_last

        # Расчет изменений
        dominant_change = ((dominant_last - dominant_first) / dominant_first) * 100
        target_change = ((target_last - target_first) / target_first) * 100

        # Проверка условий
        if abs(dominant_change) < self.config.dominant_threshold:
            return

        # Корреляция
        same_direction = (
                (dominant_change > 0 and target_change > 0) or
                (dominant_change < 0 and target_change < 0)
        )

        if not same_direction:
            return

        # Проверка максимума
        if abs(target_change) >= self.config.target_max_threshold:
            return

        # Генерируем сигнал
        raw_action = "BUY" if dominant_change > 0 else "SELL"
        action = self.config.apply_reverse_logic(action=raw_action)

        # Проверяем направление (direction)
        if not self.config.should_take_signal(action):
            logger.debug(
                f"[{self.config.name}] Signal {action} filtered by direction={self.config.direction}"
            )
            return

        # Проверка проскальзывания
        slippage_ok = self._check_slippage(target_last)

        signal = Signal(
            action=action,
            target_price=target_last,
            dominant_change=dominant_change,
            target_change=target_change,
            slippage_ok=slippage_ok,
        )

        self.signals_generated += 1

        logger.info(f"")
        logger.info(f"🎯 ═══ SIGNAL [{self.config.name}] ═══")
        logger.info(f"  Action: {action} {f'(reverse from {raw_action})' if self.config.reverse else ''}")
        logger.info(f"  Market: {'SPOT' if self.config.is_spot() else 'FUTURES'}")
        logger.info(f"  Dominant: {dominant_change:+.3f}%")
        logger.info(f"  Target: {target_change:+.3f}%")
        logger.info(f"  Price: ${target_last:.8f}")
        logger.info(f"  Slippage: {'✅ OK' if slippage_ok else '⚠️ EXCEEDED'}")
        logger.info(f"")

        # Вызываем callback
        if self.signal_callback and slippage_ok:
            await self.signal_callback(signal)

    def _check_slippage(self, signal_price: float) -> bool:
        """Проверка проскальзывания"""
        # TODO: проверить работоспособность self.signal_price не обновляется

        if self.signal_price == 0:
            return True  # Нет данных для проверки

        # Расчет фактического проскальзывания
        slippage_percent = abs((self.signal_price - signal_price) / signal_price) * 100

        if slippage_percent > self.config.price_change_threshold:
            logger.warning(
                f"[{self.config.name}] Slippage {slippage_percent:.2f}% > "
                f"threshold {self.config.price_change_threshold}%"
            )
            return False

        return True

    def set_signal_callback(self, callback):
        """Установка callback для сигналов"""
        self.signal_callback = callback

    # async def check_signal(self) -> Signal:
    #     """
    #     Проверка условий для генерации сигнала
    #
    #     Алгоритм:
    #     1. Буфер должен быть полным
    #     2. Изменение BTC (первый→последний) > dominant_threshold
    #     3. Целевая пара движется в ту же сторону
    #     4. Изменение целевой пары < target_max_threshold
    #
    #     Returns:
    #         Signal с действием BUY/SELL/NONE
    #     """
    #
    #     # Проверка 1: Буфер заполнен
    #     if len(self.dominant_closes) < self.config.tick_window:
    #         return Signal(Action.NONE, 0, 0, 0)
    #
    #     # Расчет изменений (первый → последний)
    #     dominant_first = self.dominant_closes[0]
    #     dominant_last = self.dominant_closes[-1]
    #     dominant_change = ((dominant_last - dominant_first) / dominant_first) * 100
    #
    #     target_first = self.target_closes[0]
    #     target_last = self.target_closes[-1]
    #     target_change = ((target_last - target_first) / target_first) * 100
    #
    #     # Проверка 2: BTC превысила порог
    #     if abs(dominant_change) < self.config.dominant_threshold:
    #         logger.debug(
    #             f"[{self.config.name}] BTC change {dominant_change:+.3f}% "
    #             f"< threshold {self.config.dominant_threshold}%"
    #         )
    #         return Signal(Action.NONE, target_last, dominant_change, target_change)
    #
    #     # Проверка 3: Корреляция направления
    #     same_direction = (
    #             (dominant_change > 0 and target_change > 0) or
    #             (dominant_change < 0 and target_change < 0)
    #     )
    #
    #     if not same_direction:
    #         logger.debug(
    #             f"[{self.config.name}] No correlation: "
    #             f"BTC {dominant_change:+.3f}%, Target {target_change:+.3f}%"
    #         )
    #         return Signal(Action.NONE, target_last, dominant_change, target_change)
    #
    #     # Проверка 4: Целевая пара не превысила максимум
    #     if abs(target_change) >= self.config.target_max_threshold:
    #         logger.debug(
    #             f"[{self.config.name}] Target exceeded max: "
    #             f"{abs(target_change):.3f}% >= {self.config.target_max_threshold}%"
    #         )
    #         return Signal(Action.NONE, target_last, dominant_change, target_change)
    #
    #     # ✅ Все условия выполнены - генерируем сигнал
    #     action = Action.BUY if dominant_change > 0 else Action.SELL
    #
    #     self.signals_generated += 1
    #
    #     logger.info("")
    #     logger.info(f"🎯 ═══ SIGNAL GENERATED [{self.config.name}] ═══")
    #     logger.info(f"  Action: {action}")
    #     logger.info(f"  BTC change: {dominant_change:+.3f}% (threshold: {self.config.dominant_threshold}%)")
    #     logger.info(f"  Target change: {target_change:+.3f}% (max: {self.config.target_max_threshold}%)")
    #     logger.info(f"  Target price: ${target_last:.8f}")
    #     logger.info(f"  Signal #{self.signals_generated}")
    #     logger.info("")
    #
    #     return Signal(action, target_last, dominant_change, target_change)

    def reset_buffers(self):
        """Сброс буферов (после сделки нужно перезагрузить историю)"""
        if self.config.tick_window > 0:
            async with self.lock:
                self.dominant_closes.clear()
                self.target_closes.clear()

        self.history_loaded = False
        logger.info(f"[{self.config.name}] 🔄 Buffers reset")
        await self.preload_history()

    def get_status(self) -> dict:
        """Статус стратегии"""
        if self.config.tick_window > 0:
            buffer_info = f"{len(self.dominant_closes)}/{self.config.tick_window}"
        else:
            buffer_info = "last only"

        return {
            "pair_name": self.config.name,
            "market": "SPOT" if self.config.is_spot() else "FUTURES",
            "timeframe": self.config.timeframe,
            "buffer": buffer_info,
            "signals": self.signals_generated
        }
