import asyncio
from datetime import datetime

from ..logger import logger
from ..api.bybit_client import BybitClient
from ..storage.models import OrderRecord


class OrderTracker:
    def __init__(self, client: BybitClient):
        self.client = client

        self.tracking_orders: dict[str, OrderRecord] = {}

        self.monitoring_task: asyncio.Task | None = None
        self.running = False

        logger.info("OrderTracker initialized")

    def track_order(self, order: OrderRecord):
        if order.order_id:
            self.tracking_orders[order.order_id] = order
            logger.debug(f"Tracking order: {order.order_id} ({order.pair_name}")

    def untrack_order(self, order_id: str):
        if order_id in self.tracking_orders:
            del self.tracking_orders[order_id]
            logger.debug(f"Stopped tracking order: {order_id}")

    async def start_monitoring(self):
        if self.running:
            return

        self.running = True
        self.monitoring_task = asyncio.create_task(self._monitor_loop())
        logger.info("Order monitoring started")

    async def stop_monitoring(self):
        self.running = False

        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass

        logger.info("Order monitoring stopped")

    async def _monitor_loop(self):
        while self.running:
            try:
                if self.tracking_orders:
                    await self._check_orders()

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(10)

    async def _check_orders(self):
        symbols_to_check = {}
        for order_id, order in self.tracking_orders.items():
            if order.symbol not in symbols_to_check:
                symbols_to_check[order.symbol] = []
            symbols_to_check[order.symbol].append(order)

        for symbol, orders in symbols_to_check.items():
            try:
                history = await self.client.get_order_history(
                    category="linear",
                    symbol=symbol,
                    limit=50,
                )

                for order in orders:
                    for hist_order in history:
                        if hist_order["orderId"] == order.order_id:
                            await self._process_order_update(order, hist_order)
                            break

            except Exception as e:
                logger.error(f"Error checking orders for symbol {symbol}: {e}")

    async def _process_order_update(self, order: OrderRecord, hist_order: dict):
        order_status = hist_order.get("orderStatus", "")

        if order_status in ("Filled", "Cancelled"):
            if order_status == "Filled":
                order.status = "CLOSED"
                order.closed_at = datetime.now()
                order.close_price = float(hist_order.get("avgPrice", order.entry_price))

                if order.side == "Buy":
                    pnl = (order.close_price - order.entry_price) * order.quantity
                else:
                    pnl = (order.entry_price - order.close_price) * order.quantity

                order.pnl = pnl
                order.pnl_percent = (pnl / (order.entry_price * order.quantity)) * 100

                if order.close_price >= order.take_profit:
                    order.close_reason = "TP"
                elif order.close_price <= order.stop_loss:
                    order.close_reason = "SL"
                else:
                    order.close_reason = "MANUAL"

                logger.info(f"Order filled: {order.order_id} ({order.pair_name})")
                logger.info(f"  P&L: {pnl:+.2f} USDT ({order.pnl_percent:+.2f}%)")

            elif order_status == "Cancelled":
                order.status = "CANCELLED"
                order.closed_at = datetime.now()
                logger.info(f"Order cancelled: {order.order_id} ({order.pair_name})")

            self.untrack_order(order.order_id)

    def get_stats(self) -> dict:
        return {
            "tracking_orders": len(self.tracking_orders),
            "monitoring_active": self.running,
        }
