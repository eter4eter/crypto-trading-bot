from __future__ import annotations

from datetime import datetime, timedelta

from ..api.bybit_client import BybitClient
from ..config import Config, PairConfig
from ..logger import logger
from ..notifications.telegram_notifier import TelegramNotifier
from ..storage.database import Database
from ..storage.models import OrderRecord, SignalRecord
from ..strategy.correlation_strategy import Signal
from ..strategy.multi_signal_strategy import SignalResult
from ..trading.order_tracker import OrderTracker


class PositionManager:
    def __init__(
        self,
        config: Config,
        client: BybitClient,
        database: Database,
        notifier: TelegramNotifier,
        order_tracker: OrderTracker,
    ):
        self.config = config
        self.client = client
        self.database = database
        self.notifier = notifier
        self.order_tracker = order_tracker

        # Текущие открытые позиции
        self.open_positions: dict[str, OrderRecord] = {}

        # Баланс депозита
        self.wallet_balance: float = 0.0

        # Статистика
        self.total_trades = 0
        self.profitable_trades = 0
        self.stop_loss_streak = 0
        self.max_stop_loss_streak = 0
        self.last_stop_loss_time: datetime | None = None

        logger.info("PositionManager initialized")

    async def initialize(self) -> None:
        logger.info("Setting leverage for all enabled strategies...")
        await self._update_wallet_balance()
        processed_pairs: set[str] = set()

        for strategy_config in self.config.enabled_strategies.values():
            if not strategy_config.enabled:
                continue
            logger.info(f"[{strategy_config.name}] Initializing strategy...")
            for trade_pair in strategy_config.trade_pairs:
                if trade_pair in processed_pairs:
                    continue
                processed_pairs.add(trade_pair)
                if strategy_config.is_futures():
                    logger.info(f"  Setting {strategy_config.leverage}x leverage for {trade_pair}")
                    try:
                        success = await self.client.set_leverage(
                            category="linear",
                            symbol=trade_pair,
                            leverage=strategy_config.leverage,
                        )
                        if success:
                            logger.info(f"✓ [{strategy_config.name}] {trade_pair} leverage: {strategy_config.leverage}x")
                        else:
                            logger.warning(f"✗ [{strategy_config.name}] Failed to set leverage for {trade_pair}")
                    except Exception as e:
                        logger.warning(f"  ⚠️ Leverage error for {trade_pair} (continuing): {e}")
                else:
                    logger.info(f"  [{strategy_config.name}] {trade_pair} - spot trading (no leverage)")

        # Поддержка старого формата pairs
        for pair in self.config.pairs:
            if not pair.enabled or pair.target_pair in processed_pairs:
                continue
            processed_pairs.add(pair.target_pair)
            logger.info(f"[{pair.name}] Initializing legacy pair...")
            if pair.is_futures():
                try:
                    success = await self.client.set_leverage(
                        category="linear",
                        symbol=pair.target_pair,
                        leverage=pair.leverage,
                    )
                    if success:
                        logger.info(f"✓ [{pair.name}] Leverage: {pair.leverage}x")
                    else:
                        logger.warning(f"✗ [{pair.name}] Failed to set leverage")
                except Exception as e:
                    logger.warning(f"  ⚠️ Leverage error (continuing): {e}")

    async def _update_wallet_balance(self) -> None:
        try:
            wallet_data = await self.client.get_wallet_balance()
            if wallet_data and wallet_data.get("list"):
                account = wallet_data["list"][0]
                self.wallet_balance = float(account.get("totalEquity", 0))
                logger.info(f"Wallet balance: ${self.wallet_balance:.2f} USDT")
            else:
                logger.warning("Failed to get wallet balance, using cached value")
        except Exception as e:
            logger.error(f"Error getting wallet balance: {e}")

    def _update_stop_loss_streak(self, increment: bool) -> None:
        """Обновление счетчика stop-loss streak с автосбросом через 24 часа."""
        if increment:
            self.stop_loss_streak += 1
            self.last_stop_loss_time = datetime.now()
            self.max_stop_loss_streak = max(self.max_stop_loss_streak, self.stop_loss_streak)
            logger.warning(
                f"Stop-loss streak increased to {self.stop_loss_streak} "
                f"(max: {self.max_stop_loss_streak})"
            )
        else:
            if self.stop_loss_streak > 0:
                logger.info(f"Stop-loss streak reset (was {self.stop_loss_streak})")
                self.stop_loss_streak = 0
                self.last_stop_loss_time = None

    def _check_auto_reset_stop_loss_streak(self) -> None:
        """Автоматический сброс счетчика stop-loss streak через 24 часа."""
        if (
            self.stop_loss_streak > 0
            and self.last_stop_loss_time is not None
            and datetime.now() - self.last_stop_loss_time > timedelta(hours=24)
        ):
            logger.info(
                f"🔄 Auto-reset stop-loss streak after 24h (was {self.stop_loss_streak})"
            )
            self.stop_loss_streak = 0
            self.last_stop_loss_time = None

    async def execute_multi_signal(self, sig_result: SignalResult) -> bool:
        self._check_auto_reset_stop_loss_streak()
        signal_record = SignalRecord(
            pair_name=sig_result.strategy_name,
            action=sig_result.action,
            dominant_change=sig_result.index_change,
            target_change=sig_result.target_change,
            target_price=sig_result.target_price,
            executed=False,
        )
        signal_id = self.database.save_signal(signal_record)
        if self.stop_loss_streak >= self.config.max_stop_loss_trades:
            logger.error(
                f"⛔ Stop-loss streak limit reached ({self.stop_loss_streak}). Trading halted for safety."
            )
            await self.notifier.notify_error(
                f"Trading halted: {self.stop_loss_streak} consecutive stop-losses"
            )
            return False
        if sig_result.strategy_name in self.open_positions:
            logger.warning(f"[{sig_result.strategy_name}] Position already open, skipping signal")
            return False
        success = await self._open_multi_position(sig_result)
        if success:
            signal_record.executed = True
        return success

    async def _open_multi_position(self, sig_result: SignalResult) -> bool:
        strategy_config = self.config.strategies.get(sig_result.strategy_name)
        if not strategy_config:
            logger.error(f"Strategy config not found: {sig_result.strategy_name}")
            return False
        await self._update_wallet_balance()
        if self.wallet_balance <= 0:
            logger.error(
                f"[{sig_result.strategy_name}] Invalid wallet balance: ${self.wallet_balance}"
            )
            return False
        position_size_usdt = strategy_config.position_size
        if position_size_usdt < 5.0:
            logger.warning(
                f"[{sig_result.strategy_name}] Position size too small: ${position_size_usdt:.2f}. "
                f"Minimum 5 USDT required."
            )
            return False
        target_pair = sig_result.target_pairs[0] if sig_result.target_pairs else strategy_config.trade_pairs[0]
        quantity = position_size_usdt / sig_result.target_price
        qty_str = f"{quantity:.4f}"
        side = "Buy" if sig_result.action == "Buy" else "Sell"
        if sig_result.action == "Buy":
            take_profit = sig_result.target_price * (1 + strategy_config.stop_take_percent)
            stop_loss = sig_result.target_price * (1 - strategy_config.stop_take_percent)
        else:
            take_profit = sig_result.target_price * (1 - strategy_config.stop_take_percent)
            stop_loss = sig_result.target_price * (1 + strategy_config.stop_take_percent)
        logger.info("")
        logger.info(f"📊 ═══ Opening Multi Position [{sig_result.strategy_name}:{sig_result.signal_name}] ═══")
        logger.info(f"  Pair: {target_pair}")
        logger.info(f"  Side: {side}")
        logger.info(f"  Entry: ${sig_result.target_price:.8f}")
        logger.info(f"  Quantity: {qty_str}")
        logger.info(f"  Take-Profit: ${take_profit:.8f} (+{strategy_config.stop_take_percent*100:.2f}%)")
        logger.info(f"  Stop-Loss: ${stop_loss:.8f} (-{strategy_config.stop_take_percent*100:.2f}%)")
        logger.info(f"  Index change: {sig_result.index_change:+.3f}%")
        logger.info(f"  Target change: {sig_result.target_change:+.3f}%")
        result = await self.client.place_market_order(
            category=strategy_config.get_market_category(),
            symbol=target_pair,
            side=side,
            qty=qty_str,
            take_profit=f"{take_profit:.8f}",
            stop_loss=f"{stop_loss:.8f}",
            position_idx=0,
        )
        if not result:
            logger.error(f"✗ [{sig_result.strategy_name}] Failed to place order")
            return False
        order = OrderRecord(
            pair_name=sig_result.strategy_name,
            symbol=target_pair,
            order_id=result.get("orderId", ""),
            side=side,
            quantity=quantity,
            entry_price=sig_result.target_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            status="OPEN",
            opened_at=datetime.now(),
        )
        order.id = self.database.save_order(order)
        self.open_positions[sig_result.strategy_name] = order
        self.order_tracker.track_order(order)
        self.total_trades += 1
        logger.info(f"✅ [{sig_result.strategy_name}:{sig_result.signal_name}] Position opened successfully")
        logger.info(f"   Order ID: {order.order_id}")
        logger.info(f"   Total trades: {self.total_trades}")
        logger.info("")
        await self.notifier.notify_signal(
            pair_name=sig_result.strategy_name,
            side=side,
            entry_price=sig_result.target_price,
            quantity=quantity,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
        return True

    async def execute_signal(self, pair: PairConfig, signal: Signal) -> bool:
        self._check_auto_reset_stop_loss_streak()
        signal_record = SignalRecord(
            pair_name=pair.name,
            action=signal.action,
            dominant_change=signal.dominant_change,
            target_change=signal.target_change,
            target_price=signal.target_price,
            executed=False,
        )
        signal_id = self.database.save_signal(signal_record)
        if self.stop_loss_streak >= self.config.max_stop_loss_trades:
            logger.error(
                f"⛔ Stop-loss streak limit reached ({self.stop_loss_streak}). Trading halted for safety."
            )
            await self.notifier.notify_error(
                f"Trading halted: {self.stop_loss_streak} consecutive stop-losses"
            )
            return False
        if pair.name in self.open_positions:
            logger.warning(f"[{pair.name}] Position already open, skipping signal")
            return False
        success = await self._open_position(pair, signal)
        if success:
            signal_record.executed = True
        return success

    async def _open_position(self, pair: PairConfig, signal: Signal) -> bool:
        await self._update_wallet_balance()
        if self.wallet_balance <= 0:
            logger.error(f"[{pair.name}] Invalid wallet balance: ${self.wallet_balance}")
            return False
        position_size_usdt = self.wallet_balance * (pair.position_size_percent / 100)
        if position_size_usdt < 5.0:
            logger.warning(
                f"[{pair.name}] Position size too small: ${position_size_usdt:.2f}. Minimum 5 USDT required."
            )
            return False
        quantity = position_size_usdt / signal.target_price
        qty_str = f"{quantity:.4f}"
        side = "Buy" if signal.action == "Buy" else "Sell"
        if signal.action == "Buy":
            take_profit = signal.target_price * (1 + pair.take_profit_percent / 100)
            stop_loss = signal.target_price * (1 - pair.stop_loss_percent / 100)
        else:
            take_profit = signal.target_price * (1 - pair.take_profit_percent / 100)
            stop_loss = signal.target_price * (1 + pair.stop_loss_percent / 100)
        logger.info("")
        logger.info(f"📊 ═══ Opening Position [{pair.name}] ═══")
        logger.info(f"  Side: {side}")
        logger.info(f"  Entry: ${signal.target_price:.8f}")
        logger.info(f"  Quantity: {qty_str}")
        logger.info(f"  Take-Profit: ${take_profit:.8f} (+{pair.take_profit_percent}%)")
        logger.info(f"  Stop-Loss: ${stop_loss:.8f} (-{pair.stop_loss_percent}%)")
        result = await self.client.place_market_order(
            category="linear",
            symbol=pair.target_pair,
            side=side,
            qty=qty_str,
            take_profit=f"{take_profit:.8f}",
            stop_loss=f"{stop_loss:.8f}",
            position_idx=0,
        )
        if not result:
            logger.error(f"✗ [{pair.name}] Failed to place order")
            return False
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
            opened_at=datetime.now(),
        )
        order.id = self.database.save_order(order)
        self.open_positions[pair.name] = order
        self.order_tracker.track_order(order)
        self.total_trades += 1
        logger.info(f"✅ [{pair.name}] Position opened successfully")
        logger.info(f"   Order ID: {order.order_id}")
        logger.info(f"   Total trades: {self.total_trades}")
        logger.info("")
        await self.notifier.notify_signal(
            pair_name=pair.name,
            side=side,
            entry_price=signal.target_price,
            quantity=quantity,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
        return True

    async def check_positions(self) -> None:
        self._check_auto_reset_stop_loss_streak()
        closed_pairs: list[str] = []
        for pair_name, order in self.open_positions.items():
            position = await self.client.get_position("linear", order.symbol)
            if position is None or float(position.get("size", 0)) == 0:
                await self._handle_position_closed(order)
                closed_pairs.append(pair_name)
        for pair_name in closed_pairs:
            del self.open_positions[pair_name]

    async def _handle_position_closed(self, order: OrderRecord) -> None:
        logger.info("")
        logger.info(f"📊 ═══ Position Closed [{order.pair_name}] ═══")
        history = await self.client.get_order_history(
            category="linear",
            symbol=order.symbol,
            limit=10,
        )
        close_price = order.entry_price
        close_reason = "UNKNOWN"
        for hist_order in history:
            if hist_order["orderId"] == order.order_id:
                close_price = float(hist_order.get("avgPrice", order.entry_price))
                if close_price >= order.take_profit:
                    close_reason = "TP"
                elif close_price <= order.stop_loss:
                    close_reason = "SL"
                else:
                    close_reason = "MANUAL"
                break
        if order.side == "Buy":
            pnl = (close_price - order.entry_price) * order.quantity
        else:
            pnl = (order.entry_price - close_price) * order.quantity
        pnl_percent = (pnl / (order.entry_price * order.quantity)) * 100
        order.status = "CLOSED"
        order.closed_at = datetime.now()
        order.close_price = close_price
        order.pnl = pnl
        order.pnl_percent = pnl_percent
        order.close_reason = close_reason
        self.database.update_order(
            order.id,
            status="CLOSED",
            closed_at=order.closed_at,
            close_price=close_price,
            pnl=pnl,
            pnl_percent=pnl_percent,
            close_reason=close_reason,
        )
        if pnl > 0:
            self.profitable_trades += 1
            self._update_stop_loss_streak(increment=False)  # Сброс при прибыльной сделке
            logger.info("  Result: ✅ PROFIT")
        else:
            if close_reason == "SL":
                self._update_stop_loss_streak(increment=True)  # Инкремент при SL
            logger.info("  Result: ❌ LOSS")
        duration = (order.closed_at - order.opened_at).seconds
        logger.info(f"  Entry: ${order.entry_price:.8f}")
        logger.info(f"  Close: ${close_price:.8f}")
        logger.info(f"  P&L: {pnl:+.2f} USDT ({pnl_percent:+.2f}%)")
        logger.info(f"  Reason: {close_reason}")
        logger.info(f"  Duration: {duration}s")
        logger.info(f"  Win Rate: {self.get_win_rate():.1f}%")
        logger.info(f"  SL Streak: {self.stop_loss_streak}")
        logger.info("")
        await self.notifier.notify_trade_closed(
            pair_name=order.pair_name,
            pnl=pnl,
            pnl_percent=pnl_percent,
            close_reason=close_reason,
            duration_seconds=duration,
        )
        self.database.calculate_and_save_daily_stats()

    def has_position(self, pair_name: str) -> bool:
        return pair_name in self.open_positions

    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.profitable_trades / self.total_trades) * 100

    def get_stats(self) -> dict[str, object]:
        return {
            "total_trades": self.total_trades,
            "profitable_trades": self.profitable_trades,
            "win_rate": f"{self.get_win_rate():.1f}%",
            "stop_loss_streak": self.stop_loss_streak,
            "max_stop_loss_streak": self.max_stop_loss_streak,
            "open_positions": len(self.open_positions),
            "last_stop_loss_time": self.last_stop_loss_time.isoformat() if self.last_stop_loss_time else None,
        }
