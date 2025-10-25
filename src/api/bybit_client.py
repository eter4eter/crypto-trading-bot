import asyncio
import functools
import time

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from pybit.unified_trading import HTTP

from .common import Kline
from ..logger import logger


def async_wrap(func):
    """Декоратор для async выполнения sync функций"""
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: func(self, *args, **kwargs)
        )
    return wrapper


def retry(max_attempts: int = 3, delay: float = 1.0):
    """Декоратор для повторных попыток"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(self, *args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logger.warning(f"{func.__name__} attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(delay * (attempt + 1))
        return wrapper
    return decorator


class BybitClient:
    instance: "BybitClient" | None = None

    def __new__(cls, *args, **kwargs):
        if cls.instance:
            return cls.instance
        else:
            cls.instance = super().__new__(cls, *args, **kwargs)
            return cls.instance

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        self.session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

        self._executor = ThreadPoolExecutor(max_workers=10)

        self.request_count = 0
        self.error_count = 0
        self.last_request_time = 0

        logger.info(f"BybitClient initialized. Testnet: {testnet}")

    @async_wrap
    def get_klines(
            self,
            category: str,
            symbol: str,
            interval: str,
            limit: int = 200
    ) -> list[Kline]:
        """
        Получение исторических свечей (klines)

        Args:
            category: Product type. spot,linear,inverse
                    When category is not passed, use linear by default
            symbol: Symbol name, like BTCUSDT, uppercase only
            interval: Timeframe (1,3,5,15,30,60,120,240,360,720,D,W,M)
            limit: Количество свечей (макс 200)

        Returns:
            List кортежей (timestamp, open, high, low, close, volume)
        """
        try:
            response = self.session.get_kline(
                category=category,
                symbol=symbol,
                interval=interval,
                limit=limit
            )

            if response['retCode'] == 0:
                klines = response["result"]["list"]

                # Формат: [timestamp, open, high, low, close, volume, turnover]
                # Преобразуем в удобный формат
                result = []
                for kline in klines:
                    result.append(Kline(
                        timestamp=int(kline[0]),
                        open=float(kline[1]),
                        high=float(kline[2]),
                        low=float(kline[3]),
                        close=float(kline[4]),
                        volume=float(kline[5]),
                        confirm=False,
                    ))

                # Bybit возвращает в обратном порядке, разворачиваем
                result.reverse()

                logger.info(f"Loaded {len(result)} klines for {symbol} ({interval})")
                return result

            logger.error(f"Get klines error: {response.get('retMsg')}")
            return []

        except Exception as e:
            logger.error(f"Exception getting klines: {e}")
            return []

    @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_ticker_price(self, category: str, symbol: str) -> float | None:
        """Получение цены тикера с retry"""
        try:
            self.request_count += 1
            self.last_request_time = time.time()

            response = self.session.get_tickers(category=category, symbol=symbol)

            if response["retCode"] == 0 and response["result"]["list"]:
                return float(response["result"]["list"][0]["lastPrice"])

            logger.warning(f"Get ticker error for {symbol}: {response.get('retMsg', 'Unknown')}")
            return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception getting ticker {symbol}: {e}")
            raise

    @retry(max_attempts=3, delay=2)
    @async_wrap
    def set_leverage(self, category: str, symbol: str, leverage: int) -> bool:
        """Установка плеча с retry"""
        try:
            self.request_count += 1
            lev = str(leverage)

            response = self.session.set_leverage(
                category=category,
                symbol=symbol,
                buyLeverage=lev,
                sellLeverage=lev
            )

            success = response["retCode"] == 0

            if success:
                logger.info(f"Leverage set for {symbol}: {leverage}x")
            else:
                logger.error(f"Set leverage error for {symbol}: {response.get('retMsg')}")

            return success

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception setting leverage for {symbol}: {e}")
            raise

    @retry(max_attempts=3, delay=2)
    @async_wrap
    def place_market_order(
            self,
            category: str,
            symbol: str,
            side: str,
            qty: str,
            take_profit: str | None = None,
            stop_loss: str | None = None,
            position_idx: int = 0
    ) -> dict[str, Any] | None:
        """Размещение рыночного ордера с TP/SL"""
        try:
            self.request_count += 1

            params = {
                "category": category,
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": qty,
                "positionIdx": position_idx
            }

            if take_profit:
                params["takeProfit"] = take_profit
            else:
                logger.warning(f"Market order without take profit for {symbol}")

            if stop_loss:
                params["stopLoss"] = stop_loss
            else:
                logger.warning(f"Market order without stop loss for {symbol}")

            response = self.session.place_order(**params)

            if response["retCode"] == 0:
                result = response["result"]
                logger.info(f"Order placed: {side} {qty} {symbol}")
                logger.debug(f"  Order ID: {result.get('orderId')}")
                if take_profit:
                    logger.debug(f"  TP: {take_profit}")
                if stop_loss:
                    logger.debug(f"  SL: {stop_loss}")
                return result

            logger.error(f"Place order error: {response.get('retMsg')}")
            return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception placing order for {symbol}: {e}")
            raise

    @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_position(self, category: str, symbol: str) -> dict[str, Any] | None:
        """Получение позиции"""
        try:
            self.request_count += 1

            response = self.session.get_positions(
                category=category,
                symbol=symbol
            )

            if response["retCode"] == 0 and response["result"]["list"]:
                for pos in response["result"]["list"]:
                    if float(pos.get("size", 0)) > 0:
                        return pos

            return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception getting position {symbol}: {e}")
            return None

    @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_order_history(
            self,
            category: str,
            symbol: str | None = None,
            limit: int = 20
    ) -> list[dict[str, Any]]:
        """Получение истории ордеров"""
        try:
            self.request_count += 1

            params = {
                "category": category,
                "limit": limit
            }

            if symbol:
                params["symbol"] = symbol

            response = self.session.get_order_history(**params)

            if response["retCode"] == 0:
                return response["result"]["list"]

            return []

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception getting order history: {e}")
            return []

    @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any] | None:
        """Получение баланса кошелька"""
        try:
            self.request_count += 1

            response = self.session.get_wallet_balance(accountType=account_type)

            if response["retCode"] == 0:
                return response["result"]

            return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception getting wallet balance: {e}")
            return None

    async def close(self):
        """Закрытие соединений"""
        self._executor.shutdown(wait=True)
        logger.info(f"BybitClient closed. Total requests: {self.request_count}, Errors: {self.error_count}")

    def get_stats(self) -> dict[str, Any]:
        """Статистика клиента"""
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": f"{(self.error_count / max(self.request_count, 1)) * 100:.2f}%",
            "last_request_time": self.last_request_time
        }
