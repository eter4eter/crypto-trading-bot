from datetime import datetime

from ..logger import get_app_logger
from ..storage.database import Database

logger = get_app_logger()


class StatisticsMonitor:
    """Мониторинг и аналитика статистики"""

    def __init__(self, database: Database):
        self.database = database
        logger.info("StatisticsMonitor initialized")

    def get_today_stats(self) -> dict:
        """Статистика за сегодня"""
        return self.database.get_statistics_summary(days=1)

    def get_week_stats(self) -> dict:
        """Статистика за неделю"""
        return self.database.get_statistics_summary(days=7)

    def get_month_stats(self) -> dict:
        """Статистика за месяц"""
        return self.database.get_statistics_summary(days=30)

    def get_comprehensive_report(self) -> dict:
        """Комплексный отчет"""

        today = self.get_today_stats()
        week = self.get_week_stats()
        month = self.get_month_stats()

        return {
            "generated_at": datetime.now().isoformat(),
            "today": today,
            "last_7_days": week,
            "last_30_days": month
        }

    def format_report(self, report: dict) -> str:
        """Форматирование отчета для вывода"""

        lines = []
        lines.append("=" * 60)
        lines.append("TRADING STATISTICS REPORT")
        lines.append(f"Generated: {report['generated_at']}")
        lines.append("=" * 60)

        for period_name, period_data in [
            ("TODAY", report['today']),
            ("LAST 7 DAYS", report['last_7_days']),
            ("LAST 30 DAYS", report['last_30_days'])
        ]:
            lines.append(f"\n{period_name}:")
            lines.append(f"  Total Trades: {period_data['total_trades']}")
            lines.append(f"  Profitable: {period_data['profitable_trades']}")
            lines.append(f"  Win Rate: {period_data['win_rate']}%")
            lines.append(f"  Total P&L: {period_data['total_pnl']:+.2f} USDT")
            lines.append(f"  Avg P&L%: {period_data['avg_pnl_percent']:+.2f}%")
            lines.append(f"  Best Trade: {period_data['best_trade']:+.2f} USDT")
            lines.append(f"  Worst Trade: {period_data['worst_trade']:+.2f} USDT")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)
