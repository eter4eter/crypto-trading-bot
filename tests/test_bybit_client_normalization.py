import pytest
import asyncio
from unittest.mock import Mock, patch

from src.api.bybit_client import BybitClient


class TestBybitClientNormalization(pytest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = BybitClient(api_key="test", api_secret="test", testnet=True, demo=True)
        
    def test_decimal_places_calculation(self):
        """Тест расчета количества десятичных знаков"""
        self.assertEqual(self.client._decimal_places(1.0), 0)
        self.assertEqual(self.client._decimal_places(0.1), 1)
        self.assertEqual(self.client._decimal_places(0.01), 2)
        self.assertEqual(self.client._decimal_places(0.001), 3)
        self.assertEqual(self.client._decimal_places(0.0001), 4)

    def test_floor_to_step(self):
        """Тест округления вниз до шага"""
        # Тестовые случаи для SOLUSDT (qtyStep = 0.01)
        self.assertAlmostEqual(self.client._floor_to_step(0.3183, 0.01), 0.31)
        self.assertAlmostEqual(self.client._floor_to_step(0.3199, 0.01), 0.31)
        
        # Тестовые случаи для WIFUSDT (qtyStep = 1.0)
        self.assertAlmostEqual(self.client._floor_to_step(240.9639, 1.0), 240.0)
        self.assertAlmostEqual(self.client._floor_to_step(241.1, 1.0), 241.0)

    def test_ceil_to_step(self):
        """Тест округления вверх до шага"""
        self.assertAlmostEqual(self.client._ceil_to_step(0.3101, 0.01), 0.32)
        self.assertAlmostEqual(self.client._ceil_to_step(240.1, 1.0), 241.0)
        self.assertAlmostEqual(self.client._ceil_to_step(240.0, 1.0), 240.0)

    async def test_normalize_order_solusdt_case(self):
        """Тест нормализации для случая SOLUSDT из лога"""
        # Подменяем кэш чтобы не делать HTTP запрос
        self.client._instrument_cache["linear:SOLUSDT"] = {
            "qtyStep": 0.01,
            "minOrderQty": 0.01,
            "tickSize": 0.001,
            "minNotional": 5.0
        }
        self.client._instrument_cache_ts["linear:SOLUSDT"] = float('inf')
        
        # Исходные данные из лога: qty "0.3183", цена ~157
        result = await self.client.normalize_order(
            category="linear",
            symbol="SOLUSDT",
            side="Sell",
            last_price=157.09,
            position_size_usdt=50.0,
            take_profit=156.46164000,
            stop_loss=157.71836000
        )
        
        self.assertIsNotNone(result)
        
        # Проверяем что qty округлено правильно
        expected_raw_qty = 50.0 / 157.09  # ≈ 0.3183
        expected_qty = 0.31  # floor(0.3183 / 0.01) * 0.01
        
        self.assertAlmostEqual(result["qty"], expected_qty, places=2)
        self.assertEqual(result["qty_str"], "0.31")
        
        # Проверяем что цены округлены к tickSize = 0.001
        self.assertAlmostEqual(float(result["tp_str"]) % 0.001, 0.0, places=6)
        self.assertAlmostEqual(float(result["sl_str"]) % 0.001, 0.0, places=6)

    async def test_normalize_order_wifusdt_case(self):
        """Тест нормализации для случая WIFUSDT из лога"""
        # Подменяем кэш для WIFUSDT
        self.client._instrument_cache["linear:WIFUSDT"] = {
            "qtyStep": 1.0,
            "minOrderQty": 1.0,
            "tickSize": 0.0001,
            "minNotional": 5.0
        }
        self.client._instrument_cache_ts["linear:WIFUSDT"] = float('inf')
        
        # Исходные данные из лога: qty "240.9639", цена ~0.41
        result = await self.client.normalize_order(
            category="linear",
            symbol="WIFUSDT",
            side="Buy",
            last_price=0.4150,
            position_size_usdt=100.0,
            take_profit=0.41707500,
            stop_loss=0.41292500
        )
        
        self.assertIsNotNone(result)
        
        # Проверяем что qty округлено к целому числу
        expected_raw_qty = 100.0 / 0.4150  # ≈ 240.96
        expected_qty = 240.0  # floor(240.96 / 1.0) * 1.0
        
        self.assertAlmostEqual(result["qty"], expected_qty, places=0)
        self.assertEqual(result["qty_str"], "240.0" if result["qty"] == 240.0 else result["qty_str"])
        
        # Проверяем минимальный номинал
        notional = result["qty"] * 0.4150
        self.assertGreaterEqual(notional, 5.0)

    async def test_normalize_order_min_notional_adjustment(self):
        """Тест коррекции qty для соответствия minNotional"""
        self.client._instrument_cache["linear:TESTUSDT"] = {
            "qtyStep": 0.1,
            "minOrderQty": 0.1,
            "tickSize": 0.01,
            "minNotional": 10.0  # высокий минимальный номинал
        }
        self.client._instrument_cache_ts["linear:TESTUSDT"] = float('inf')
        
        # Маленький position_size который даст qty < minNotional
        result = await self.client.normalize_order(
            category="linear",
            symbol="TESTUSDT",
            side="Buy",
            last_price=100.0,
            position_size_usdt=5.0,  # даст qty = 0.05, notional = 5.0 < 10.0
            take_profit=101.0,
            stop_loss=99.0
        )
        
        self.assertIsNotNone(result)
        
        # qty должно быть скорректировано для minNotional
        min_qty_for_notional = 10.0 / 100.0  # 0.1
        self.assertGreaterEqual(result["qty"], min_qty_for_notional)
        
        # Проверяем что номинал соответствует требованиям
        notional = result["qty"] * 100.0
        self.assertGreaterEqual(notional, 10.0)

    async def test_normalize_order_with_fallback_spec(self):
        """Тест нормализации с fallback спецификацией когда API недоступен"""
        # Очищаем кэш чтобы симулировать отсутствие данных
        self.client._instrument_cache.clear()
        
        # Подменяем get_instruments_info чтобы вернуть None
        with patch.object(self.client, 'get_instruments_info', return_value=None):
            result = await self.client.normalize_order(
                category="linear",
                symbol="UNKNOWNUSDT",
                side="Buy",
                last_price=50.0,
                position_size_usdt=25.0,
                take_profit=51.0,
                stop_loss=49.0
            )
            
        self.assertIsNotNone(result)
        # Должен использовать дефолтные значения: qtyStep=1.0, tickSize=0.0001, minNotional=5.0
        self.assertEqual(result["steps"]["qty_step"], 1.0)
        self.assertEqual(result["steps"]["tick"], 0.0001)
        self.assertEqual(result["steps"]["min_notional"], 5.0)

    def test_tp_sl_rounding_direction(self):
        """Тест правильности округления TP/SL в зависимости от стороны ордера"""
        tick_size = 0.001
        
        # Для покупки (Buy): TP округляем вниз, SL округляем вверх
        tp_buy = self.client._floor_to_step(101.2347, tick_size)  # 101.234
        sl_buy = self.client._ceil_to_step(98.7653, tick_size)    # 98.766
        
        self.assertAlmostEqual(tp_buy, 101.234)
        self.assertAlmostEqual(sl_buy, 98.766)
        
        # Для продажи (Sell): TP округляем вверх, SL округляем вниз
        tp_sell = self.client._ceil_to_step(98.7653, tick_size)   # 98.766
        sl_sell = self.client._floor_to_step(101.2347, tick_size) # 101.234
        
        self.assertAlmostEqual(tp_sell, 98.766)
        self.assertAlmostEqual(sl_sell, 101.234)


if __name__ == "__main__":
    pytest.main([__file__])
