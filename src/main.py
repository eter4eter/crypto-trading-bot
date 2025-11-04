import asyncio
import signal
from datetime import datetime

from src.logger import logger, setup_logger
from src.config import Config, PairConfig
from src.api.bybit_client import BybitClient
from src.api.bybit_websocket_client import BybitWebSocketClient
from src.strategy.correlation_strategy import CorrelationStrategy, Signal
from src.trading.position_manager import PositionManager
from src.trading.order_tracker import OrderTracker
from src.storage.database import Database
from src.notifications.telegram_notifier import TelegramNotifier
from src.monitoring.statistics import StatisticsMonitor


class TradingBot:
    def __init__(self, config_path: str = "config/config.json"):
        # Загружаем конфигурацию
        self.config = Config.load(config_path)
        setup_logger(level=self.config.logging_level)

        # Инициализируем компоненты
        self.client = BybitClient(
            api_key=self.config.api_key,
            api_secret=self.config.api_secret,
            testnet=self.config.testnet,
            demo=self.config.demo_mode,
        )

        self.ws_client = BybitWebSocketClient(
            api_key=self.config.api_key,
            api_secret=self.config.api_secret,
            testnet=self.config.testnet,
            demo=self.config.demo_mode,
        )

        self.database = Database(self.config.database_path)
        self.notifier = TelegramNotifier(self.config.telegram)
        self.order_tracker = OrderTracker(self.client)

        self.position_manager = PositionManager(
            config=self.config,
            client=self.client,
            database=self.database,
            notifier=self.notifier,
            order_tracker=self.order_tracker
        )

        self.statistics = StatisticsMonitor(self.database)

        # Создаем стратегии для каждой пары
        self.strategies = {}
        for pair in self.config.pairs:
            if pair.enabled:
                self.strategies[pair.name] = CorrelationStrategy(pair, self.client, self.ws_client)

        self.running = False
        self.daily_report_sent = False

        logger.info("═" * 70)
        logger.info(" " * 25 + "CRYPTO TRADING BOT")
        logger.info("═" * 70)
        logger.info(f"Active pairs: {len(self.strategies)}")
        logger.info(f"Database: {self.config.database_path}")
        logger.info(f"Telegram: {'Enabled' if self.config.telegram.enabled else 'Disabled'}")
        logger.info(f"Testnet: {self.config.testnet}")
        logger.info("═" * 70)

    async def start(self):
        """Запуск бота"""

        self.running = True

        try:
            # Инициализируем компоненты
            await self._initialize()

            # Запускаем основной цикл
            await self._main_loop()

        except KeyboardInterrupt:
            logger.info("\n⏹ Stopping bot (KeyboardInterrupt)...")
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}", exc_info=True)
            await self.notifier.notify_error(f"Fatal error: {str(e)}")
        finally:
            await self.stop()

    async def _initialize(self):
        """Инициализация всех компонентов"""

        logger.info("Initializing components...")

        try:
            await self.ws_client.connect()
        except Exception as e:
            logger.error(f"WebSocket connect warning: {e}")

        try:
            # Устанавливаем плечи
            await self.position_manager.initialize()
        except Exception as e:
            logger.error(f"Position manager init warning: {e}")

        for pair in self.config.pairs:
            if pair.enabled:
                strategy = self.strategies[pair.name]

                # Предзагрузка истории
                await strategy.preload_history()

                strategy.set_signal_callback(
                    lambda sig, p=pair: asyncio.create_task(
                        self._handle_signal(p, sig),
                    )
                )

                # Запускаем WebSocket подписки
                await strategy.start()

        # Запускаем трекер ордеров
        await self.order_tracker.start_monitoring()

        # Загружаем незакрытые позиции из БД
        open_orders = self.database.get_open_orders()
        for order in open_orders:
            self.position_manager.open_positions[order.pair_name] = order
            self.order_tracker.track_order(order)

        if open_orders:
            logger.info(f"Restored {len(open_orders)} open positions from database")

        logger.info("✅ All components initialized")
        logger.info("")
        logger.info("🚀 Bot started successfully!")
        logger.info("═" * 70)
        logger.info("")

    async def _handle_signal(self, pair: PairConfig, sig: Signal):
        """
        Вызывается через callback когда стратегия генерирует сигнал
        """
        try:
            # Проверяем что нет открытой позиции
            if self.position_manager.has_position(pair.name):
                logger.debug(f"[{pair.name}] Position already open, skipping signal")
                return

            # Проверяем проскальзывание
            if not sig.slippage_ok:
                logger.warning(f"[{pair.name}] Signal rejected: slippage exceeded")
                return

            logger.info(f"[{pair.name}] Processing signal: {sig.action}")

            # await self.notifier.notify_signal(
            #     pair_name=pair.name,
            #     action=signal.action,
            #
            # )

            success = await self.position_manager.execute_signal(pair, sig)

            if success:
                # Сбрасываем буферы после успешного открытия
                strategy = self.strategies[pair.name]
                await strategy.reset_buffers()
                logger.info(f"[{pair.name}] ✅ Signal processed successfully")

        except Exception as e:
            logger.error(f"[{pair.name}] Error handling signal: {e}", exc_info=True)
            await self.notifier.notify_error(
                f"Error handling signal for {pair.name}: {str(e)}"
            )

    async def _main_loop(self):
        """Главный цикл бота"""

        cycle = 0

        while self.running:
            cycle += 1

            try:
                # Проверяем лимит stop-loss
                if self.position_manager.stop_loss_streak >= self.config.max_stop_loss_streak:
                    logger.error(
                        f"⛔ TRADING HALTED: {self.position_manager.stop_loss_streak} "
                        f"consecutive stop-losses"
                    )
                    await asyncio.sleep(300)  # Пауза 5 минут
                    continue

                # Проверяем статус открытых позиций
                await self.position_manager.check_positions()

                # Логируем статус каждую минуту
                if cycle % 60 == 0:
                    self._log_status(cycle)

                # 6. Отправляем дневной отчет в 00:00
                await self._check_daily_report()

                # Пауза
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def stop(self):
        """Остановка бота"""

        self.running = False

        logger.info("")
        logger.info("═" * 70)
        logger.info("STOPPING BOT")
        logger.info("═" * 70)

        # Останавливаем трекер
        await self.order_tracker.stop_monitoring()

        # Финальная статистика
        logger.info("")
        logger.info("📊 FINAL STATISTICS:")
        logger.info("")

        pm_stats = self.position_manager.get_stats()
        for key, value in pm_stats.items():
            logger.info(f"  {key}: {value}")

        logger.info("")
        logger.info("Strategies:")
        for name, strategy in self.strategies.items():
            status = strategy.get_status()
            logger.info(f"  [{name}] Signals: {status['signals']}")

        logger.info("")

        # Полная статистика
        report = self.statistics.get_comprehensive_report()
        logger.info(self.statistics.format_report(report))

        # Закрываем клиент
        await self.client.close()

        logger.info("═" * 70)
        logger.info("✅ Bot stopped successfully")
        logger.info("═" * 70)

    def _log_status(self, cycle: int):
        """Логирование текущего статуса"""

        logger.info("")
        logger.info(f"📍 ═══ Cycle {cycle} Status ═══")
        logger.info(f"  Open positions: {len(self.position_manager.open_positions)}")
        logger.info(f"  Total trades: {self.position_manager.total_trades}")
        logger.info(f"  Win rate: {self.position_manager.get_win_rate():.1f}%")
        logger.info(f"  SL streak: {self.position_manager.stop_loss_streak}")

        logger.info("")
        logger.info("  Strategies:")
        for name, strategy in self.strategies.items():
            status = strategy.get_status()
            logger.info(
                f"    [{name}] Buffer: {status.get('buffer', 'N/A')}, "
                f"Signals: {status.get('signals', 0)}"
            )

        # API статистика
        client_stats = self.client.get_stats()
        logger.info("")
        logger.info("  API Stats:")
        logger.info(f"    Requests: {client_stats['request_count']}")
        logger.info(f"    Errors: {client_stats['error_count']} ({client_stats['error_rate']})")

        ws_stats = self.ws_client.get_stats()
        logger.info("")
        logger.info("  WebSocket Stats:")
        logger.info(f"    Connected: {ws_stats['connected']}")
        logger.info(f"    Messages: {ws_stats['messages_received']}")
        logger.info(f"    Subscriptions: {ws_stats['active_subscriptions']}")
        logger.info("")

    async def _check_daily_report(self):
        """Проверка и отправка дневного отчета"""

        now = datetime.now()

        # Сбрасываем флаг в начале нового дня
        if now.hour == 0 and now.minute < 10:
            if self.daily_report_sent:
                self.daily_report_sent = False

        # Отправляем отчет в 00:00
        if now.hour == 0 and now.minute < 10 and not self.daily_report_sent:
            logger.info("Generating daily report...")

            stats = self.statistics.get_today_stats()
            await self.notifier.notify_daily_report(stats)

            self.daily_report_sent = True
            logger.info("Daily report sent")


def main():
    """Entry point"""

    # Создаем event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Создаем бота
    bot = TradingBot("../config/config.json")

    # Signal handlers для graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"\nReceived signal {signum}")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Запускаем
    try:
        loop.run_until_complete(bot.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
