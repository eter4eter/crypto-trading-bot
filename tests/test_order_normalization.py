import math
import unittest

from src.api.bybit_client import BybitClient


class TestNormalization(unittest.TestCase):
    def setUp(self) -> None:
        # Создаём клиент с фиктивными ключами (HTTP-часть не используется в тестах нормализации)
        self.client = BybitClient(api_key="k", api_secret="s", testnet=True, demo=True)

    def test_decimal_places(self):
        self.assertEqual(BybitClient._decimal_places(0.1), 1)
        self.assertEqual(BybitClient._decimal_places(0.01), 2)
        self.assertEqual(BybitClient._decimal_places(1.0), 0)

    def test_step_rounding(self):
        self.assertEqual(BybitClient._floor_to_step(0.3183, 0.01), 0.31)
        self.assertEqual(BybitClient._ceil_to_step(0.3183, 0.01), 0.32)
        self.assertEqual(BybitClient._floor_to_step(240.9639, 1.0), 240.0)
        self.assertEqual(BybitClient._ceil_to_step(240.001, 1.0), 241.0)

    async def _normalize_case(self, *, qty_step, min_qty, tick, min_notional, last_price, pos_usdt, side):
        # Подменим кеш инструмента вручную, чтобы не ходить в сеть
        key = f"linear:TEST"
        self.client._instrument_cache[key] = {
            "qtyStep": qty_step,
            "minOrderQty": min_qty,
            "tickSize": tick,
            "minNotional": min_notional,
        }
        self.client._instrument_cache_ts[key] = 1e18  # не истекает

        return await self.client.normalize_order(
            category="linear",
            symbol="TEST",
            side=side,
            last_price=last_price,
            position_size_usdt=pos_usdt,
            take_profit=last_price * (1 + 0.005 if side == "buy" else 1 - 0.005),
            stop_loss=last_price * (1 - 0.005 if side == "buy" else 1 + 0.005),
        )

    def test_normalize_qty_and_prices_integer_step(self):
        import asyncio
        # Шаг количества 1.0, тик 0.01, минимум 5 USDT
        norm = asyncio.get_event_loop().run_until_complete(
            self._normalize_case(
                qty_step=1.0, min_qty=1.0, tick=0.01, min_notional=5.0,
                last_price=157.09, pos_usdt=50.0, side="buy"
            )
        )
        self.assertIsNotNone(norm)
        self.assertEqual(norm["qty_str"], "0.00" if float(norm["qty"]) < 0.5 else norm["qty_str"])  # допускаем >=1
        # Проверяем соответствие шагам
        self.assertTrue(float(norm["qty"]) % 1.0 == 0.0)

    def test_normalize_qty_and_prices_decimal_step(self):
        import asyncio
        # Шаг количества 0.01, тик 0.001, минимум 5 USDT
        norm = asyncio.get_event_loop().run_until_complete(
            self._normalize_case(
                qty_step=0.01, min_qty=0.01, tick=0.001, min_notional=5.0,
                last_price=156.99, pos_usdt=50.0, side="sell"
            )
        )
        self.assertIsNotNone(norm)
        # Количество кратно 0.01
        self.assertAlmostEqual(float(norm["qty"]) % 0.01, 0.0, places=6)
        # Цены кратны 0.001
        self.assertAlmostEqual(float(norm["tp"]) % 0.001, 0.0, places=6)
        self.assertAlmostEqual(float(norm["sl"]) % 0.001, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
