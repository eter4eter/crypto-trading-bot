# ... [import/beginning skipped for brevity] ...
        # ... [unchanged before] ...
        result = await self.client.place_market_order(
            category=strategy_config.get_market_category(),
            symbol=target_pair,
            side=side,
            qty=qty_str,
            take_profit=take_profit_str,
            stop_loss=stop_loss_str,
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
            quantity=float(norm["qty"]),
            entry_price=sig_result.target_price,
            take_profit=float(norm["tp"]),
            stop_loss=float(norm["sl"]),
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
            quantity=float(norm["qty"]),
            take_profit=float(norm["tp"]),
            stop_loss=float(norm["sl"]),
            symbol=target_pair
        )
        return True
# ... тоже самое для _open_position ...
    async def _open_position(self, pair: PairConfig, signal: Signal) -> bool:
# ... начало осталось как было ...
        result = await self.client.place_market_order(
            category="linear",
            symbol=pair.target_pair,
            side=side,
            qty=qty_str,
            take_profit=take_profit_str,
            stop_loss=stop_loss_str,
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
            quantity=float(norm["qty"]),
            entry_price=signal.target_price,
            take_profit=float(norm["tp"]),
            stop_loss=float(norm["sl"]),
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
            quantity=float(norm["qty"]),
            take_profit=float(norm["tp"]),
            stop_loss=float(norm["sl"]),
            symbol=pair.target_pair
        )
        return True
# ...[rest unchanged]...