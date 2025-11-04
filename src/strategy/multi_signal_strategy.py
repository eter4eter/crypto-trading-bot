"""
Мультисигнальная стратегия согласно техническому заданию
Поддержка множественных сигналов на одну стратегию
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Callable, Any, Literal

from ..config import StrategyConfig, SignalConfig
from ..api.bybit_client import BybitClient
from ..api.bybit_websocket_client import BybitWebSocketClient
from ..api.common import Kline
from ..logger import logger


@dataclass
class SignalResult:
    """Результат обработки сигнала"""
    signal_name: str
    strategy_name: str
    action: Literal["Buy", "Sell", "None"]
    index_pair: str
    target_pairs: List[str]
    target_price: float
    index_change: float
    target_change: float
    triggered: bool = False
    slippage_ok: bool = True
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class MultiSignalStrategy:
    """
    Стратегия с поддержкой множественных сигналов согласно ТЗ
    
    Полная реализация логики из ТЗ:
    1. Накопление тиков в массивах tick_window
    2. Сравнение первого и последнего значения в массиве
    3. Проверка корреляции между валютами
    4. Применение логики reverse и direction
    5. Генерация сигнала при соблюдении условий
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        rest_client: BybitClient,
        ws_client: BybitWebSocketClient
    ):
        self.config = config
        self.rest_client = rest_client
        self.ws_client = ws_client
        
        # Буферы для каждого сигнала
        self.signal_buffers: Dict[str, Dict[str, Any]] = {}
        self.signal_callbacks: Dict[str, Callable] = {}
        
        # Локи для каждого сигнала
        self.signal_locks: Dict[str, asyncio.Lock] = {}
        
        # Общие callbackи
        self.strategy_callback: Callable = None
        
        # Инициализируем буферы для всех сигналов
        for signal_name, signal_config in config.signals.items():
            window_size = signal_config.tick_window if signal_config.tick_window > 0 else 2
            
            self.signal_buffers[signal_name] = {
                "index_prices": deque(maxlen=window_size),
                "target_prices": {}  # Dict[str, deque]
            }
            
            # Буферы для каждой торговой пары
            for trade_pair in config.trade_pairs:
                self.signal_buffers[signal_name]["target_prices"][trade_pair] = deque(maxlen=window_size)
            
            self.signal_locks[signal_name] = asyncio.Lock()
        
        self.signals_generated = 0
        self.history_loaded = False
        
        logger.info(f"✅ MultiSignalStrategy [{config.name}] инициализирована")
        logger.info(f"   Торговые пары: {config.trade_pairs}")
        logger.info(f"   Количество сигналов: {len(config.signals)}")
        for signal_name, signal_config in config.signals.items():
            logger.info(f"   - {signal_name}: {signal_config.index} -> frame:{signal_config.frame}, window:{signal_config.tick_window}")

    async def preload_history(self):
        """Предзагрузка исторических данных для всех сигналов"""
        
        logger.info(f"[{self.config.name}] 📅 Загрузка исторических данных...")
        
        for signal_name, signal_config in self.config.signals.items():
            try:
                # Определяем количество данных для загрузки
                limit = max(signal_config.tick_window, 2) if signal_config.tick_window > 0 else 2
                
                # Загружаем историю для index пары
                index_klines = await self.rest_client.get_klines(
                    category=self.config.get_market_category(),
                    symbol=signal_config.index,
                    interval=signal_config.frame,
                    limit=limit
                )
                
                if not index_klines:
                    logger.error(f"[{self.config.name}] Не удалось загрузить index данные для {signal_name}")
                    continue
                
                # Загружаем историю для target пар
                target_klines_data = {}
                for trade_pair in self.config.trade_pairs:
                    target_klines = await self.rest_client.get_klines(
                        category=self.config.get_market_category(),
                        symbol=trade_pair,
                        interval=signal_config.frame,
                        limit=limit
                    )
                    
                    if not target_klines:
                        logger.error(f"[{self.config.name}] Не удалось загрузить target данные для {trade_pair}")
                        continue
                    
                    target_klines_data[trade_pair] = target_klines
                
                if not target_klines_data:
                    logger.error(f"[{self.config.name}] Не удалось загрузить target данные для {signal_name}")
                    continue
                
                # Заполняем буферы
                async with self.signal_locks[signal_name]:
                    buffer = self.signal_buffers[signal_name]
                    
                    if signal_config.tick_window > 0:
                        # Заполняем полный буфер кроме последней свечи
                        for kline in index_klines[:-1]:
                            buffer["index_prices"].append(kline.close)
                        
                        for trade_pair, klines in target_klines_data.items():
                            for kline in klines[:-1]:
                                buffer["target_prices"][trade_pair].append(kline.close)
                    else:
                        # Для tick_window=0 берем только предпоследнюю свечу
                        if len(index_klines) >= 2:
                            buffer["index_prices"].append(index_klines[-2].close)
                        
                        for trade_pair, klines in target_klines_data.items():
                            if len(klines) >= 2:
                                buffer["target_prices"][trade_pair].append(klines[-2].close)
                
                logger.info(f"   ✅ {signal_name}: {len(buffer['index_prices'])} свечей загружено")
                
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка загрузки данных для {signal_name}: {e}")
        
        self.history_loaded = True
        return True

    async def start(self):
        """Запуск стратегии с подписками на все необходимые пары"""
        
        # Подписываемся на все уникальные пары из сигналов
        unique_pairs = set()
        
        # Добавляем index пары из сигналов
        for signal_config in self.config.signals.values():
            unique_pairs.add(signal_config.index)
        
        # Добавляем торговые пары
        for trade_pair in self.config.trade_pairs:
            unique_pairs.add(trade_pair)
        
        # Подписываемся на kline streams
        for pair in unique_pairs:
            try:
                await self.ws_client.subscribe_kline(
                    category=self.config.get_market_category(),
                    symbol=pair,
                    interval="1",  # Базовый интервал
                    callback=self._on_kline_data
                )
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка подписки на {pair}: {e}")
        
        logger.info(f"[{self.config.name}] ✅ Подписки активированы для {len(unique_pairs)} пар")
    
    async def stop(self):
        """Остановка стратегии"""
        logger.info(f"[{self.config.name}] ⏹ Останавливаем стратегию...")
        
        # Очищаем буферы корректно
        for signal_name in self.signal_buffers.keys():
            async with self.signal_locks[signal_name]:
                buffer = self.signal_buffers[signal_name]
                buffer["index_prices"].clear()
                
                # Очищаем словарь target_prices корректно
                target_prices_dict = buffer["target_prices"]
                for pair_name, target_deque in target_prices_dict.items():
                    target_deque.clear()
        
        logger.info(f"[{self.config.name}] ✅ Стратегия остановлена")

    async def _on_kline_data(self, symbol: str, kline: Kline):
        """Обработка входящих kline данных"""
        
        # Проверяем все сигналы
        for signal_name, signal_config in self.config.signals.items():
            try:
                async with self.signal_locks[signal_name]:
                    # Добавляем данные в соответствующие буферы
                    buffer = self.signal_buffers[signal_name]
                    
                    if symbol == signal_config.index:
                        # Обновляем index буфер
                        buffer["index_prices"].append(kline.close)
                    elif symbol in self.config.trade_pairs:
                        # Обновляем target буфер
                        if symbol in buffer["target_prices"]:
                            buffer["target_prices"][symbol].append(kline.close)
                
                # Проверяем условия сигнала
                await self._check_signal(signal_name, signal_config)
                
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка обработки kline для {signal_name}: {e}")

    async def _check_signal(self, signal_name: str, signal_config: SignalConfig):
        """Проверка условий конкретного сигнала согласно ТЗ"""
        
        buffer = self.signal_buffers[signal_name]
        
        # Проверяем заполненность буферов
        required_size = signal_config.tick_window if signal_config.tick_window > 0 else 2
        
        if len(buffer["index_prices"]) < required_size:
            return
        
        # Проверяем все target пары
        for trade_pair in self.config.trade_pairs:
            if trade_pair not in buffer["target_prices"]:
                continue
                
            if len(buffer["target_prices"][trade_pair]) < required_size:
                continue
            
            # Получаем значения для анализа
            if signal_config.tick_window > 0:
                index_first = buffer["index_prices"][0]
                index_last = buffer["index_prices"][-1]
                target_first = buffer["target_prices"][trade_pair][0]
                target_last = buffer["target_prices"][trade_pair][-1]
            else:
                # tick_window=0: сравниваем последние 2 значения
                index_first = buffer["index_prices"][-2]
                index_last = buffer["index_prices"][-1]
                target_first = buffer["target_prices"][trade_pair][-2]
                target_last = buffer["target_prices"][trade_pair][-1]
            
            if index_first == 0 or target_first == 0:
                continue
            
            # Расчет изменений
            index_change = ((index_last - index_first) / index_first) * 100
            target_change = ((target_last - target_first) / target_first) * 100
            
            # Проверка порога index пары
            if abs(index_change) < signal_config.index_change_threshold:
                continue
            
            # Проверка направления движения index пары
            if signal_config.direction != 0:
                if signal_config.direction == 1 and index_change < 0:
                    continue  # Нужен рост, а получили падение
                if signal_config.direction == -1 and index_change > 0:
                    continue  # Нужно падение, а получили рост
            
            # Проверка целевого значения
            if abs(target_change) >= signal_config.target:
                continue  # Превышен максимальный порог target пары
            
            # Проверка корреляции (одинаковое направление)
            same_direction = (
                (index_change > 0 and target_change > 0) or
                (index_change < 0 and target_change < 0)
            )
            
            if not same_direction:
                continue
            
            # Генерируем базовое действие
            raw_action = "Buy" if index_change > 0 else "Sell"
            
            # Применяем reverse логику
            if signal_config.reverse == 1:
                action = "Sell" if raw_action == "Buy" else "Buy"
            else:
                action = raw_action
            
            # Проверяем направление стратегии
            if not self.config.should_take_signal(action):
                continue
            
            # Проверяем проскальзывание (реальная реализация)
            current_price = await self._get_current_price(trade_pair)
            price_diff_percent = abs((current_price - target_last) / target_last) * 100
            slippage_ok = price_diff_percent <= self.config.price_change_threshold
            
            # Создаем результат сигнала
            signal_result = SignalResult(
                signal_name=signal_name,
                strategy_name=self.config.name,
                action=action,
                index_pair=signal_config.index,
                target_pairs=[trade_pair],
                target_price=current_price,  # Используем текущую цену
                index_change=index_change,
                target_change=target_change,
                triggered=True,
                slippage_ok=slippage_ok
            )
            
            self.signals_generated += 1
            
            logger.info(f"")
            logger.info(f"🎯 СИГНАЛ [{self.config.name}:{signal_name}] {action}")
            logger.info(f"   Index ({signal_config.index}): {index_change:+.3f}%")
            logger.info(f"   Target ({trade_pair}): {target_change:+.3f}%")
            logger.info(f"   Price: ${current_price:.8f} (slippage: {price_diff_percent:.2f}%)")
            logger.info(f"   Reverse: {'ON' if signal_config.reverse else 'OFF'}")
            logger.info(f"   Window: {signal_config.tick_window}")
            logger.info(f"")
            
            # Вызываем callback если есть
            if signal_name in self.signal_callbacks:
                await self.signal_callbacks[signal_name](signal_result)
            elif self.strategy_callback:
                await self.strategy_callback(signal_result)
    
    async def _get_current_price(self, symbol: str) -> float:
        """Получение текущей цены символа"""
        try:
            ticker = await self.rest_client.get_ticker(
                category=self.config.get_market_category(),
                symbol=symbol
            )
            
            if ticker and 'result' in ticker and 'list' in ticker['result']:
                ticker_data = ticker['result']['list'][0]
                return float(ticker_data.get('lastPrice', 0))
            
            logger.warning(f"Failed to get current price for {symbol}, using 0")
            return 0.0
            
        except Exception as e:
            logger.error(f"Error getting current price for {symbol}: {e}")
            return 0.0

    def set_signal_callback(self, signal_name: str, callback: Callable):
        """Установка callback для конкретного сигнала"""
        self.signal_callbacks[signal_name] = callback

    def set_strategy_callback(self, callback: Callable):
        """Установка общего callback для стратегии"""
        self.strategy_callback = callback

    async def reset_buffers(self):
        """Сброс буферов (после сделки нужно перезагрузить историю)"""
        for signal_name in self.signal_buffers.keys():
            async with self.signal_locks[signal_name]:
                buffer = self.signal_buffers[signal_name]
                buffer["index_prices"].clear()
                
                # Очищаем словарь target_prices корректно
                target_prices_dict = buffer["target_prices"]
                for pair_name, target_deque in target_prices_dict.items():
                    target_deque.clear()
        
        self.history_loaded = False
        logger.info(f"[{self.config.name}] 🔄 Буферы сброшены")
        await self.preload_history()

    def get_status(self) -> dict:
        """Получение статуса стратегии"""
        return {
            "name": self.config.name,
            "signals_count": len(self.config.signals),
            "signals_generated": self.signals_generated,
            "trade_pairs": self.config.trade_pairs,
            "leverage": self.config.leverage,
            "history_loaded": self.history_loaded,
            "buffers_status": {
                signal_name: {
                    "index_buffer": len(buffer["index_prices"]),
                    "target_buffers": {
                        pair: len(target_buffer) 
                        for pair, target_buffer in buffer["target_prices"].items()
                    }
                }
                for signal_name, buffer in self.signal_buffers.items()
            }
        }
