from datetime import datetime

from ..logger import logger
from ..config import Config, PairConfig, StrategyConfig
from ..api.bybit_client import BybitClient
from ..strategy.correlation_strategy import Signal
from ..strategy.multi_signal_strategy import SignalResult
from ..storage.database import Database
from ..storage.models import OrderRecord, SignalRecord
from ..notifications.telegram_notifier import TelegramNotifier
from ..trading.order_tracker import OrderTracker


class PositionManager:
    def __init__(
            self,
            config: Config,
            client: BybitClient,
            database: Database,
            notifier: TelegramNotifier,
            order_tracker: OrderTracker
    ):
        self.config = config
        self.client = client
        self.database = database
        self.notifier = notifier
        self.order_tracker = order_tracker

        # Текущие открытые позиции {pair_name: OrderRecord}
        self.open_positions: dict[str, OrderRecord] = {}

        # Баланс депозита (кеш)
        self.wallet_balance: float = 0.0

        # Статистика
        self.total_trades = 0
        self.profitable_trades = 0
        self.stop_loss_streak = 0
        self.max_stop_loss_streak = 0

        logger.info("PositionManager initialized")

    async def initialize(self):
        """Инициализация: установка плечей"""
        logger.info("Setting leverage for all enabled strategies...")

        # Получаем баланс кошелька
        await self._update_wallet_balance()

        # Устанавливаем плечи для всех торговых пар из strategies
        processed_pairs = set()
        
        for strategy_config in self.config.enabled_strategies.values():
            if not strategy_config.enabled:
                continue
                
            logger.info(f"[{strategy_config.name}] Initializing strategy...")
            
            for trade_pair in strategy_config.trade_pairs:
                if trade_pair in processed_pairs:
                    continue  # Уже обработали эту пару
                    
                processed_pairs.add(trade_pair)
                
                if strategy_config.is_futures():
                    logger.info(f"  Setting {strategy_config.leverage}x leverage for {trade_pair}")

                    try:
                        success = await self.client.set_leverage(
                            category="linear",
                            symbol=trade_pair,
                            leverage=strategy_config.leverage
                        )

                        if success:
                            logger.info(f"✓ [{strategy_config.name}] {trade_pair} leverage: {strategy_config.leverage}x")
                        else:
                            logger.warning(f"✗ [{strategy_config.name}] Failed to set leverage for {trade_pair}")

                    except Exception as e:
                        logger.warning(f"  ⚠️ Leverage error for {trade_pair} (continuing): {e}")
                else:
                    logger.info(f"  [{strategy_config.name}] {trade_pair} - spot trading (no leverage)")

        # Поддержка старого формата pairs (обратная совместимость)
        for pair in self.config.pairs:
            if not pair.enabled:
                continue
                
            if pair.target_pair in processed_pairs:
                continue
                
            processed_pairs.add(pair.target_pair)
            logger.info(f"[{pair.name}] Initializing legacy pair...")

            if pair.is_futures():
                logger.info(f"  Setting {pair.leverage}x leverage for {pair.target_pair}")

                try:
                    success = await self.client.set_leverage(
                        category="linear",
                        symbol=pair.target_pair,
                        leverage=pair.leverage
                    )

                    if success:
                        logger.info(f"✓ [{pair.name}] Leverage: {pair.leverage}x")
                    else:
                        logger.warning(f"✗ [{pair.name}] Failed to set leverage")

                except Exception as e:
                    logger.warning(f"  ⚠️ Leverage error (continuing): {e}")
            else:
                logger.info(f"  Spot trading (no leverage needed)")

    async def _update_wallet_balance(self):
        """Обновление баланса кошелька"""
        try:
            wallet_data = await self.client.get_wallet_balance()

            if wallet_data and wallet_data.get('list'):
                account = wallet_data['list'][0]
                self.wallet_balance = float(account.get('totalEquity', 0))

                logger.info(f"Wallet balance: ${self.wallet_balance:.2f} USDT")
            else:
                logger.warning("Failed to get wallet balance, using cached value")

        except Exception as e:
            logger.error(f"Error getting wallet balance: {e}")

    async def execute_multi_signal(self, sig_result: SignalResult) -> bool:
        """
        Исполнение мультисигнального торгового сигнала согласно ТЗ
        
        Args:
            sig_result: Результат сигнала от MultiSignalStrategy
            
        Returns:
            True если позиция открыта
        """
        
        # Сохраняем сигнал в БД
        signal_record = SignalRecord(
            pair_name=sig_result.strategy_name,
            action=sig_result.action,
            dominant_change=sig_result.index_change,
            target_change=sig_result.target_change,
            target_price=sig_result.target_price,
            executed=False
        )

        signal_id = self.database.save_signal(signal_record)
        logger.debug(f"Multi signal saved to DB: ID={signal_id}")

        # Проверяем лимит stop-loss
        if self.stop_loss_streak >= self.config.max_stop_loss_streak:
            logger.error(
                f"⛔ Stop-loss streak limit reached ({self.stop_loss_streak}). "
                f"Trading halted for safety."
            )
            await self.notifier.notify_error(
                f"Trading halted: {self.stop_loss_streak} consecutive stop-losses"
            )
            return False

        # Проверяем открытую позицию по имени стратегии
        if sig_result.strategy_name in self.open_positions:
            logger.warning(f"[{sig_result.strategy_name}] Position already open, skipping signal")
            return False

        # Открываем позицию для мультисигнала
        success = await self._open_multi_position(sig_result)

        if success:
            # Отмечаем сигнал как исполненный
            signal_record.executed = True

        return success
    
    async def _open_multi_position(self, sig_result: SignalResult) -> bool:
        """Открытие позиции на основе мультисигнала"""
        
        # Получаем конфигурацию стратегии
        strategy_config = self.config.strategies.get(sig_result.strategy_name)
        if not strategy_config:
            logger.error(f"Strategy config not found: {sig_result.strategy_name}")
            return False
            
        # Обновляем баланс
        await self._update_wallet_balance()
        
        if self.wallet_balance <= 0:
            logger.error(f"[{sig_result.strategy_name}] Invalid wallet balance: ${self.wallet_balance}")
            return False
        
        # Рассчитываем размер позиции в USDT
        position_size_usdt = strategy_config.position_size
        
        # Проверяем минимальный размер
        if position_size_usdt < 5.0:
            logger.warning(
                f"[{sig_result.strategy_name}] Position size too small: ${position_size_usdt:.2f}. "
                f"Minimum 5 USDT required."
            )
            return False
        
        # Выбираем первую торговую пару (можно расширить для множественных пар)
        target_pair = sig_result.target_pairs[0] if sig_result.target_pairs else strategy_config.trade_pairs[0]
        
        # Расчет количества
        quantity = position_size_usdt / sig_result.target_price
        qty_str = f"{quantity:.4f}"
        
        # Определяем сторону и цены TP/SL
        side = "Buy" if sig_result.action == "Buy" else "Sell"
        
        if sig_result.action == "Buy":
            # Long позиция
            take_profit = sig_result.target_price * (1 + strategy_config.stop_take_percent)
            stop_loss = sig_result.target_price * (1 - strategy_config.stop_take_percent)
        else:
            # Short позиция
            take_profit = sig_result.target_price * (1 - strategy_config.stop_take_percent)
            stop_loss = sig_result.target_price * (1 + strategy_config.stop_take_percent)
        
        logger.info(f"")
        logger.info(f"📊 ═══ Opening Multi Position [{sig_result.strategy_name}:{sig_result.signal_name}] ═══")
        logger.info(f"  Pair: {target_pair}")
        logger.info(f"  Side: {side}")
        logger.info(f"  Entry: ${sig_result.target_price:.8f}")
        logger.info(f"  Quantity: {qty_str}")
        logger.info(f"  Take-Profit: ${take_profit:.8f} (+{strategy_config.stop_take_percent*100:.2f}%)")
        logger.info(f"  Stop-Loss: ${stop_loss:.8f} (-{strategy_config.stop_take_percent*100:.2f}%)")
        logger.info(f"  Index change: {sig_result.index_change:+.3f}%")
        logger.info(f"  Target change: {sig_result.target_change:+.3f}%")
        
        # Размещаем ордер
        result = await self.client.place_market_order(
            category=strategy_config.get_market_category(),
            symbol=target_pair,
            side=side,
            qty=qty_str,
            take_profit=f"{take_profit:.8f}",
            stop_loss=f"{stop_loss:.8f}",
            position_idx=0
        )
        
        if not result:
            logger.error(f"✗ [{sig_result.strategy_name}] Failed to place order")
            return False
        
        # Создаем запись об ордере
        order = OrderRecord(
            pair_name=sig_result.strategy_name,  # Используем имя стратегии как pair_name
            symbol=target_pair,
            order_id=result.get("orderId", ""),
            side=side,
            quantity=quantity,
            entry_price=sig_result.target_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            status="OPEN",
            opened_at=datetime.now()
        )
        
        # Сохраняем в БД
        order.id = self.database.save_order(order)
        
        # Добавляем в открытые позиции
        self.open_positions[sig_result.strategy_name] = order
        
        # Добавляем в трекер для мониторинга
        self.order_tracker.track_order(order)
        
        # Обновляем статистику
        self.total_trades += 1
        
        logger.info(f"✅ [{sig_result.strategy_name}:{sig_result.signal_name}] Position opened successfully")
        logger.info(f"   Order ID: {order.order_id}")
        logger.info(f"   Total trades: {self.total_trades}")
        logger.info(f"")
        
        # Отправляем уведомление
        await self.notifier.notify_signal(
            pair_name=sig_result.strategy_name,
            side=side,
            entry_price=sig_result.target_price,
            quantity=quantity,
            take_profit=take_profit,
            stop_loss=stop_loss
        )
        
        return True

    async def execute_signal(self, pair: PairConfig, signal: Signal) -> bool:
        """
        Исполнение торгового сигнала (старый формат для обратной совместимости)

        1. Сохранение сигнала в БД
        2. Проверка наличия открытой позиции
        3. Проверка лимита stop-loss
        4. Открытие позиции
        5. Уведомления

        Returns:
            True если позиция открыта
        """

        # Сохраняем сигнал в БД
        signal_record = SignalRecord(
            pair_name=pair.name,
            action=signal.action,
            dominant_change=signal.dominant_change,
            target_change=signal.target_change,
            target_price=signal.target_price,
            executed=False
        )

        signal_id = self.database.save_signal(signal_record)
        logger.debug(f"Signal saved to DB: ID={signal_id}")

        # Проверяем лимит stop-loss
        if self.stop_loss_streak >= self.config.max_stop_loss_streak:
            logger.error(
                f"⛔ Stop-loss streak limit reached ({self.stop_loss_streak}). "
                f"Trading halted for safety."
            )
            await self.notifier.notify_error(
                f"Trading halted: {self.stop_loss_streak} consecutive stop-losses"
            )
            return False

        # Проверяем открытую позицию
        if pair.name in self.open_positions:
            logger.warning(f"[{pair.name}] Position already open, skipping signal")
            return False

        # Открываем позицию
        success = await self._open_position(pair, signal)

        if success:
            # Отмечаем сигнал как исполненный
            signal_record.executed = True

        return success

    async def _open_position(self, pair: PairConfig, signal: Signal) -> bool:
        """Открытие новой позиции (старый формат)"""

        # Обновляем баланс перед открытием позиции
        await self._update_wallet_balance()

        if self.wallet_balance <= 0:
            logger.error(f"[{pair.name}] Invalid wallet balance: ${self.wallet_balance}")
            return False

        # Рассчитываем размер позиции от процента депозита
        position_size_usdt = self.wallet_balance * (pair.position_size_percent / 100)

        # Проверяем минимальный размер
        if position_size_usdt < 5.0:  # Минимум 5 USDT
            logger.warning(
                f"[{pair.name}] Position size too small: ${position_size_usdt:.2f}. "
                f"Minimum 5 USDT required."
            )
            return False

        # Расчет размера позиции
        quantity = position_size_usdt / signal.target_price
        qty_str = f"{quantity:.4f}"

        # Определяем сторону и цены TP/SL
        side = "Buy" if signal.action == "Buy" else "Sell"

        if signal.action == "Buy":
            # Long позиция
            take_profit = signal.target_price * (1 + pair.take_profit_percent / 100)
            stop_loss = signal.target_price * (1 - pair.stop_loss_percent / 100)
        else:
            # Short позиция
            take_profit = signal.target_price * (1 - pair.take_profit_percent / 100)
            stop_loss = signal.target_price * (1 + pair.stop_loss_percent / 100)

        logger.info(f"")
        logger.info(f"📊 ═══ Opening Position [{pair.name}] ═══")
        logger.info(f"  Side: {side}")
        logger.info(f"  Entry: ${signal.target_price:.8f}")
        logger.info(f"  Quantity: {qty_str}")
        logger.info(f"  Take-Profit: ${take_profit:.8f} (+{pair.take_profit_percent}%)")
        logger.info(f"  Stop-Loss: ${stop_loss:.8f} (-{pair.stop_loss_percent}%)")

        # Размещаем ордер
        result = await self.client.place_market_order(
            category="linear",
            symbol=pair.target_pair,
            side=side,
            qty=qty_str,
            take_profit=f"{take_profit:.8f}",
            stop_loss=f"{stop_loss:.8f}",
            position_idx=0
        )

        if not result:
            logger.error(f"✗ [{pair.name}] Failed to place order")
            return False

        # Создаем запись об ордере
        order = OrderRecord(
            pair_name=pair.name,
            symbol=pair.target_pair,
            order_id=result.get("orderId", ""),
            side=side,
            quantity=quantity,
            entry_price=signal.target_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            status="OPEN",
            opened_at=datetime.now()
        )

        # Сохраняем в БД
        order.id = self.database.save_order(order)

        # Добавляем в открытые позиции
        self.open_positions[pair.name] = order

        # Добавляем в трекер для мониторинга
        self.order_tracker.track_order(order)

        # Обновляем статистику
        self.total_trades += 1

        logger.info(f"✅ [{pair.name}] Position opened successfully")
        logger.info(f"   Order ID: {order.order_id}")
        logger.info(f"   Total trades: {self.total_trades}")
        logger.info(f"")

        # Отправляем уведомление
        await self.notifier.notify_signal(
            pair_name=pair.name,
            side=side,
            entry_price=signal.target_price,
            quantity=quantity,
            take_profit=take_profit,
            stop_loss=stop_loss
        )

        return True

    async def check_positions(self):
        """Проверка статуса открытых позиций"""

        closed_pairs = []

        for pair_name, order in self.open_positions.items():
            # Проверяем позицию через API
            position = await self.client.get_position("linear", order.symbol)

            # Если позиции нет - значит закрылась
            if position is None or float(position.get('size', 0)) == 0:
                await self._handle_position_closed(order)
                closed_pairs.append(pair_name)

        # Удаляем закрытые позиции
        for pair_name in closed_pairs:
            del self.open_positions[pair_name]

    async def _handle_position_closed(self, order: OrderRecord):
        """Обработка закрытой позиции"""

        logger.info(f"")
        logger.info(f"📊 ═══ Position Closed [{order.pair_name}] ═══")

        # Получаем фактическую информацию о закрытии из истории
        history = await self.client.get_order_history(
            category="linear",
            symbol=order.symbol,
            limit=10
        )

        close_price = order.entry_price
        close_reason = "UNKNOWN"

        # Ищем наш ордер в истории
        for hist_order in history:
            if hist_order['orderId'] == order.order_id:
                close_price = float(hist_order.get('avgPrice', order.entry_price))

                # Определяем причину закрытия
                if close_price >= order.take_profit:
                    close_reason = "TP"
                elif close_price <= order.stop_loss:
                    close_reason = "SL"
                else:
                    close_reason = "MANUAL"

                break

        # Рассчитываем P&L
        if order.side == "Buy":
            pnl = (close_price - order.entry_price) * order.quantity
        else:
            pnl = (order.entry_price - close_price) * order.quantity

        pnl_percent = (pnl / (order.entry_price * order.quantity)) * 100

        # Обновляем запись
        order.status = "CLOSED"
        order.closed_at = datetime.now()
        order.close_price = close_price
        order.pnl = pnl
        order.pnl_percent = pnl_percent
        order.close_reason = close_reason

        # Сохраняем в БД
        self.database.update_order(
            order.id,
            status="CLOSED",
            closed_at=order.closed_at,
            close_price=close_price,
            pnl=pnl,
            pnl_percent=pnl_percent,
            close_reason=close_reason
        )

        # Обновляем статистику
        if pnl > 0:
            self.profitable_trades += 1
            self.stop_loss_streak = 0
            logger.info(f"  Result: ✅ PROFIT")
        else:
            if close_reason == "SL":
                self.stop_loss_streak += 1
                self.max_stop_loss_streak = max(
                    self.max_stop_loss_streak,
                    self.stop_loss_streak
                )
            logger.info(f"  Result: ❌ LOSS")

        duration = (order.closed_at - order.opened_at).seconds

        logger.info(f"  Entry: ${order.entry_price:.8f}")
        logger.info(f"  Close: ${close_price:.8f}")
        logger.info(f"  P&L: {pnl:+.2f} USDT ({pnl_percent:+.2f}%)")
        logger.info(f"  Reason: {close_reason}")
        logger.info(f"  Duration: {duration}s")
        logger.info(f"  Win Rate: {self.get_win_rate():.1f}%")
        logger.info(f"  SL Streak: {self.stop_loss_streak}")
        logger.info(f"")

        # Отправляем уведомление
        await self.notifier.notify_trade_closed(
            pair_name=order.pair_name,
            pnl=pnl,
            pnl_percent=pnl_percent,
            close_reason=close_reason,
            duration_seconds=duration
        )

        # Пересчитываем дневную статистику
        self.database.calculate_and_save_daily_stats()

    def has_position(self, pair_name: str) -> bool:
        """Проверка наличия открытой позиции"""
        return pair_name in self.open_positions

    def get_win_rate(self) -> float:
        """Расчет винрейта"""
        if self.total_trades == 0:
            return 0.0
        return (self.profitable_trades / self.total_trades) * 100

    def get_stats(self) -> dict:
        """Статистика менеджера"""
        return {
            "total_trades": self.total_trades,
            "profitable_trades": self.profitable_trades,
            "win_rate": f"{self.get_win_rate():.1f}%",
            "stop_loss_streak": self.stop_loss_streak,
            "max_stop_loss_streak": self.max_stop_loss_streak,
            "open_positions": len(self.open_positions)
        }
