import asyncio
import time
from typing import Callable, Any
from datetime import datetime

from backup.strategies.base_strategy import MarketData
from ..logger import logger
from ..config import PairConfig
from .bybit_client import BybitClient
from .bybit_websocket_client import BybitWebSocketClient
from .common import Kline


class MarketDataProvider:
    """
    Унифицированный провайдер рыночных данных

    Автоматически выбирает источник:
    - WebSocket для интервалов ≥ 1 минута
    - REST API polling для интервалов < 1 минута
    """

    WS_MODE = "websocket"
    POLLING_MODE = "polling"

    def __init__(
            self,
            config: PairConfig,
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
        self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.dominant_pair,
            interval=self.config.timeframe,
            callback=self._ws_dominant_callback
        )

        # Подписка на целевую пару
        self.ws_client.subscribe_kline(
            category=self.config.get_market_category(),
            symbol=self.config.target_pair,
            interval=self.config.timeframe,
            callback=self._ws_target_callback
        )

        logger.info(f"[{self.config.name}] ✅ WebSocket subscriptions active")

    def _ws_dominant_callback(self, symbol: str, kline: Kline):
        """Обработка kline доминирующей пары из WebSocket"""
        if self.dominant_callback and kline.confirm:
            # Вызываем async callback из sync контекста
            if self.ws_client._loop:
                asyncio.run_coroutine_threadsafe(
                    self.dominant_callback(symbol, kline),
                    self.ws_client._loop
                )

    def _ws_target_callback(self, symbol: str, kline: Kline):
        """Обработка kline целевой пары из WebSocket"""
        if self.target_callback and kline.confirm:
            # Вызываем async callback из sync контекста
            if self.ws_client._loop:
                asyncio.run_coroutine_threadsafe(
                    self.target_callback(symbol, kline),
                    self.ws_client._loop
                )

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
