"""
Мультисигнальная стратегия с унифицированным провайдером данных

Поддерживает множественные сигналы с разными timeframe:
- < 1 минуты: REST polling
- ≥ 1 минуты: WebSocket подписки
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from ..config import StrategyConfig, SignalConfig
from ..api.bybit_client import BybitClient
from ..api.bybit_websocket_client import BybitWebSocketClient
from ..api.common import Kline
from ..api.market_data_provider import MultiMarketDataProvider
from ..logger import logger


@dataclass
class SignalResult:
    """Результат обработки сигнала"""
    signal_name: str
    strategy_name: str
    action: Literal["Buy", "Sell", "None"]
    index_pair: str
    target_pairs: list[str]
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
    Мультисигнальная стратегия с автоматическим выбором источника данных:
    
    Источники данных по frame:
    - Секундные ("1s", "5s", "10s", ...) → REST polling
    - Минутные ("1", "5", "15", "60", "D", ...) → WebSocket
    
    Логика сигналов согласно ТЗ:
    1. Накопление тиков в окнах tick_window
    2. Сравнение первого и последнего значения
    3. Проверка корреляции и порогов
    4. Применение reverse и direction логики
    5. Проверка проскальзывания
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
        
        # Унифицированный провайдер данных
        self.data_provider = MultiMarketDataProvider(
            strategy_config=config,
            rest_client=rest_client,
            ws_client=ws_client
        )
        
        # Буферы для каждого сигнала
        self.signal_buffers: dict[str, dict[str, deque]] = {}
        self.signal_callbacks: dict[str, Callable] = {}
        self.signal_locks: dict[str, asyncio.Lock] = {}
        self.strategy_callback: Callable | None = None
        
        # Инициализируем буферы
        self._initialize_buffers()
        
        self.signals_generated = 0
        self.history_loaded = False
        
        logger.info(f"✅ MultiSignalStrategy [{config.name}] с унифицированным провайдером")
        logger.info(f"   Торговые пары: {config.trade_pairs}")
        logger.info(f"   Количество сигналов: {len(config.signals)}")
        for signal_name, signal_config in config.signals.items():
            mode = "polling" if signal_config.frame.endswith("s") else "websocket"
            logger.info(f"   - {signal_name}: {signal_config.index}+targets ({signal_config.frame}, {mode})")
    
    def _initialize_buffers(self):
        """Инициализация буферов для всех сигналов"""
        for signal_name, signal_config in self.config.signals.items():
            window_size = signal_config.tick_window if signal_config.tick_window > 0 else 2
            
            self.signal_buffers[signal_name] = {
                "index_prices": deque(maxlen=window_size),
                "target_prices": {}
            }
            
            # Буферы для каждой торговой пары
            for trade_pair in self.config.trade_pairs:
                self.signal_buffers[signal_name]["target_prices"][trade_pair] = deque(maxlen=window_size)
            
            self.signal_locks[signal_name] = asyncio.Lock()

    async def preload_history(self):
        """Предзагрузка исторических данных для всех сигналов"""
        logger.info(f"[{self.config.name}] 📅 Загрузка исторических данных...")
        
        for signal_name, signal_config in self.config.signals.items():
            try:
                # Определяем количество свечей
                limit = max(signal_config.tick_window, 2) if signal_config.tick_window > 0 else 2
                
                # Загружаем историю index пары
                index_klines = await self.rest_client.get_klines(
                    category=self.config.get_market_category(),
                    symbol=signal_config.index,
                    interval=signal_config.frame,
                    limit=limit
                )
                
                if not index_klines:
                    logger.error(f"[{self.config.name}] Не удалось загрузить index данные: {signal_config.index} @ {signal_config.frame}")
                    continue
                
                # Загружаем историю для всех target пар
                target_klines_data = {}
                for trade_pair in self.config.trade_pairs:
                    target_klines = await self.rest_client.get_klines(
                        category=self.config.get_market_category(),
                        symbol=trade_pair,
                        interval=signal_config.frame,
                        limit=limit
                    )
                    
                    if target_klines:
                        target_klines_data[trade_pair] = target_klines
                    else:
                        logger.warning(f"[{self.config.name}] Не удалось загрузить {trade_pair} @ {signal_config.frame}")
                
                if not target_klines_data:
                    logger.error(f"[{self.config.name}] Нет target данных для {signal_name}")
                    continue
                
                # Заполняем буферы (n-1 свечей)
                async with self.signal_locks[signal_name]:
                    buffer = self.signal_buffers[signal_name]
                    
                    if signal_config.tick_window > 0:
                        # Полное окно: кроме последней свечи
                        for kline in index_klines[:-1]:
                            buffer["index_prices"].append(kline.close)
                        
                        for trade_pair, klines in target_klines_data.items():
                            for kline in klines[:-1]:
                                buffer["target_prices"][trade_pair].append(kline.close)
                    else:
                        # tick_window=0: только предпоследняя свеча
                        if len(index_klines) >= 2:
                            buffer["index_prices"].append(index_klines[-2].close)
                        
                        for trade_pair, klines in target_klines_data.items():
                            if len(klines) >= 2:
                                buffer["target_prices"][trade_pair].append(klines[-2].close)
                
                loaded_count = len(buffer["index_prices"])
                logger.info(f"   ✅ {signal_name}: {loaded_count} свечей загружено ({signal_config.frame})")
                
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка загрузки сигнала {signal_name}: {e}")
        
        self.history_loaded = True
        return True

    async def start(self):
        """Запуск стратегии: унифицированный провайдер данных"""
        
        # Устанавливаем callback для приема данных
        self.data_provider.set_callback(self._on_kline_data)
        
        # Запускаем провайдер (он сам определит WS/polling по frame)
        await self.data_provider.start()
        
        logger.info(f"[{self.config.name}] ✅ Мультисигнальная стратегия активна")

    async def stop(self):
        """Остановка стратегии"""
        logger.info(f"[{self.config.name}] ⏹ Остановка стратегии...")
        
        # Останавливаем провайдер
        await self.data_provider.stop()
        
        # Очищаем все буферы
        for signal_name in self.signal_buffers.keys():
            async with self.signal_locks[signal_name]:
                buffer = self.signal_buffers[signal_name]
                buffer["index_prices"].clear()
                
                # Корректное очищение словаря target_prices
                for _, target_deque in buffer["target_prices"].items():
                    target_deque.clear()
        
        logger.info(f"[{self.config.name}] ✅ Стратегия остановлена")

    async def _on_kline_data(self, symbol: str, kline: Kline):
        """Центральная обработка всех kline данных (из WS и polling)"""
        
        # Обновляем буферы всех сигналов, где фигурирует этот symbol
        for signal_name, signal_config in self.config.signals.items():
            try:
                async with self.signal_locks[signal_name]:
                    buffer = self.signal_buffers[signal_name]
                    
                    if symbol == signal_config.index:
                        # Обновляем index буфер
                        buffer["index_prices"].append(kline.close)
                        
                    elif symbol in self.config.trade_pairs:
                        # Обновляем target буфер
                        if symbol in buffer["target_prices"]:
                            buffer["target_prices"][symbol].append(kline.close)
                
                # Проверяем сигнал после каждого обновления
                await self._check_signal(signal_name, signal_config)
                
            except Exception as e:
                logger.error(f"[{self.config.name}] Ошибка обработки kline {symbol} для {signal_name}: {e}")

    async def _check_signal(self, signal_name: str, signal_config: SignalConfig):
        """Проверка условий сигнала согласно ТЗ"""
        
        buffer = self.signal_buffers[signal_name]
        required_size = signal_config.tick_window if signal_config.tick_window > 0 else 2
        
        # Проверяем заполненность index буфера
        if len(buffer["index_prices"]) < required_size:
            return
        
        # Проверяем каждую target пару
        for trade_pair in self.config.trade_pairs:
            if trade_pair not in buffer["target_prices"]:
                continue
                
            if len(buffer["target_prices"][trade_pair]) < required_size:
                continue
            
            # Получаем значения для анализа
            if signal_config.tick_window > 0:
                # Окно: первое и последнее значение
                index_first = buffer["index_prices"][0]
                index_last = buffer["index_prices"][-1]
                target_first = buffer["target_prices"][trade_pair][0]
                target_last = buffer["target_prices"][trade_pair][-1]
            else:
                # tick_window=0: последние 2 значения
                index_first = buffer["index_prices"][-2]
                index_last = buffer["index_prices"][-1]
                target_first = buffer["target_prices"][trade_pair][-2]
                target_last = buffer["target_prices"][trade_pair][-1]
            
            # Проверка на нулевые цены
            if index_first == 0 or target_first == 0:
                continue
            
            # Расчет изменений в процентах
            index_change = ((index_last - index_first) / index_first) * 100
            target_change = ((target_last - target_first) / target_first) * 100
            
            # Проверка порога index пары
            if abs(index_change) < signal_config.index_change_threshold:
                continue
            
            # Проверка направления index (сигнального) direction
            if signal_config.direction != 0:
                if signal_config.direction == 1 and index_change < 0:
                    continue  # Нужен рост, а получили падение
                if signal_config.direction == -1 and index_change > 0:
                    continue  # Нужно падение, а получили рост
            
            # Проверка максимального порога target пары
            if abs(target_change) >= signal_config.target:
                continue  # Превышен target порог
            
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
            
            # Проверяем direction на уровне стратегии
            if not self.config.should_take_signal(action):
                continue
            
            # Проверка проскальзывания
            current_price = await self._get_current_price(trade_pair)
            if current_price == 0:
                logger.warning(f"[{self.config.name}] Не удалось получить текущую цену {trade_pair}")
                continue
                
            price_diff_percent = abs((current_price - target_last) / target_last) * 100
            slippage_ok = price_diff_percent <= self.config.price_change_threshold
            
            # Создаем SignalResult
            signal_result = SignalResult(
                signal_name=signal_name,
                strategy_name=self.config.name,
                action=action,
                index_pair=signal_config.index,
                target_pairs=[trade_pair],
                target_price=current_price,
                index_change=index_change,
                target_change=target_change,
                triggered=True,
                slippage_ok=slippage_ok
            )
            
            self.signals_generated += 1
            
            # Логирование сигнала с указанием источника данных
            data_source = "📡 Polling" if signal_config.frame.endswith("s") else "🔌 WebSocket"
            
            logger.info("")
            logger.info(f"🎯 СИГНАЛ [{self.config.name}:{signal_name}] {action} ({data_source})")
            logger.info(f"   Index ({signal_config.index} @ {signal_config.frame}): {index_change:+.3f}%")
            logger.info(f"   Target ({trade_pair} @ {signal_config.frame}): {target_change:+.3f}%")
            logger.info(f"   Price: ${current_price:.8f} (slippage: {price_diff_percent:.2f}%)")
            logger.info(f"   Reverse: {'ON' if signal_config.reverse else 'OFF'}")
            logger.info(f"   Window: {signal_config.tick_window} ({'last 2' if signal_config.tick_window == 0 else 'candles'})")
            logger.info(f"   Slippage: {'\u2705 OK' if slippage_ok else '\u26a0\ufe0f EXCEEDED'}")
            logger.info("")
            
            # Вызываем callback
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
            
            logger.warning(f"Не удалось получить текущую цену {symbol}")
            return 0.0
            
        except Exception as e:
            logger.error(f"Ошибка получения цены {symbol}: {e}")
            return 0.0

    def set_signal_callback(self, signal_name: str, callback: Callable):
        """Установка callback для конкретного сигнала"""
        self.signal_callbacks[signal_name] = callback

    def set_strategy_callback(self, callback: Callable):
        """Установка общего callback для стратегии"""
        self.strategy_callback = callback

    async def reset_buffers(self):
        """Сброс буферов и перезагрузка истории"""
        for signal_name in self.signal_buffers.keys():
            async with self.signal_locks[signal_name]:
                buffer = self.signal_buffers[signal_name]
                buffer["index_prices"].clear()
                
                # Корректное очищение target_prices
                for _, target_deque in buffer["target_prices"].items():
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
