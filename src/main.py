import asyncio
import signal
from datetime import datetime

from src.logger import logger, setup_logger
from src.config import Config
from src.api.bybit_client import BybitClient
from src.api.bybit_websocket_client import BybitWebSocketClient
from src.api.global_market_data_manager import GlobalMarketDataManager
from src.strategy.multi_signal_strategy import MultiSignalStrategy, SignalResult
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

        # Инициализируем клиентов
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

        # База, уведомления, трекинг
        self.database = Database(self.config.database_path)
        self.notifier = TelegramNotifier(self.config.telegram)
        self.order_tracker = OrderTracker(self.client)

        # Менеджер позиций
        self.position_manager = PositionManager(
            config=self.config,
            client=self.client,
            database=self.database,
            notifier=self.notifier,
            order_tracker=self.order_tracker
        )

        self.statistics = StatisticsMonitor(self.database)

        # Глобальный менеджер рыночных данных (единый для всех стратегий)
        market_category = (
            "linear" if any(s.leverage > 1 for s in self.config.strategies.values()) else "spot"
        )
        self.market_data_manager = GlobalMarketDataManager(
            rest_client=self.client,
            ws_client=self.ws_client,
            market_category=market_category,
        )

        # Создаем мультисигнальные стратегии и регистрируем их в глобальном менеджере
        self.strategies: dict[str, MultiSignalStrategy] = {}
        for strategy_name, strategy_config in self.config.enabled_strategies.items():
            strategy = MultiSignalStrategy(strategy_config, self.client, self.ws_client)
            # Регистрируем стратегию в глобальном менеджере с ее callback
            self.market_data_manager.register_strategy(
                strategy_config=strategy_config,
                kline_callback=strategy._on_kline_data  # стратегия принимает kline напрямую
            )
            self.strategies[strategy_name] = strategy

        self.running = False
        self.daily_report_sent = False

        logger.info("═" * 70)
        logger.info(" " * 25 + "CRYPTO TRADING BOT")
        logger.info("═" * 70)
        logger.info(f"Active strategies: {len(self.strategies)}")
        logger.info(f"Database: {self.config.database_path}")
        logger.info(f"Telegram: {'Enabled' if self.config.telegram.enabled else 'Disabled'}")
        logger.info(f"Testnet: {self.config.testnet}")
        logger.info("═" * 70)

    async def start(self):
        """Запуск бота"""
        self.running = True
        try:
            await self._initialize()
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
            await self.position_manager.initialize()
        except Exception as e:
            logger.error(f"Position manager init warning: {e}")

        # Предзагрузка истории стратегий и установка общих callback
        for strategy in self.strategies.values():
            await strategy.preload_history()
            strategy.set_strategy_callback(
                lambda sig_result: asyncio.create_task(self._handle_signal(sig_result))
            )

        # Запуск мониторинга ордеров
        await self.order_tracker.start_monitoring()

        # Восстановление открытых позиций из БД
        open_orders = self.database.get_open_orders()
        for order in open_orders:
            self.position_manager.open_positions[order.pair_name] = order
            self.order_tracker.track_order(order)
        if open_orders:
            logger.info(f"Restored {len(open_orders)} open positions from database")

        # Запуск глобального менеджера рыночных данных
        await self.market_data_manager.start()

        logger.info("✅ All components initialized")
        logger.info("")
        logger.info("🚀 Bot started successfully!")
        logger.info("═" * 70)
        logger.info("")

    async def _handle_signal(self, sig_result: SignalResult):
        """Обработка сигнала от мультисигнальной стратегии"""
        try:
            strategy_name = sig_result.strategy_name

            if self.position_manager.has_position(strategy_name):
                logger.debug(f"[{strategy_name}] Position already open, skipping signal")
                return

            if not sig_result.slippage_ok:
                logger.warning(f"[{strategy_name}] Signal rejected: slippage exceeded")
                return

            logger.info(f"[{strategy_name}:{sig_result.signal_name}] Processing signal: {sig_result.action}")

            success = await self.position_manager.execute_multi_signal(sig_result)

            if success:
                # Сброс буферов после успешного открытия
                strategy = self.strategies[strategy_name]
                await strategy.reset_buffers()
                logger.info(f"[{strategy_name}:{sig_result.signal_name}] ✅ Signal processed successfully")
            else:
                logger.warning(f"[{strategy_name}:{sig_result.signal_name}] Failed to execute signal")

        except Exception as e:
            logger.error(f"[{sig_result.strategy_name}] Error handling signal: {e}", exc_info=True)
            await self.notifier.notify_error(
                f"Error handling signal for {sig_result.strategy_name}: {str(e)}"
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
                        f"⛔ TRADING HALTED: {self.position_manager.stop_loss_streak} consecutive stop-losses"
                    )
                    await asyncio.sleep(300)
                    continue

                # Проверяем статус открытых позиций
                await self.position_manager.check_positions()

                # Логируем статус каждую минуту
                if cycle % 60 == 0:
                    self._log_status(cycle)

                # Отправляем дневной отчет в 00:00
                await self._check_daily_report()

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

        # Останавливаем глобальный менеджер данных
        try:
            await self.market_data_manager.stop()
        except Exception as e:
            logger.error(f"Error stopping GlobalMarketDataManager: {e}")

        # Финальная статистика
        logger.info("")
        logger.info("📈 FINAL STATISTICS:")
        logger.info("")

        pm_stats = self.position_manager.get_stats()
        for key, value in pm_stats.items():
            logger.info(f"  {key}: {value}")

        logger.info("")
        logger.info("Strategies:")
        for name, strategy in self.strategies.items():
            status = strategy.get_status()
            logger.info(f"  [{name}] Signals: {status['signals_generated']}")

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
                f"    [{name}] Signals: {status['signals_count']}, Generated: {status['signals_generated']}"
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
        if now.hour == 0 and now.minute < 10:
            if self.daily_report_sent:
                self.daily_report_sent = False
        if now.hour == 0 and now.minute < 10 and not self.daily_report_sent:
            logger.info("Generating daily report...")
            stats = self.statistics.get_today_stats()
            await self.notifier.notify_daily_report(stats)
            self.daily_report_sent = True
            logger.info("Daily report sent")


def main():
    """Точка входа"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = TradingBot("config/config.json")

    def signal_handler(signum, frame):
        logger.info(f"\nReceived signal {signum}")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(bot.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
