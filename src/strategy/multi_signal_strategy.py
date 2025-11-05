from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from ..api.bybit_client import BybitClient
from ..api.bybit_websocket_client import BybitWebSocketClient
from ..api.common import Kline
from ..config import SignalConfig, StrategyConfig
from ..logger import logger


@dataclass
class SignalResult:
    signal_name: str
    strategy_name: str
    action: Literal["Buy", "Sell", "None"]
    index_pair: str
    target_pairs: list[str]
    target_price: float
    index_change: float
    target_change: float
    triggered: bool = False
    slippage_ok: bool = True
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now()


class MultiSignalStrategy:
    """Стратегия без локальных источников. Данные приходят из GlobalMarketDataManager."""

    def __init__(
        self,
        config: StrategyConfig,
        rest_client: BybitClient,
        ws_client: BybitWebSocketClient,
    ) -> None:
        self.config = config
        self.rest_client = rest_client
        self.ws_client = ws_client

        self.signal_buffers: dict[str, dict[str, deque[float]]] = {}
        self.signal_callbacks: dict[str, Callable[[SignalResult], None]] = {}
        self.signal_locks: dict[str, asyncio.Lock] = {}
        self.strategy_callback: Callable[[SignalResult], None] | None = None

        self._initialize_buffers()
        self.signals_generated = 0
        self.history_loaded = False

        logger.info(
            f"✅ MultiSignalStrategy [{config.name}] готова (источник: GlobalMarketDataManager)"
        )

    def _initialize_buffers(self) -> None:
        for signal_name, signal_config in self.config.signals.items():
            window_size = signal_config.tick_window if signal_config.tick_window > 0 else 2
            self.signal_buffers[signal_name] = {
                "index_prices": deque(maxlen=window_size),
                "target_prices": {pair: deque(maxlen=window_size) for pair in self.config.trade_pairs},
            }
            self.signal_locks[signal_name] = asyncio.Lock()

    async def preload_history(self) -> bool:
        logger.info(f"[{self.config.name}] 📅 Загрузка исторических данных...")
        for signal_name, signal_config in self.config.signals.items():
            try:
                limit = max(signal_config.tick_window, 2) if signal_config.tick_window > 0 else 2
                index_klines = await self.rest_client.get_klines(
                    category=self.config.get_market_category(),
                    symbol=signal_config.index,
                    interval=signal_config.frame,
                    limit=limit,
                )
                if not index_klines:
                    logger.error(
                        f"[{self.config.name}] Не удалось загрузить index {signal_config.index} @ {signal_config.frame}"
                    )
                    continue
                target_klines_data: dict[str, list[Kline]] = {}
                for trade_pair in self.config.trade_pairs:
                    kl = await self.rest_client.get_klines(
                        category=self.config.get_market_category(),
                        symbol=trade_pair,
                        interval=signal_config.frame,
                        limit=limit,
                    )
                    if kl:
                        target_klines_data[trade_pair] = kl
                if not target_klines_data:
                    logger.error(f"[{self.config.name}] Нет target данных для {signal_name}")
                    continue
                async with self.signal_locks[signal_name]:
                    buf = self.signal_buffers[signal_name]
                    if signal_config.tick_window > 0:
                        for k in index_klines[:-1]:
                            buf["index_prices"].append(k.close)
                        for pair, kl_list in target_klines_data.items():
                            for k in kl_list[:-1]:
                                buf["target_prices"][pair].append(k.close)
                    else:
                        if len(index_klines) >= 2:
                            buf["index_prices"].append(index_klines[-2].close)
                        for pair, kl_list in target_klines_data.items():
                            if len(kl_list) >= 2:
                                buf["target_prices"][pair].append(kl_list[-2].close)
                logger.info(f"   ✅ {signal_name}: {len(buf['index_prices'])} свечей загружено")
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка загрузки истории {signal_name}: {e}")
        self.history_loaded = True
        return True

    async def start(self) -> None:
        logger.info(f"[{self.config.name}] ✅ Strategy is listening (via GlobalMarketDataManager)")

    async def stop(self) -> None:
        logger.info(f"[{self.config.name}] ⏹ Strategy stopped (buffers will be cleared)")
        for signal_name in list(self.signal_buffers.keys()):
            async with self.signal_locks[signal_name]:
                buf = self.signal_buffers[signal_name]
                buf["index_prices"].clear()
                for dq in buf["target_prices"].values():
                    dq.clear()

    async def _on_kline_data(self, symbol: str, kline: Kline) -> None:
        for signal_name, signal_config in self.config.signals.items():
            try:
                async with self.signal_locks[signal_name]:
                    buf = self.signal_buffers[signal_name]
                    if symbol == signal_config.index:
                        buf["index_prices"].append(kline.close)
                    elif symbol in self.config.trade_pairs and symbol in buf["target_prices"]:
                        buf["target_prices"][symbol].append(kline.close)
                await self._check_signal(signal_name, signal_config)
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка обработки kline {symbol} для {signal_name}: {e}")

    async def _check_signal(self, signal_name: str, signal_config: SignalConfig) -> None:
        buf = self.signal_buffers[signal_name]
        required = signal_config.tick_window if signal_config.tick_window > 0 else 2
        if len(buf["index_prices"]) < required:
            return
        for pair in self.config.trade_pairs:
            if pair not in buf["target_prices"] or len(buf["target_prices"][pair]) < required:
                continue
            if signal_config.tick_window > 0:
                i0, i1 = buf["index_prices"][0], buf["index_prices"][-1]
                t0, t1 = buf["target_prices"][pair][0], buf["target_prices"][pair][-1]
            else:
                i0, i1 = buf["index_prices"][-2], buf["index_prices"][-1]
                t0, t1 = buf["target_prices"][pair][-2], buf["target_prices"][pair][-1]
            if i0 == 0 or t0 == 0:
                continue
            index_change = ((i1 - i0) / i0) * 100
            target_change = ((t1 - t0) / t0) * 100
            if abs(index_change) < signal_config.index_change_threshold:
                continue
            if signal_config.direction != 0:
                if signal_config.direction == 1 and index_change < 0:
                    continue
                if signal_config.direction == -1 and index_change > 0:
                    continue
            if abs(target_change) >= signal_config.target:
                continue
            same_dir = (index_change > 0 and target_change > 0) or (index_change < 0 and target_change < 0)
            if not same_dir:
                continue
            raw_action: Literal["Buy", "Sell"] = "Buy" if index_change > 0 else "Sell"
            action: Literal["Buy", "Sell"] = raw_action if signal_config.reverse == 0 else (
                "Sell" if raw_action == "Buy" else "Buy"
            )
            if not self.config.should_take_signal(action):
                continue
            current_price = await self._get_current_price(pair)
            price_diff_percent = abs((current_price - t1) / t1) * 100 if t1 != 0 else 0.0
            slippage_ok = price_diff_percent <= self.config.price_change_threshold
            signal_result = SignalResult(
                signal_name=signal_name,
                strategy_name=self.config.name,
                action=action,
                index_pair=signal_config.index,
                target_pairs=[pair],
                target_price=current_price,
                index_change=index_change,
                target_change=target_change,
                triggered=True,
                slippage_ok=slippage_ok,
            )
            self.signals_generated += 1
            logger.info("")
            logger.info(f"🎯 СИГНАЛ [{self.config.name}:{signal_name}] {action}")
            logger.info(f"   Index ({signal_config.index}): {index_change:+.3f}%")
            logger.info(f"   Target ({pair}): {target_change:+.3f}%")
            logger.info(f"   Price: ${current_price:.8f} (slippage: {price_diff_percent:.2f}%)")
            logger.info(f"   Frame: {signal_config.frame}, Window: {signal_config.tick_window}")
            logger.info("")
            if signal_name in self.signal_callbacks:
                await self.signal_callbacks[signal_name](signal_result)
            elif self.strategy_callback:
                await self.strategy_callback(signal_result)

    async def _get_current_price(self, symbol: str) -> float:
        try:
            ticker = await self.rest_client.get_ticker(
                category=self.config.get_market_category(),
                symbol=symbol,
            )
            if ticker and "result" in ticker and "list" in ticker["result"]:
                return float(ticker["result"]["list"][0].get("lastPrice", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error getting current price for {symbol}: {e}")
            return 0.0

    def set_signal_callback(self, signal_name: str, callback: Callable[[SignalResult], None]) -> None:
        self.signal_callbacks[signal_name] = callback

    def set_strategy_callback(self, callback: Callable[[SignalResult], None]) -> None:
        self.strategy_callback = callback

    async def reset_buffers(self) -> None:
        for signal_name in self.signal_buffers.keys():
            async with self.signal_locks[signal_name]:
                buf = self.signal_buffers[signal_name]
                buf["index_prices"].clear()
                for dq in buf["target_prices"].values():
                    dq.clear()
        self.history_loaded = False
        logger.info(f"[{self.config.name}] 🔄 Буферы сброшены")
        await self.preload_history()

    def get_status(self) -> dict[str, object]:
        return {
            "name": self.config.name,
            "signals_count": len(self.config.signals),
            "signals_generated": self.signals_generated,
            "trade_pairs": self.config.trade_pairs,
            "leverage": self.config.leverage,
            "history_loaded": self.history_loaded,
            "buffers_status": {
                sname: {
                    "index_buffer": len(buf["index_prices"]),
                    "target_buffers": {pair: len(dq) for pair, dq in buf["target_prices"].items()},
                }
                for sname, buf in self.signal_buffers.items()
            },
        }
