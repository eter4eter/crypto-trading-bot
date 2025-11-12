from datetime import datetime, date
import os

from ..logger import get_app_logger
from ..storage.database import Database

logger = get_app_logger()

class StatisticsMonitor:
    """Мониторинг и аналитика статистики с антиповтором для daily_report"""
    
    def __init__(self, database: Database):
        self.database = database
        self._last_daily_report_date = None
        self._state_file = os.environ.get("STATISTICS_DAILYREPORT_STATE", ".daily_report_sent")
        self._restore_last_report_date()
        logger.info("StatisticsMonitor initialized")

    def _restore_last_report_date(self):
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r") as f:
                    date_str = f.read().strip()
                    if date_str:
                        self._last_daily_report_date = date.fromisoformat(date_str)
        except Exception as e:
            logger.warning(f"Failed to restore last daily report date: {e}")
    
    def _save_last_report_date(self, report_date: date):
        try:
            with open(self._state_file, "w") as f:
                f.write(report_date.isoformat())
        except Exception as e:
            logger.warning(f"Failed to persist last daily report date: {e}")

    def can_send_daily_report(self) -> bool:
        today = date.today()
        if self._last_daily_report_date == today:
            return False
        return True

    def mark_daily_report_sent(self):
        today = date.today()
        self._last_daily_report_date = today
        self._save_last_report_date(today)

    def get_today_stats(self) -> dict:
        return self.database.get_statistics_summary(days=1)

    def get_week_stats(self) -> dict:
        return self.database.get_statistics_summary(days=7)

    def get_month_stats(self) -> dict:
        return self.database.get_statistics_summary(days=30)

    def get_comprehensive_report(self) -> dict:
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
