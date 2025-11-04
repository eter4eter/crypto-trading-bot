from __future__ import annotations

import asyncio
import functools
import time

from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar, ParamSpec, Coroutine, Callable, Literal

from pybit.unified_trading import HTTP

from .common import Kline, Singleton
from ..logger import logger


P = ParamSpec("P")
R = TypeVar("R")


def async_wrap(func: Callable[P, R]) -> Callable[P, Coroutine[Any, Any, R]]:
    """Декоратор для async выполнения sync функций"""
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        self = args[0]
        loop = asyncio.get_running_loop()
        partial_func = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._executor, partial_func)
        # return await loop.run_in_executor(
        #     self._executor,
        #     lambda: func(*args, **kwargs)
        # )

    return wrapper


class BybitClient(metaclass=Singleton):

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, demo: bool = False):
        self.api_key: str = api_key
        self.api_secret: str = api_secret
        self.testnet: bool = testnet
        self.demo: bool = demo

        self.session: HTTP | None = None

        self._executor = ThreadPoolExecutor(max_workers=10)
        self._initialized: bool = False

        self.request_count = 0
        self.error_count = 0
        self.last_request_time = 0

        logger.info(f"BybitClient initialized. Testnet: {testnet}")

    def _init_session(self):
        if self.session is None:
            self.session = HTTP(
                testnet=self.testnet,
                api_key=self.api_key,
                api_secret=self.api_secret,
                demo=self.demo,
            )
            self._initialized = True

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
        self._init_session()

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

    # @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_ticker(self, category: str, symbol: str) -> dict[str, Any] | None:
        """
        Получение текущего тикера (для sub-minute polling)

        Args:
            category: "spot" или "linear"
            symbol: Символ пары (e.g., "BTCUSDT")

        Returns:
            dict: Информация о тикере или None при ошибке
            {
                "symbol": "BTCUSDT",
                "lastPrice": "50000.00",
                "highPrice24h": "51000.00",
                "lowPrice24h": "49000.00",
                "volume24h": "12345.67",
                "turnover24h": "616728405.23",
                "bid1Price": "49999.99",
                "ask1Price": "50000.01",
                ...
            }
        """
        self._init_session()

        try:
            self.request_count += 1
            self.last_request_time = time.time()

            response = self.session.get_tickers(category=category, symbol=symbol)

            if response["retCode"] == 0:
                tickers = response.get('result', {}).get('list', [])
                if tickers:
                    ticker = tickers[0]
                    logger.debug(
                        f"Got ticker for {symbol}: "
                        f"${ticker.get('lastPrice')} "
                        f"(high: ${ticker.get('highPrice24h')}, "
                        f"low: ${ticker.get('lowPrice24h')})"
                    )
                    return ticker

                logger.warning(f"No ticker data for {symbol}")
                return None

            else:
                logger.error(
                    f"Get ticker error for {symbol}: "
                    f"({response.get('retCode')}) {response.get('retMsg')}"
                )
                return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception getting ticker for {symbol}: {e}", exc_info=True)
            return None

    # @retry(max_attempts=3, delay=2)
    @async_wrap
    def set_leverage(self, category: str, symbol: str, leverage: int) -> bool:
        """Установка плеча с retry"""
        self._init_session()

        try:
            self.request_count += 1
            lev = str(leverage)

            response = self.session.set_leverage(
                category=category,
                symbol=symbol,
                buyLeverage=lev,
                sellLeverage=lev,
            )

            ret_code = response.get("retCode", 0)

            if ret_code == 0:
                logger.info(f"✓ Leverage set for {symbol}: {leverage}x")
                return True

            elif ret_code == 110043:
                # 1. Плечо уже установлено (OK)
                # 2. Spot пара (ожидаемо)
                logger.debug(f"Leverage already set or not applicable for {symbol}")
                return True

            else:
                logger.error(
                    f"✗ Set leverage error for {symbol} "
                    f"(code {ret_code}): {response.get('retMsg')}"
                )
                return False

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception setting leverage for {symbol}: {e}")
            return False

    # @retry(max_attempts=3, delay=2)
    @async_wrap
    def place_market_order(
            self,
            category: str,
            symbol: str,
            side: Literal["Buy", "Sell"],
            qty: str,
            take_profit: str | None = None,
            stop_loss: str | None = None,
            position_idx: int = 0
    ) -> dict[str, Any] | None:
        """Размещение рыночного ордера с TP/SL"""
        self._init_session()

        try:
            if not qty or float(qty) <= 0:
                logger.error(f"Invalid quantity for {symbol}: {qty}")
                return None

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
                logger.info(f"✅ Order placed: {side} {qty} {symbol}")
                logger.debug(f"  Order ID: {result.get('orderId')}")
                if take_profit:
                    logger.debug(f"  TP: {take_profit}")
                if stop_loss:
                    logger.debug(f"  SL: {stop_loss}")
                return result

            logger.error(f"❌ Place order error: {response.get('retMsg')}")
            return None

        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception placing order for {symbol}: {e}")
            return None

    # @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_position(self, category: str, symbol: str) -> dict[str, Any] | None:
        """Получение позиции"""
        self._init_session()

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

    # @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_order_history(
            self,
            category: str,
            symbol: str | None = None,
            limit: int = 20
    ) -> list[dict[str, Any]]:
        """Получение истории ордеров"""
        self._init_session()

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

    # @retry(max_attempts=3, delay=1)
    @async_wrap
    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict[str, Any] | None:
        """Получение баланса кошелька"""
        self._init_session()

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
