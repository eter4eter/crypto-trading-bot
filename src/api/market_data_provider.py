"""
Унифицированный провайдер рыночных данных для MultiSignalStrategy

Поддерживает получение данных от множественных пар с разными timeframe:
- WebSocket для интервалов ≥ 1 минута  
- REST API polling для интервалов < 1 минута
"""

import asyncio
import time
from typing import Callable, Dict, Set

from ..logger import logger
from ..config import StrategyConfig, SignalConfig
from .bybit_client import BybitClient
from .bybit_websocket_client import BybitWebSocketClient
from .common import Kline


class MultiMarketDataProvider:
    """
    Провайдер данных для мультисигнальных стратегий
    
    Автоматически определяет источник данных для каждого сигнала:
    - Frame < 1m (секундные): REST polling
    - Frame ≥ 1m (минутные): WebSocket подписки
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        rest_client: BybitClient,
        ws_client: BybitWebSocketClient,
    ):
        self.strategy_config = strategy_config
        self.rest_client = rest_client
        self.ws_client = ws_client
        
        # Колбэк для передачи данных в стратегию
        self.kline_callback: Callable[[str, Kline], None] = None
        
        # Разделяем сигналы по типу получения данных
        self.polling_signals: Dict[str, SignalConfig] = {}
        self.websocket_signals: Dict[str, SignalConfig] = {}
        
        self._analyze_signals()
        
        # Для polling режима
        self.polling_tasks: Dict[str, asyncio.Task] = {}
        self.polling_active = False
        
        # Кеш последних данных для каждой пары
        self.last_poll_times: Dict[str, float] = {}
        
        logger.info(f"[{strategy_config.name}] MultiMarketDataProvider инициализирован")
        logger.info(f"   Polling сигналы: {len(self.polling_signals)}")
        logger.info(f"   WebSocket сигналы: {len(self.websocket_signals)}")

    def _analyze_signals(self):
        """Анализ сигналов для определения источника данных"""
        
        for signal_name, signal_config in self.strategy_config.signals.items():
            # Определяем тип по frame
            if signal_config.frame.endswith("s"):
                # Секундные интервалы -> polling
                self.polling_signals[signal_name] = signal_config
            else:
                # Минутные и выше -> websocket
                self.websocket_signals[signal_name] = signal_config
        
        # Логируем разделение
        for name, sig in self.polling_signals.items():
            logger.info(f"   📡 Polling: {name} -> {sig.index}+targets ({sig.frame})")
            
        for name, sig in self.websocket_signals.items():
            logger.info(f"   🔌 WebSocket: {name} -> {sig.index}+targets ({sig.frame})")

    def set_callback(self, callback: Callable[[str, Kline], None]):
        """Установка callback для передачи kline данных"""
        self.kline_callback = callback

    async def start(self):
        """Запуск всех источников данных"""
        
        # Запускаем WebSocket подписки
        await self._start_websocket_subscriptions()
        
        # Запускаем polling для секундных интервалов
        await self._start_polling_tasks()
        
        logger.info(f"[{self.strategy_config.name}] ✅ Все источники данных активированы")

    async def stop(self):
        """Остановка всех источников данных"""
        
        # Останавливаем polling
        await self._stop_polling_tasks()
        
        logger.info(f"[{self.strategy_config.name}] ✅ Провайдер данных остановлен")

    # ========== WebSocket подписки ==========
    
    async def _start_websocket_subscriptions(self):
        """Запуск WebSocket подписок для минутных интервалов"""
        
        if not self.websocket_signals:
            logger.info(f"[{self.strategy_config.name}] Нет WebSocket сигналов")
            return
        
        logger.info(f"[{self.strategy_config.name}] Запуск WebSocket подписок...")
        
        # Собираем уникальные комбинации symbol+interval
        subscriptions: Set[tuple] = set()
        
        for signal_config in self.websocket_signals.values():
            # Index пара
            subscriptions.add((signal_config.index, signal_config.frame))
            
            # Target пары (все торговые пары стратегии)
            for trade_pair in self.strategy_config.trade_pairs:
                subscriptions.add((trade_pair, signal_config.frame))
        
        # Выполняем подписки
        for symbol, interval in subscriptions:
            try:
                await self.ws_client.subscribe_kline(
                    category=self.strategy_config.get_market_category(),
                    symbol=symbol,
                    interval=interval,
                    callback=self._ws_callback
                )
                logger.debug(f"   ✓ WS подписка: {symbol} @ {interval}")
                
            except Exception as e:
                logger.error(f"[{self.strategy_config.name}] Ошибка WS подписки {symbol}@{interval}: {e}")
        
        logger.info(f"[{self.strategy_config.name}] ✅ WebSocket: {len(subscriptions)} подписок активно")

    async def _ws_callback(self, symbol: str, kline: Kline):
        """Callback для WebSocket данных"""
        if self.kline_callback and kline.confirm:
            await self.kline_callback(symbol, kline)

    # ========== REST Polling ==========
    
    async def _start_polling_tasks(self):
        """Запуск polling задач для секундных интервалов"""
        
        if not self.polling_signals:
            logger.info(f"[{self.strategy_config.name}] Нет polling сигналов")
            return
        
        logger.info(f"[{self.strategy_config.name}] Запуск polling задач...")
        
        self.polling_active = True
        
        # Группируем сигналы по интервалу для оптимизации
        interval_groups: Dict[str, List[SignalConfig]] = {}
        for signal_config in self.polling_signals.values():
            if signal_config.frame not in interval_groups:
                interval_groups[signal_config.frame] = []
            interval_groups[signal_config.frame].append(signal_config)
        
        # Создаем задачу для каждого уникального интервала
        for frame, signal_configs in interval_groups.items():
            interval_seconds = self._frame_to_seconds(frame)
            
            task_name = f"polling_{frame}"
            task = asyncio.create_task(
                self._polling_loop(frame, signal_configs, interval_seconds),
                name=task_name
            )\n            self.polling_tasks[task_name] = task
            
            logger.info(f"   📡 Polling {frame} ({interval_seconds}s): {len(signal_configs)} сигналов")
        
        logger.info(f"[{self.strategy_config.name}] ✅ Polling: {len(self.polling_tasks)} задач запущено")

    async def _stop_polling_tasks(self):
        """Остановка всех polling задач"""
        
        self.polling_active = False
        
        for task_name, task in self.polling_tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                
        self.polling_tasks.clear()
        logger.info(f"[{self.strategy_config.name}] Polling задачи остановлены")

    async def _polling_loop(
        self, 
        frame: str, 
        signal_configs: List[SignalConfig], 
        interval_seconds: int
    ):
        """Основной цикл polling для конкретного интервала"""
        
        while self.polling_active:
            try:
                # Rate limiting
                now = time.time()
                last_poll = self.last_poll_times.get(frame, 0)
                
                if now - last_poll < interval_seconds:
                    await asyncio.sleep(interval_seconds - (now - last_poll))
                
                # Получаем данные для всех пар этого интервала
                await self._poll_frame_data(frame, signal_configs)
                
                self.last_poll_times[frame] = time.time()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.strategy_config.name}] Polling ошибка {frame}: {e}")
                await asyncio.sleep(5)

    async def _poll_frame_data(self, frame: str, signal_configs: List[SignalConfig]):
        """Получение данных через REST для конкретного frame"""
        
        # Собираем уникальные пары для этого frame
        unique_symbols: Set[str] = set()
        
        for signal_config in signal_configs:
            unique_symbols.add(signal_config.index)
            for trade_pair in self.strategy_config.trade_pairs:
                unique_symbols.add(trade_pair)
        
        # Получаем ticker для каждой уникальной пары
        for symbol in unique_symbols:
            try:
                ticker = await self.rest_client.get_ticker(
                    category=self.strategy_config.get_market_category(),
                    symbol=symbol
                )
                
                if ticker:
                    # Конвертируем ticker в Kline и отправляем в callback
                    kline = self._ticker_to_kline(ticker)
                    if self.kline_callback:
                        await self.kline_callback(symbol, kline)
                        
            except Exception as e:
                logger.error(f"[{self.strategy_config.name}] Ошибка получения {symbol} @ {frame}: {e}")

    @staticmethod
    def _ticker_to_kline(ticker_data: dict) -> Kline:
        """Конвертация ticker в Kline объект"""
        
        # Извлекаем данные из ответа API
        if 'result' in ticker_data and 'list' in ticker_data['result']:
            ticker = ticker_data['result']['list'][0]
        else:
            ticker = ticker_data
            
        last_price = float(ticker.get("lastPrice", 0))
        high_price = float(ticker.get("highPrice24h", last_price))
        low_price = float(ticker.get("lowPrice24h", last_price))
        volume = float(ticker.get("volume24h", 0))

        return Kline(
            timestamp=int(time.time() * 1000),
            open=last_price,  # Для polling используем последнюю цену
            high=high_price,
            low=low_price,
            close=last_price,
            volume=volume,
            confirm=True  # Всегда подтвержденная для polling
        )

    @staticmethod  
    def _frame_to_seconds(frame: str) -> int:
        """Конвертация frame в секунды"""
        if frame.endswith("s"):
            return int(frame[:-1])
        if frame == "D":
            return 86400
        if frame == "W":
            return 604800  
        if frame == "M":
            return 2592000
        return int(frame) * 60


# Сохраняем старый MarketDataProvider для обратной совместимости
class MarketDataProvider:
    """
    Унифицированный провайдер рыночных данных (legacy для PairConfig)

    Автоматически выбирает источник:
    - WebSocket для интервалов ≥ 1 минута
    - REST API polling для интервалов < 1 минута
    """

    WS_MODE = "websocket"
    POLLING_MODE = "polling"

    def __init__(
            self,
            config,  # PairConfig или аналог
            rest_client: BybitClient,
            ws_client: BybitWebSocketClient,
    ):
        self.config = config
        self.rest_client = rest_client
        self.ws_client = ws_client

        # Режим работы
        self.mode = self.WS_MODE if config.uses_websocket() else self.POLLING_MODE

        # Для polling режима
        self.polling_task: asyncio.Task | None = None
        self.polling_active = False
        self.last_poll_time = 0
        self.poll_interval = config.get_polling_interval_seconds()

        # Callbacks
        self.dominant_callback: Callable | None = None
        self.target_callback: Callable | None = None

        logger.info(
            f"[{config.name}] Market data mode: {self.mode.upper()} "
            f"(interval: {config.timeframe})"
        )

    async def start(self):
        """Запуск получения данных"""
        if self.mode == self.WS_MODE:
            await self._start_websocket()
        else:
            await self._start_polling()

    async def stop(self):
        """Остановка получения данных"""
        if self.mode == self.POLLING_MODE:
            await self._stop_polling()

    def set_callbacks(
            self,
            dominant_callback: Callable,
            target_callback: Callable,
    ):
        """Установка callbacks для обработки klines"""
        self.dominant_callback = dominant_callback
        self.target_callback = target_callback

    # ========== WebSocket режим ==========

    async def _start_websocket(self):
        """Запуск WebSocket подписок"""
        logger.info(f"[{self.config.name}] Starting WebSocket subscriptions...")

        # Подписка на доминирующую пару
        await self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.dominant_pair,
            interval=self.config.timeframe,
            callback=self._ws_dominant_callback
        )

        # Подписка на целевую пару
        await self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.target_pair,
            interval=self.config.timeframe,
            callback=self._ws_target_callback
        )

        logger.info(f"[{self.config.name}] ✅ WebSocket subscriptions active")

    async def _ws_dominant_callback(self, symbol: str, kline: Kline):
        """Обработка kline доминирующей пары из WebSocket"""
        if self.dominant_callback and kline.confirm:
            await self.dominant_callback(symbol, kline)

    async def _ws_target_callback(self, symbol: str, kline: Kline):
        """Обработка kline целевой пары из WebSocket"""
        if self.target_callback and kline.confirm:
            await self.target_callback(symbol, kline)

    # ========== REST API Polling режим ==========

    async def _start_polling(self):
        """Запуск REST API polling"""
        logger.info(
            f"[{self.config.name}] Starting REST API polling "
            f"(every {self.poll_interval}s)..."
        )

        self.polling_active = True
        self.polling_task = asyncio.create_task(self._polling_loop())

        logger.info(f"[{self.config.name}] ✅ Polling active")

    async def _stop_polling(self):
        """Остановка polling"""
        self.polling_active = False

        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass

        logger.info(f"[{self.config.name}] Polling stopped")

    async def _polling_loop(self):
        """Основной цикл polling"""
        while self.polling_active:
            try:
                # Проверяем rate limiting
                now = time.time()
                time_since_last = now - self.last_poll_time

                if time_since_last < self.poll_interval:
                    await asyncio.sleep(self.poll_interval - time_since_last)

                # Получаем тикеры (текущие цены)
                await self._poll_tickers()

                self.last_poll_time = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.config.name}] Polling error: {e}")
                await asyncio.sleep(5)

    async def _poll_tickers(self):
        """Получение текущих цен через REST API"""
        try:
            # Получаем тикеры для обеих пар одновременно
            dominant_ticker = await self.rest_client.get_ticker(
                category=self.config.get_market_category(),
                symbol=self.config.dominant_pair
            )

            target_ticker = await self.rest_client.get_ticker(
                category=self.config.get_market_category(),
                symbol=self.config.target_pair
            )

            # Конвертируем в Kline формат и вызываем callbacks
            if dominant_ticker and self.dominant_callback:
                kline = self._ticker_to_kline(dominant_ticker)
                await self.dominant_callback(self.config.dominant_pair, kline)

            if target_ticker and self.target_callback:
                kline = self._ticker_to_kline(target_ticker)
                await self.target_callback(self.config.target_pair, kline)

        except Exception as e:
            logger.error(f"[{self.config.name}] Error polling tickers: {e}")

    @staticmethod
    def _ticker_to_kline(ticker: dict) -> Kline:
        """Конвертация ticker в Kline объект"""
        
        # Извлекаем данные из ответа API
        if 'result' in ticker and 'list' in ticker['result']:
            ticker_data = ticker['result']['list'][0]
        else:
            ticker_data = ticker
            
        last_price = float(ticker_data.get("lastPrice", 0))
        high_price = float(ticker_data.get("highPrice24h", last_price))
        low_price = float(ticker_data.get("lowPrice24h", last_price))
        volume = float(ticker_data.get("volume24h", 0))

        return Kline(
            timestamp=int(time.time() * 1000),
            open=last_price,  # Для polling используем последнюю цену
            high=high_price,
            low=low_price,
            close=last_price,
            volume=volume,
            confirm=True  # Всегда подтвержденная для polling
        )