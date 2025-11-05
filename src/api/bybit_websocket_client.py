from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Callable, Any

from pybit.unified_trading import WebSocket as _WebSocket

from .common import Kline, Singleton
from ..logger import get_app_logger

logger = get_app_logger()


class WebSocket(_WebSocket):
    def kline_stream(self, interval: str | int, symbol: str | list, callback):
        """Subscribe to the klines stream.

        Push frequency: 1-60s

        Required args:
            symbol (string/list): Symbol name(s)
            interval (str/int): Kline interval
            Available intervals:
                1 3 5 15 30 (min)
                60 120 240 360 720 (min)
                D (day)
                W (week)
                M (month)

         Additional information:
            https://bybit-exchange.github.io/docs/v5/websocket/public/kline
        """
        self._validate_public_topic()
        topic = f"kline.{interval}.{symbol}"
        self.subscribe(topic, callback, symbol)


class BybitWebSocketClient(metaclass=Singleton):

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, demo: bool = False):
        self.api_key: str = api_key
        self.api_secret: str = api_secret
        self.testnet: bool = testnet
        self.demo: bool = demo

        # WebSocket соединения
        self.ws_connections: dict[str, WebSocket] = {}
        self.reconnect_tasks: dict[str, asyncio.Task] = {}

        # Callbacks для klines
        self.kline_callbacks: dict[str, list] = defaultdict(list)

        # Статистика
        self.messages_received: int = 0
        self.last_message_times: dict[str, float] = {}
        self.connected: bool = False

        # reconnect
        self.reconnect_delay: int = 5
        self.max_reconnect_attempts: int = 10

        self._loop: asyncio.AbstractEventLoop | None = None

        logger.info(f"BybitWebSocketClient initialized (testnet={testnet})")

    async def connect(self) -> None:
        """Подключение к WebSocket"""
        self._loop = asyncio.get_running_loop()
        self.connected = True
        logger.info("✅ WebSocket ready")

    async def subscribe_kline(
        self,
        category: str,
        symbol: str,
        interval: str,
        callback: Callable[[str, Kline], None]
    ) -> bool:
        """
        Подписка на kline stream

        Args:
            category: "spot" или "linear"
            symbol: Символ пары
            interval: Timeframe Available intervals:
                    1 3 5 15 30 (min)
                    60 120 240 360 720 (min)
                    D (day)
                    W (week)
                    M (month)
            callback: Функция обратного вызова
            
        Returns:
            bool: True если подписка создана
        """
        try:
            ws_key = f"{category}_{symbol}_{interval}"

            # Создаём WebSocket для этой подписки
            ws = WebSocket(
                testnet=self.testnet,
                channel_type=category,
            )

            self.ws_connections[ws_key] = ws
            self.kline_callbacks[ws_key].append(callback)

            # Подписываемся на kline
            ws.kline_stream(
                interval=interval,
                symbol=symbol,
                callback=lambda msg: self._handle_kline(ws_key, symbol, msg),
            )

            logger.info(f"📊 Subscribed to kline: {symbol} ({interval} @ {category})")
            
            # Запускаем watchdog
            self._start_watchdog(ws_key, category, symbol, interval, callback)
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка создания WS подписки {symbol}@{interval}[{category}]: {e}")
            return False

    def _handle_kline(self, ws_key: str, symbol: str, message: dict) -> None:
        """
        Обработка kline сообщения
        msg: dict {
            "topic": "kline.5.BTCUSDT",
            "data": [
                {
                    "start": 1672324800000,
                    "end": 1672325099999,
                    "interval": "5",
                    "open": "16649.5",
                    "close": "16677",
                    "high": "16677",
                    "low": "16608",
                    "volume": "2.081",
                    "turnover": "34666.4005",
                    "confirm": false,
                    "timestamp": 1672324988882
                }
            ],
            "ts": 1672324988882,
            "type": "snapshot"
        }
        """

        try:
            self.messages_received += 1
            self.last_message_times[ws_key] = time.time()

            # Формат сообщения от Bybit WebSocket
            if message.get("topic", "").startswith("kline"):
                data = message.get("data", [])

                if data:
                    kline_data = data[0] if isinstance(data, list) else data

                    # Извлекаем данные свечи
                    kline = Kline(
                        timestamp=int(kline_data.get("start", 0)),
                        open=float(kline_data.get("open", 0)),
                        high=float(kline_data.get("high", 0)),
                        low=float(kline_data.get("low", 0)),
                        close=float(kline_data.get("close", 0)),
                        volume=float(kline_data.get("volume", 0)),
                        confirm=kline_data.get("confirm", False)  # True когда свеча закрылась
                    )

                    # Вызываем callbacks
                    if self._loop is not None:
                        for callback in self.kline_callbacks[ws_key]:
                            try:
                                # Проверяем что это async функция
                                if asyncio.iscoroutinefunction(callback):
                                    try:
                                        asyncio.run_coroutine_threadsafe(
                                            callback(symbol, kline),
                                            self._loop
                                        )
                                    except RuntimeError:
                                        break
                                else:
                                    # Sync callback - вызываем напрямую
                                    callback(symbol, kline)

                            except Exception as e:
                                logger.error(f"Callback error: {e}", exc_info=True)

                    if kline.confirm:
                        logger.debug(f"📊 {symbol} kline closed: ${kline.close:.8f}")

        except Exception as e:
            logger.error(f"Error handling kline: {e}")

    def _start_watchdog(self, ws_key: str, category: str, symbol: str, interval: str, callback: Callable) -> None:
        """Запуск watchdog для отслеживания разрывов"""

        async def watchdog() -> None:
            """Проверка активности соединения"""
            reconnect_count = 0

            while self.connected and reconnect_count < self.max_reconnect_attempts:
                await asyncio.sleep(30)

                last_message_time = self.last_message_times.get(ws_key, 0)
                time_since_last = time.time() - last_message_time

                if time_since_last > 70:
                    logger.warning(f"⚠️ No messages for {ws_key}, reconnecting...")

                    try:
                        if ws_key in self.ws_connections:
                            del self.ws_connections[ws_key]

                        await self.subscribe_kline(category, symbol, interval, callback)
                        reconnect_count += 1

                        logger.info(f"✅ Reconnected {ws_key} (attempt {reconnect_count})")

                    except Exception as e:
                        logger.error(f"Reconnect failed: {e}")
                        await asyncio.sleep(self.reconnect_delay)

        task = asyncio.create_task(watchdog())
        self.reconnect_tasks[ws_key] = task

    async def close(self) -> None:
        """Закрытие WebSocket соединений"""
        self.connected = False

        # Отменяем все watchdog tasks
        for task in self.reconnect_tasks.values():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Закрываем WebSocket соединения
        for ws in self.ws_connections.values():
            try:
                if hasattr(ws, 'exit'):
                    ws.exit()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {e}")

        self.ws_connections.clear()
        self.reconnect_tasks.clear()

        logger.info("WebSocket connections closed")

    def get_stats(self) -> dict[str, Any]:
        """Статистика WebSocket"""
        return {
            "connected": self.connected,
            "messages_received": self.messages_received,
            "active_subscriptions": len(self.ws_connections),
            "watchdog_tasks": len(self.reconnect_tasks),
        }
