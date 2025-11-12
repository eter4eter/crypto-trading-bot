from datetime import datetime
import aiohttp

from ..logger import get_app_logger
from ..config import TelegramConfig

logger = get_app_logger()

class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config
        self.enabled = config.enabled and bool(config.bot_token) and bool(config.chat_id)

        if self.enabled:
            self.api_url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
            logger.info("TelegramNotifier initialized")
        else:
            logger.info("TelegramNotifier disabled")

    async def send_message(self, message: str, parse_mode: str = "HTML"):
        if not self.enabled:
            return
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.config.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                }
                async with session.post(self.api_url, json=payload) as response:
                    if response.status == 200:
                        logger.debug("Telegram message sent successfully")
                    else:
                        logger.error(f"Failed to send Telegram message: {response.status}")
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")

    async def notify_signal(
            self,
            pair_name: str,
            side: str,
            entry_price: float,
            quantity: float,
            take_profit: float,
            stop_loss: float,
            symbol: str = None
    ):
        """Уведомление об открытии позиции"""
        if not self.config.notify_trades:
            return
        sym_str = f"{symbol}" if symbol else pair_name
        message = f'''
✅ <b>Позиция открыта</b>

📊 Стратегия: <code>{pair_name}</code>
📈 Пара: <code>{sym_str}</code>
📍 Направление: <b>{side}</b>
💵 Вход: <code>${entry_price:.6f}</code>
📦 Размер: <code>{quantity:.4f}</code>

🎯 Take-Profit: <code>${take_profit:.6f}</code>
⛔ Stop-Loss: <code>${stop_loss:.6f}</code>

⏰ {datetime.now().strftime('%H:%M:%S')}
'''
        await self.send_message(message)

    async def notify_trade_closed(
            self,
            pair_name: str,
            pnl: float,
            pnl_percent: float,
            close_reason: str,
            duration_seconds: int,
    ):
        if not self.config.notify_trades:
            return
        emoji = "✅" if pnl > 0 else "❌"
        message = f'''
{emoji} <b>Позиция закрыта</b>

📊 Пара: <code>{pair_name}</code>
💰 P&L: <b>{pnl:+.2f} USDT ({pnl_percent:+.2f}%)</b>
📍 Причина: <b>{close_reason}</b>
⏱ Длительность: <code>{duration_seconds}s</code>

⏰ {datetime.now().strftime('%H:%M:%S')}
'''
        await self.send_message(message)

    async def notify_error(self, error_message: str):
        if not self.config.notify_errors:
            return
        message = f'''
⚠️ <b>Ошибка</b>

{error_message}

⏰ {datetime.now().strftime('%H:%M:%S')}
'''
        await self.send_message(message)

    async def notify_daily_report(self, stats: dict):
        if not self.config.notify_daily_report:
            return
        message = f'''
📊 <b>Дневной отчет</b>

📈 Сделок: <b>{stats['total_trades']}</b>
✅ Прибыльных: <b>{stats['profitable_trades']}</b>
📊 Win Rate: <b>{stats['win_rate']:.1f}%</b>

💰 Общий P&L: <b>{stats['total_pnl']:+.2f} USDT</b>
🏆 Лучшая: <b>{stats['best_trade']:+.2f} USDT</b>
📉 Худшая: <b>{stats['worst_trade']:+.2f} USDT</b>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}
'''
        await self.send_message(message)
