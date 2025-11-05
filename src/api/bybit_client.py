from __future__ import annotations

import asyncio
import functools
import time

from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar, ParamSpec, Coroutine, Callable, Literal, Optional, Dict

from pybit.unified_trading import HTTP

from .common import Kline, Singleton
from ..logger import get_app_logger

logger = get_app_logger()


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

        # cache for instruments info
        self._instrument_cache: Dict[str, Dict[str, Any]] = {}
        self._instrument_cache_ts: Dict[str, float] = {}
        self._instrument_ttl_sec: int = 300

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
                result.reverse()
                logger.info(f"Loaded {len(result)} klines for {symbol} ({interval})")
                return result

            logger.error(f"Get klines error: {response.get('retMsg')}")
            return []

        except Exception as e:
            logger.error(f"Exception getting klines: {e}")
            return []

    @async_wrap
    def get_ticker(self, category: str, symbol: str) -> dict[str, Any] | None:
        """Получение текущего тикера (для sub-minute polling)"""
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

    # ====== Instruments info / normalization ======

    @async_wrap
    def get_instruments_info(self, category: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Обертка над v5/market/instruments-info. Возвращает спецификацию символа.
        Возвращает dict: qtyStep, minOrderQty, tickSize, minNotional (если доступно).
        Кэшируется на _instrument_ttl_sec.
        """
        self._init_session()
        import time as _t

        cache_key = f"{category}:{symbol}"
        now = _t.time()
        if cache_key in self._instrument_cache and now - self._instrument_cache_ts.get(cache_key, 0) < self._instrument_ttl_sec:
            return self._instrument_cache[cache_key]

        try:
            self.request_count += 1
            resp = self.session.get_instruments_info(category=category, symbol=symbol)
            if resp.get("retCode") == 0:
                items = resp.get("result", {}).get("list", [])
                if items:
                    it = items[0]
                    lot = it.get("lotSizeFilter", {})
                    price = it.get("priceFilter", {})
                    spec = {
                        "qtyStep": float(lot.get("qtyStep", 0) or 0),
                        "minOrderQty": float(lot.get("minOrderQty", 0) or 0),
                        "tickSize": float(price.get("tickSize", 0) or 0),
                        # minNotional может отсутствовать; на тестнете часто используется порог 5 USDT
                        "minNotional": float(lot.get("minNotional", 0) or 5.0),
                    }
                    self._instrument_cache[cache_key] = spec
                    self._instrument_cache_ts[cache_key] = now
                    return spec
            logger.error(f"Get instruments info error for {symbol}: {resp.get('retMsg')}")
            return None
        except Exception as e:
            self.error_count += 1
            logger.error(f"Exception get_instruments_info for {symbol}: {e}")
            return None

    @staticmethod
    def _decimal_places(step: float) -> int:
        s = f"{step:.12f}".rstrip("0").rstrip(".")
        if "." in s:
            return len(s.split(".")[1])
        return 0

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        import math
        return math.floor(value / step) * step

    @staticmethod
    def _ceil_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        import math
        return math.ceil(value / step) * step

    async def normalize_order(
        self,
        *,
        category: str,
        symbol: str,
        side: str,
        last_price: float,
        position_size_usdt: float,
        take_profit: float,
        stop_loss: float,
    ) -> Optional[Dict[str, Any]]:
        """Рассчитывает qty по правилам инструмента, нормализует qty и цены по шагам.
        Возвращает словарь с полями qty_str, tp_str, sl_str, qty, tp, sl или None при ошибке.
        """
        spec = await self.get_instruments_info(category, symbol)
        if spec is None:
            spec = {"qtyStep": 1.0, "minOrderQty": 1.0, "tickSize": 0.0001, "minNotional": 5.0}

        qty_step = float(spec.get("qtyStep", 1.0) or 1.0)
        min_qty = float(spec.get("minOrderQty", qty_step) or qty_step)
        tick = float(spec.get("tickSize", 0.0001) or 0.0001)
        min_notional = float(spec.get("minNotional", 5.0) or 5.0)

        raw_qty = position_size_usdt / max(last_price, 1e-12)
        qty = self._floor_to_step(raw_qty, qty_step)
        if qty < min_qty:
            qty = min_qty
        if qty * last_price < min_notional:
            qty = self._ceil_to_step(min_notional / max(last_price, 1e-12), qty_step)
        if qty <= 0:
            logger.error(f"Normalized qty is zero for {symbol} (raw={raw_qty}, step={qty_step})")
            return None

        # Нормализация TP/SL по tickSize с учётом стороны
        if side.lower() == "buy":
            tp = self._floor_to_step(take_profit, tick)
            sl = self._ceil_to_step(stop_loss, tick)
        else:
            tp = self._ceil_to_step(take_profit, tick)
            sl = self._floor_to_step(stop_loss, tick)

        qty_dp = self._decimal_places(qty_step)
        price_dp = self._decimal_places(tick)

        return {
            "qty": qty,
            "qty_str": f"{qty:.{qty_dp}f}",
            "tp": tp,
            "sl": sl,
            "tp_str": f"{tp:.{price_dp}f}",
            "sl_str": f"{sl:.{price_dp}f}",
            "steps": {"qty_step": qty_step, "tick": tick, "min_qty": min_qty, "min_notional": min_notional},
        }

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
