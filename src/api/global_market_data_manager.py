"""
Глобальный менеджер рыночных данных

Единый провайдер для всех стратегий, который:
1. Инициализируется один раз
2. Принимает регистрации от множественных стратегий
3. Оптимизирует подписки/polling для всех уникальных пар+frame
4. Транслирует данные в зарегистрированные стратегии
"""

import asyncio
import time
from typing import Dict, Set, List, Callable, Tuple
from dataclasses import dataclass

from ..logger import logger
from ..config import StrategyConfig
from .bybit_client import BybitClient
from .bybit_websocket_client import BybitWebSocketClient
from .common import Kline


@dataclass
class SubscriptionRequest:
    """Запрос на подписку от стратегии"""
    strategy_name: str
    symbol: str
    frame: str
    callback: Callable[[str, Kline], None]
    source_type: str  # "websocket" или "polling"


class GlobalMarketDataManager:
    """
    Глобальный менеджер рыночных данных для всех стратегий

    Централизованно управляет:
    - WebSocket подписками для минутных интервалов
    - REST polling для секундных интервалов
    - Оптимизацией дублирующихся запросов
    - Трансляцией данных в зарегистрированные стратегии
    """

    def __init__(
        self,
        rest_client: BybitClient,
        ws_client: BybitWebSocketClient,
        market_category: str = "linear",
    ):
        self.rest_client = rest_client
        self.ws_client = ws_client
        self.market_category = market_category

        # Активные подписки от стратегий: {(symbol, frame): [SubscriptionRequest, ...]}
        self.subscriptions: Dict[Tuple[str, str], List[SubscriptionRequest]] = {}

        # Активные WebSocket подписки {(symbol, frame)}
        self.active_ws_subscriptions: Set[Tuple[str, str]] = set()

        # Активные polling задачи (группированные по интервалу)
        self.polling_tasks: Dict[str, asyncio.Task] = {}
        self.polling_active = False
        self.last_poll_times: Dict[str, float] = {}

        # Зарегистрированные стратегии
        self.registered_strategies: Set[str] = set()

        self.is_running = False

        logger.info("🌍 GlobalMarketDataManager инициализирован")

    def register_strategy(
        self, strategy_config: StrategyConfig, kline_callback: Callable[[str, Kline], None]
    ) -> None:
        """Регистрация стратегии и её потребностей в данных."""

        strategy_name = strategy_config.name

        if strategy_name in self.registered_strategies:
            logger.warning(f"[{strategy_name}] Стратегия уже зарегистрирована")
            return

        logger.info(f"[{strategy_name}] 📝 Регистрация стратегии...")

        subscription_count = 0

        # Анализируем все signals стратегии
        for _, signal_config in strategy_config.signals.items():
            # Определяем источник данных по frame
            source_type = "polling" if signal_config.frame.endswith("s") else "websocket"

            # Регистрируем index пару
            self._add_subscription(
                strategy_name=strategy_name,
                symbol=signal_config.index,
                frame=signal_config.frame,
                callback=kline_callback,
                source_type=source_type,
            )
            subscription_count += 1

            # Регистрируем все target пары
            for trade_pair in strategy_config.trade_pairs:
                self._add_subscription(
                    strategy_name=strategy_name,
                    symbol=trade_pair,
                    frame=signal_config.frame,
                    callback=kline_callback,
                    source_type=source_type,
                )
                subscription_count += 1

        self.registered_strategies.add(strategy_name)

        logger.info(f"[{strategy_name}] ✅ Зарегистрировано {subscription_count} подписок")
        logger.info(f"   Signals: {len(strategy_config.signals)}")
        logger.info(f"   Trade pairs: {len(strategy_config.trade_pairs)}")

        # Если менеджер уже запущен, активируем новые подписки
        if self.is_running:
            asyncio.create_task(self._activate_new_subscriptions(strategy_name))

    def _add_subscription(
        self,
        strategy_name: str,
        symbol: str,
        frame: str,
        callback: Callable[[str, Kline], None],
        source_type: str,
    ) -> None:
        """Добавление подписки в реестр."""

        key = (symbol, frame)

        subscription = SubscriptionRequest(
            strategy_name=strategy_name,
            symbol=symbol,
            frame=frame,
            callback=callback,
            source_type=source_type,
        )

        if key not in self.subscriptions:
            self.subscriptions[key] = []

        # Проверяем дублирование по стратегии
        if not any(s.strategy_name == strategy_name for s in self.subscriptions[key]):
            self.subscriptions[key].append(subscription)
            logger.debug(f"   + {symbol} @ {frame} ({source_type}) -> [{strategy_name}]")

    async def start(self) -> None:
        """Запуск глобального менеджера данных."""

        if self.is_running:
            logger.warning("GlobalMarketDataManager уже запущен")
            return

        logger.info("🚀 Запуск GlobalMarketDataManager...")

        # Запускаем WebSocket подписки
        await self._start_websocket_subscriptions()

        # Запускаем polling задачи
        await self._start_polling_tasks()

        self.is_running = True

        total_keys = len(self.subscriptions)
        polling_count = sum(
            1 for subs in self.subscriptions.values() for s in subs if s.source_type == "polling"
        )
        websocket_count = sum(
            1 for subs in self.subscriptions.values() for s in subs if s.source_type == "websocket"
        )

        logger.info("")
        logger.info("🌍 ═══ GLOBAL MARKET DATA MANAGER ACTIVE ═══")
        logger.info(f"   Registered strategies: {len(self.registered_strategies)}")
        logger.info(f"   Keys (symbol@frame): {total_keys}")
        logger.info(f"   📡 Polling subs: {polling_count}")
        logger.info(f"   🔌 WebSocket subs: {websocket_count}")
        logger.info(f"   Active WS subs: {len(self.active_ws_subscriptions)}")
        logger.info(f"   Active polling tasks: {len(self.polling_tasks)}")
        logger.info("═" * 70)
        logger.info("")

    async def stop(self) -> None:
        """Остановка глобального менеджера."""

        logger.info("⏹ Остановка GlobalMarketDataManager...")

        self.is_running = False

        # Останавливаем все polling задачи
        await self._stop_polling_tasks()

        # Очистка
        self.subscriptions.clear()
        self.active_ws_subscriptions.clear()
        self.registered_strategies.clear()

        logger.info("✅ GlobalMarketDataManager остановлен")

    def unregister_strategy(self, strategy_name: str) -> None:
        """Отмена регистрации стратегии и очистка её подписок."""

        if strategy_name not in self.registered_strategies:
            return

        logger.info(f"[{strategy_name}] 📤 Отмена регистрации...")

        # Удаляем все подписки этой стратегии
        keys_to_remove: List[Tuple[str, str]] = []

        for key, subscription_list in list(self.subscriptions.items()):
            self.subscriptions[key] = [s for s in subscription_list if s.strategy_name != strategy_name]
            if not self.subscriptions[key]:
                keys_to_remove.append(key)

        # Удаляем пустые ключи и снимаем WS-подписки при необходимости
        for key in keys_to_remove:
            del self.subscriptions[key]
            symbol, frame = key
            if (symbol, frame) in self.active_ws_subscriptions:
                # TODO: реализовать отписку от WebSocket на клиенте
                self.active_ws_subscriptions.remove((symbol, frame))

        self.registered_strategies.remove(strategy_name)
        logger.info(f"[{strategy_name}] ✅ Отмена регистрации завершена")

    # ========== WebSocket Management ==========

    async def _start_websocket_subscriptions(self) -> None:
        """Запуск WebSocket подписок для всех минутных интервалов."""

        ws_subscriptions: Set[Tuple[str, str]] = set()

        for (symbol, frame), subscription_list in self.subscriptions.items():
            if any(s.source_type == "websocket" for s in subscription_list):
                ws_subscriptions.add((symbol, frame))

        if not ws_subscriptions:
            logger.info("Нет WebSocket подписок для активации")
            return

        logger.info(f"Запуск {len(ws_subscriptions)} WebSocket подписок...")

        for symbol, frame in ws_subscriptions:
            try:
                await self.ws_client.subscribe_kline(
                    category=self.market_category,
                    symbol=symbol,
                    interval=frame,
                    callback=self._ws_callback,
                )
                self.active_ws_subscriptions.add((symbol, frame))
                logger.debug(f"   ✓ WS: {symbol} @ {frame}")
            except Exception as e:
                logger.error(f"Ошибка WS подписки {symbol}@{frame}: {e}")

        logger.info(f"✅ WebSocket: {len(self.active_ws_subscriptions)} активных подписок")

    async def _ws_callback(self, symbol: str, kline: Kline) -> None:
        """Единый callback для всех WebSocket данных."""

        if not kline.confirm:
            return  # Игнорируем неподтвержденные данные

        # Находим все подписки для этой пары и транслируем данные
        for (sub_symbol, sub_frame), subscription_list in self.subscriptions.items():
            if sub_symbol != symbol:
                continue
            for subscription in subscription_list:
                if subscription.source_type == "websocket":
                    try:
                        await subscription.callback(symbol, kline)
                    except Exception as e:
                        logger.error(
                            f"Ошибка WS callback [{subscription.strategy_name}] {symbol}: {e}"
                        )

    # ========== Polling Management ==========

    async def _start_polling_tasks(self) -> None:
        """Запуск polling задач для секундных интервалов."""

        # Группируем polling подписки по интервалам
        polling_groups: Dict[str, List[SubscriptionRequest]] = {}

        for subscription_list in self.subscriptions.values():
            for subscription in subscription_list:
                if subscription.source_type == "polling":
                    polling_groups.setdefault(subscription.frame, []).append(subscription)

        if not polling_groups:
            logger.info("Нет polling задач для запуска")
            return

        logger.info(f"Запуск {len(polling_groups)} polling задач...")

        self.polling_active = True

        # Создаем задачу для каждого уникального интервала
        for frame, subscriptions in polling_groups.items():
            interval_seconds = self._frame_to_seconds(frame)
            task_name = f"polling_{frame}"
            task = asyncio.create_task(
                self._polling_loop(frame, subscriptions, interval_seconds),
                name=task_name,
            )
            self.polling_tasks[task_name] = task
            unique_symbols = {s.symbol for s in subscriptions}
            logger.info(
                f"   📡 {frame} ({interval_seconds}s): {len(unique_symbols)} пар, {len(subscriptions)} подписок"
            )

        logger.info(f"✅ Polling: {len(self.polling_tasks)} задач активно")

    async def _stop_polling_tasks(self) -> None:
        """Остановка всех polling задач."""

        self.polling_active = False

        for task_name, task in list(self.polling_tasks.items()):
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
        subscriptions: List[SubscriptionRequest],
        interval_seconds: int,
    ) -> None:
        """Основной цикл polling для конкретного интервала."""

        while self.polling_active:
            try:
                # Rate limiting
                now = time.time()
                last_poll = self.last_poll_times.get(frame, 0)
                if now - last_poll < interval_seconds:
                    await asyncio.sleep(max(0, interval_seconds - (now - last_poll)))

                # Получаем уникальные символы для этого интервала
                unique_symbols = {s.symbol for s in subscriptions}

                # Запрашиваем данные для всех символов
                for symbol in unique_symbols:
                    try:
                        ticker = await self.rest_client.get_ticker(
                            category=self.market_category,
                            symbol=symbol,
                        )
                        if ticker:
                            # Конвертируем в Kline и транслируем
                            kline = self._ticker_to_kline(ticker)
                            await self._distribute_polling_data(
                                symbol, frame, kline, subscriptions
                            )
                    except Exception as e:
                        logger.error(f"Ошибка polling {symbol} @ {frame}: {e}")

                self.last_poll_times[frame] = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в polling цикле {frame}: {e}")
                await asyncio.sleep(5)

    async def _distribute_polling_data(
        self,
        symbol: str,
        frame: str,
        kline: Kline,
        subscriptions: List[SubscriptionRequest],
    ) -> None:
        """Распределение polling данных по стратегиям."""

        for subscription in subscriptions:
            if subscription.symbol == symbol and subscription.frame == frame:
                try:
                    await subscription.callback(symbol, kline)
                except Exception as e:
                    logger.error(
                        f"Ошибка polling callback [{subscription.strategy_name}] {symbol}: {e}"
                    )

    async def _activate_new_subscriptions(self, strategy_name: str) -> None:
        """Активация подписок для новой стратегии (если менеджер уже запущен)."""

        logger.info(f"[{strategy_name}] 🔄 Активация подписок для новой стратегии...")
        await self._start_websocket_subscriptions()
        logger.info(f"[{strategy_name}] ✅ Подписки активированы")

    @staticmethod
    def _ticker_to_kline(ticker_data: dict) -> Kline:
        """Конвертация ticker в Kline объект."""

        if "result" in ticker_data and "list" in ticker_data["result"]:
            ticker = ticker_data["result"]["list"][0]
        else:
            ticker = ticker_data

        last_price = float(ticker.get("lastPrice", 0))
        high_price = float(ticker.get("highPrice24h", last_price))
        low_price = float(ticker.get("lowPrice24h", last_price))
        volume = float(ticker.get("volume24h", 0))

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
        """Конвертация frame в секунды."""
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
        """Статистика менеджера."""

        polling_subs = sum(
            len([s for s in subs if s.source_type == "polling"]) for subs in self.subscriptions.values()
        )
        websocket_subs = sum(
            len([s for s in subs if s.source_type == "websocket"]) for subs in self.subscriptions.values()
        )

        return {
            "registered_strategies": len(self.registered_strategies),
            "total_keys": len(self.subscriptions),
            "polling_subscriptions": polling_subs,
            "websocket_subscriptions": websocket_subs,
            "active_ws_subscriptions": len(self.active_ws_subscriptions),
            "active_polling_tasks": len(self.polling_tasks),
            "is_running": self.is_running,
        }
