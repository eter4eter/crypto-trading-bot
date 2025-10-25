import asyncio
from collections import defaultdict
from typing import Callable, Any

from pybit.unified_trading import WebSocket

from .common import Kline
from ..logger import logger


class BybitWebSocketClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        # WebSocket соединения
        self.ws_connections: dict[str, WebSocket] = {}

        # Callbacks для klines
        self.kline_callbacks: dict[str, list] = defaultdict(list)

        # Статистика
        self.messages_received = 0
        self.connected = False

        logger.info(f"BybitWebSocketClient initialized (testnet={testnet})")

    async def connect(self):
        """Подключение к WebSocket"""
        self.connected = True
        logger.info("✅ WebSocket ready")

    def subscribe_kline(
            self,
            category: str,
            symbol: str,
            interval: str,
            callback: Callable
    ):
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
        """

        ws_key = f"{category}_{symbol}_{interval}"

        # Создаем WebSocket для этой подписки
        ws = WebSocket(
            testnet=self.testnet,
            channel_type=category
        )

        self.ws_connections[ws_key] = ws
        self.kline_callbacks[ws_key].append(callback)

        # Подписываемся на kline
        ws.kline_stream(
            interval=interval,
            symbol=symbol,
            callback=lambda msg: asyncio.create_task(
                self._handle_kline(ws_key, symbol, msg)
            )
        )

        logger.info(f"📊 Subscribed to kline: {symbol} ({interval} @ {category})")

    async def _handle_kline(self, ws_key: str, symbol: str, message: dict):
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

            # Формат сообщения от Bybit WebSocket
            if message.get("topic", "").startswith("kline"):
                data = message.get("data", [])

                if data:
                    kline_data = data[0] if isinstance(data, list) else data

                    # Извлекаем данные свечи
                    # kline = {
                    #     "timestamp": int(kline_data.get("start", 0)),
                    #     "open": float(kline_data.get("open", 0)),
                    #     "high": float(kline_data.get("high", 0)),
                    #     "low": float(kline_data.get("low", 0)),
                    #     "close": float(kline_data.get("close", 0)),
                    #     "volume": float(kline_data.get("volume", 0)),
                    #     "confirm": kline_data.get("confirm", False)  # True когда свеча закрылась
                    # }
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
                    for callback in self.kline_callbacks[ws_key]:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(symbol, kline)
                            else:
                                callback(symbol, kline)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")

                    # if kline['confirm']:
                    if kline.confirm:
                        logger.debug(f"📊 {symbol} kline closed: ${kline.close:.8f}")

        except Exception as e:
            logger.error(f"Error handling kline: {e}")

    def close(self):
        """Закрытие WebSocket соединений"""
        self.connected = False
        self.ws_connections.clear()
        logger.info("WebSocket connections closed")

    def get_stats(self) -> dict[str, Any]:
        """Статистика WebSocket"""
        return {
            "connected": self.connected,
            "messages_received": self.messages_received,
            "active_subscriptions": len(self.ws_connections)
        }
