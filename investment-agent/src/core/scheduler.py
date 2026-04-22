"""定刻タスクスケジューラ."""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)


class AgentScheduler:
    """エージェントのタスクスケジューリングを管理する."""

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=app_config["general"]["timezone"])

    def register_source_monitor(self, func: ...) -> None:
        """ソース監視を登録する."""
        interval = app_config["scheduler"]["source_monitor_interval_min"]
        self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(minutes=interval),
            id="source_monitor",
            name="ソース監視",
        )
        log.info("source_monitor_registered", interval_min=interval)

    def register_analysis(self, func: ...) -> None:
        """分析実行を登録する."""
        interval = app_config["scheduler"]["analysis_interval_min"]
        self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(minutes=interval),
            id="analysis",
            name="分析実行",
        )
        log.info("analysis_registered", interval_min=interval)

    def register_daily_order(self, func: ...) -> None:
        """毎日の発注を登録する."""
        time_str = app_config["scheduler"]["order_time"]
        hour, minute = time_str.split(":")
        self.scheduler.add_job(
            func,
            trigger=CronTrigger(hour=int(hour), minute=int(minute)),
            id="daily_order",
            name="毎日発注",
        )
        log.info("daily_order_registered", time=time_str)

    def start(self) -> None:
        """スケジューラを開始する."""
        self.scheduler.start()
        log.info("scheduler_started")

    def shutdown(self) -> None:
        """スケジューラを停止する."""
        self.scheduler.shutdown()
        log.info("scheduler_stopped")
