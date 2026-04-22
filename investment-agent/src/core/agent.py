"""メインエージェントオーケストレーター."""
from __future__ import annotations

import asyncio
import signal

from src.core.config import settings
from src.core.logger import get_logger, setup_logging
from src.core.scheduler import AgentScheduler

log = get_logger(__name__)


class InvestmentAgent:
    """投資AIエージェントのメインクラス."""

    def __init__(self) -> None:
        setup_logging()
        self.scheduler = AgentScheduler()
        self._running = False

    async def _monitor_sources(self) -> None:
        """外部ソースを監視しアイディアを抽出する."""
        log.info("source_monitor_triggered")
        # TODO: 各ソース（X, YouTube, ArXiv）を巡回
        # TODO: LLMでアイディア抽出
        # TODO: アイディアDBに登録

    async def _run_analysis(self) -> None:
        """キューにあるアイディアを分析する."""
        log.info("analysis_triggered")
        # TODO: キューからアイディアをpop
        # TODO: 統計分析実行
        # TODO: 有意であればバックテストキューへ

    async def _execute_daily_orders(self) -> None:
        """本運用戦略のシグナルに基づき発注する."""
        log.info("daily_order_triggered")
        # TODO: アクティブなTraderからシグナル取得
        # TODO: ポジション調整計算
        # TODO: 証券会社APIで発注

    async def run(self) -> None:
        """エージェントを起動する."""
        log.info("agent_starting", env=settings.env)

        # スケジューラにタスク登録
        self.scheduler.register_source_monitor(self._monitor_sources)
        self.scheduler.register_analysis(self._run_analysis)
        self.scheduler.register_daily_order(self._execute_daily_orders)

        self.scheduler.start()
        self._running = True

        log.info("agent_running", message="Ctrl+C で停止")

        # シグナルハンドリング（Windowsではadd_signal_handler非対応のためtry/except使用）
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._shutdown)
        except NotImplementedError:
            # Windows: add_signal_handler は非対応
            pass

        try:
            while self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            self._shutdown()

    def _shutdown(self) -> None:
        """エージェントを停止する."""
        log.info("agent_shutting_down")
        self._running = False
        self.scheduler.shutdown()


def main() -> None:
    """エントリポイント."""
    agent = InvestmentAgent()
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
