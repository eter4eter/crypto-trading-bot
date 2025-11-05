"""
Глобальный менеджер рыночных данных с поддержкой per-symbol market_category
"""

import asyncio
import time
from typing import Callable
from dataclasses import dataclass

from ..logger import get_app_logger
from ..config import StrategyConfig
from .bybit_client import BybitClient
from .bybit_websocket_client import BybitWebSocketClient
from .common import Kline

logger = get_app_logger()


@dataclass
class SubscriptionRequest:
    strategy_name: str
    symbol: str
    frame: str
    market_category: str  # "spot" | "linear"
    callback: Callable[[str, Kline], None]
    source_type: str  # "websocket" | "polling"


class GlobalMarketDataManager:
    def __init__(
        self,
        rest_client: BybitClient,
        ws_client: BybitWebSocketClient,
    ):
        self.rest_client = rest_client
        self.ws_client = ws_client

        # Подписки: {(symbol, frame, category): [SubscriptionRequest, ...]}
        self.subscriptions: dict[tuple[str, str, str], list[SubscriptionRequest]] = {}

        # Активные WS подписки {(symbol, frame, category)}
        self.active_ws_subscriptions: set[tuple[str, str, str]] = set()

        # Polling задачи по ключу (frame, category)
        self.polling_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.polling_active = False
        self.last_poll_times: dict[tuple[str, str], float] = {}

        self.registered_strategies: set[str] = set()
        self.is_running = False

        logger.info("🌍 GlobalMarketDataManager инициализирован (per-symbol category)")

    # ===== helpers =====
    def _get_symbol_category(self, strategy_config: StrategyConfig, symbol: str) -> str:
        if hasattr(strategy_config, "get_pair_category"):
            try:
                cat = strategy_config.get_pair_category(symbol)
                if cat in ("spot", "linear"):
                    return cat
            except Exception:
                pass
        if hasattr(strategy_config, "get_market_category"):
            cat = strategy_config.get_market_category()
            return cat if cat in ("spot", "linear") else "linear"
        return "linear"

    def register_strategy(
        self, strategy_config: StrategyConfig, kline_callback: Callable[[str, Kline], None]
    ) -> None:
        name = strategy_config.name
        if name in self.registered_strategies:
            logger.warning(f"[{name}] Стратегия уже зарегистрирована")
            return

        logger.info(f"[{name}] 📝 Регистрация стратегии (per-symbol category)...")
        count = 0
        for _, sig in strategy_config.signals.items():
            source = "polling" if sig.frame.endswith("s") else "websocket"

            # index
            idx_cat = self._get_symbol_category(strategy_config, sig.index)
            self._add_subscription(name, sig.index, sig.frame, idx_cat, kline_callback, source)
            count += 1

            # targets
            for pair in strategy_config.trade_pairs:
                cat = self._get_symbol_category(strategy_config, pair)
                self._add_subscription(name, pair, sig.frame, cat, kline_callback, source)
                count += 1

        self.registered_strategies.add(name)
        logger.info(f"[{name}] ✅ Зарегистрировано {count} подписок (per-symbol category)")
        if self.is_running:
            asyncio.create_task(self._activate_new_subscriptions(name))

    def _add_subscription(
        self,
        strategy_name: str,
        symbol: str,
        frame: str,
        market_category: str,
        callback: Callable[[str, Kline], None],
        source_type: str,
    ) -> None:
        key = (symbol, frame, market_category)
        sub = SubscriptionRequest(strategy_name, symbol, frame, market_category, callback, source_type)
        self.subscriptions.setdefault(key, [])
        if not any(s.strategy_name == strategy_name for s in self.subscriptions[key]):
            self.subscriptions[key].append(sub)
            logger.debug(f"   + {symbol} @ {frame} [{market_category}] ({source_type}) -> [{strategy_name}]")

    async def start(self) -> None:
        if self.is_running:
            logger.warning("GlobalMarketDataManager уже запущен")
            return
        logger.info("🚀 Запуск GlobalMarketDataManager (per-symbol category)...")
        await self._start_websocket_subscriptions()
        await self._start_polling_tasks()
        self.is_running = True
        total = len(self.subscriptions)
        polling = sum(1 for subs in self.subscriptions.values() for s in subs if s.source_type == "polling")
        ws = sum(1 for subs in self.subscriptions.values() for s in subs if s.source_type == "websocket")
        logger.info("")
        logger.info("🌍 ═══ GLOBAL MARKET DATA MANAGER ACTIVE ═══")
        logger.info(f"   Keys (symbol@frame@category): {total}")
        logger.info(f"   📡 Polling subs: {polling}")
        logger.info(f"   🔌 WebSocket subs: {ws}")
        logger.info(f"   Active WS subs: {len(self.active_ws_subscriptions)}")
        logger.info(f"   Active polling tasks: {len(self.polling_tasks)}")
        logger.info("═" * 70)
        logger.info("")

    async def stop(self) -> None:
        logger.info("⏹ Остановка GlobalMarketDataManager...")
        self.is_running = False
        await self._stop_polling_tasks()
        self.subscriptions.clear()
        self.active_ws_subscriptions.clear()
        self.registered_strategies.clear()
        logger.info("✅ GlobalMarketDataManager остановлен")

    def unregister_strategy(self, strategy_name: str) -> None:
        if strategy_name not in self.registered_strategies:
            return
        logger.info(f"[{strategy_name}] 📤 Отмена регистрации...")
        to_delete: list[tuple[str, str, str]] = []
        for key, subs in list(self.subscriptions.items()):
            self.subscriptions[key] = [s for s in subs if s.strategy_name != strategy_name]
            if not self.subscriptions[key]:
                to_delete.append(key)
        for key in to_delete:
            del self.subscriptions[key]
            if key in self.active_ws_subscriptions:
                # TODO: отписка от WS на клиенте
                self.active_ws_subscriptions.remove(key)
        self.registered_strategies.remove(strategy_name)
        logger.info(f"[{strategy_name}] ✅ Отмена регистрации завершена")

    # ===== WebSocket =====
    async def _start_websocket_subscriptions(self) -> None:
        ws_keys: set[tuple[str, str, str]] = set()
        for (symbol, frame, category), subs in self.subscriptions.items():
            if any(s.source_type == "websocket" for s in subs):
                ws_keys.add((symbol, frame, category))
        if not ws_keys:
            logger.info("Нет WebSocket подписок для активации")
            return
        logger.info(f"Запуск {len(ws_keys)} WebSocket подписок...")
        for symbol, frame, category in ws_keys:
            try:
                self.ws_client.subscribe_kline(
                    category=category,
                    symbol=symbol,
                    interval=frame,
                    callback=self._ws_callback,
                )
                self.active_ws_subscriptions.add((symbol, frame, category))
                logger.debug(f"   ✓ WS: {symbol} @ {frame} [{category}]")
            except Exception as e:
                logger.error(f"Ошибка WS подписки {symbol}@{frame}[{category}]: {e}")
        logger.info(f"✅ WebSocket: {len(self.active_ws_subscriptions)} активных подписок")

    async def _ws_callback(self, symbol: str, kline: Kline) -> None:
        if not kline.confirm:
            return
        # Транслируем во все подписки по этому symbol (frame у WS evt не приходит — отправим всем подходящим)
        for (sub_symbol, sub_frame, sub_cat), subs in self.subscriptions.items():
            if sub_symbol != symbol:
                continue
            for s in subs:
                if s.source_type == "websocket":
                    try:
                        await s.callback(symbol, kline)
                    except Exception as e:
                        logger.error(f"Ошибка WS callback [{s.strategy_name}] {symbol}@{sub_frame}[{sub_cat}]: {e}")

    # ===== Polling =====
    async def _start_polling_tasks(self) -> None:
        groups: dict[tuple[str, str], list[SubscriptionRequest]] = {}
        for subs in self.subscriptions.values():
            for s in subs:
                if s.source_type == "polling":
                    key = (s.frame, s.market_category)
                    groups.setdefault(key, []).append(s)
        if not groups:
            logger.info("Нет polling задач для запуска")
            return
        logger.info(f"Запуск {len(groups)} polling задач...")
        self.polling_active = True
        for (frame, category), subs in groups.items():
            interval = self._frame_to_seconds(frame)
            task_key = (frame, category)
            task = asyncio.create_task(
                self._polling_loop(frame, category, subs, interval),
                name=f"polling_{frame}_{category}",
            )
            self.polling_tasks[task_key] = task
            uniq = {s.symbol for s in subs}
            logger.info(f"   📡 {frame} [{category}] ({interval}s): {len(uniq)} пар, {len(subs)} подписок")
        logger.info(f"✅ Polling: {len(self.polling_tasks)} задач активно")

    async def _stop_polling_tasks(self) -> None:
        self.polling_active = False
        for _, task in list(self.polling_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.polling_tasks.clear()
        self.last_poll_times.clear()
        logger.info("✅ Все polling задачи остановлены")

    async def _polling_loop(
        self,
        frame: str,
        category: str,
        subscriptions: list[SubscriptionRequest],
        interval_seconds: int,
    ) -> None:
        key = (frame, category)
        while self.polling_active:
            try:
                now = time.time()
                last = self.last_poll_times.get(key, 0)
                if now - last < interval_seconds:
                    await asyncio.sleep(max(0, interval_seconds - (now - last)))
                uniq = {s.symbol for s in subscriptions}
                for symbol in uniq:
                    try:
                        ticker = await self.rest_client.get_ticker(
                            category=category,
                            symbol=symbol,
                        )
                        if ticker:
                            kline = self._ticker_to_kline(ticker)
                            await self._distribute_polling_data(symbol, frame, category, kline, subscriptions)
                    except Exception as e:
                        logger.error(f"Ошибка polling {symbol} @ {frame}[{category}]: {e}")
                self.last_poll_times[key] = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в polling цикле {frame}[{category}]: {e}")
                await asyncio.sleep(5)

    async def _distribute_polling_data(
        self,
        symbol: str,
        frame: str,
        category: str,
        kline: Kline,
        subscriptions: list[SubscriptionRequest],
    ) -> None:
        for s in subscriptions:
            if s.symbol == symbol and s.frame == frame and s.market_category == category:
                try:
                    await s.callback(symbol, kline)
                except Exception as e:
                    logger.error(f"Ошибка polling callback [{s.strategy_name}] {symbol}@{frame}[{category}]: {e}")

    async def _activate_new_subscriptions(self, strategy_name: str) -> None:
        logger.info(f"[{strategy_name}] 🔄 Активация подписок...")
        await self._start_websocket_subscriptions()
        logger.info(f"[{strategy_name}] ✅ Подписки активированы")

    @staticmethod
    def _ticker_to_kline(ticker_data: dict) -> Kline:
        if "result" in ticker_data and "list" in ticker_data["result"]:
            t = ticker_data["result"]["list"][0]
        else:
            t = ticker_data
        last_price = float(t.get("lastPrice", 0))
        high_price = float(t.get("highPrice24h", last_price))
        low_price = float(t.get("lowPrice24h", last_price))
        volume = float(t.get("volume24h", 0))
        return Kline(
            timestamp=int(time.time() * 1000),
            open=last_price,
            high=high_price,
            low=low_price,
            close=last_price,
            volume=volume,
            confirm=True,
        )

    @staticmethod
    def _frame_to_seconds(frame: str) -> int:
        if frame.endswith("s"):
            return int(frame[:-1])
        if frame == "D":
            return 86400
        if frame == "W":
            return 604800
        if frame == "M":
            return 2592000
        return int(frame) * 60

    def get_stats(self) -> dict:
        polling = sum(len([s for s in subs if s.source_type == "polling"]) for subs in self.subscriptions.values())
        websocket = sum(len([s for s in subs if s.source_type == "websocket"]) for subs in self.subscriptions.values())
        by_cat: dict[str, int] = {}
        for (_sym, _frame, cat), subs in self.subscriptions.items():
            by_cat[cat] = by_cat.get(cat, 0) + len(subs)
        return {
            "registered_strategies": len(self.registered_strategies),
            "total_keys": len(self.subscriptions),
            "subs_by_category": by_cat,
            "polling_subscriptions": polling,
            "websocket_subscriptions": websocket,
            "active_ws_subscriptions": len(self.active_ws_subscriptions),
            "active_polling_tasks": len(self.polling_tasks),
            "is_running": self.is_running,
        }
