"""データ自動探索モジュール（Data Discovery）.

投資アイディアの分析に必要だが、データストア(a)〜(d)に存在しないデータを
自律的にWeb/API等から探索・取得する。

動作フロー:
    1. data_catalog.md を参照し、必要データがどの層にあるか確認
    2. 見つからない場合、以下の手段で探索:
       - Web検索で公開データソースを探す
       - 利用可能なAPIを調査
       - Webスクレイピングでデータを取得
       - CSV/Excel/PDFファイルをダウンロード
    3. 取得したデータをローカルに保存し、data_catalog.md に登録

実例:
    - 逆日歩データ → 日証金サイトからスクレイピング
    - 大量保有報告書 → EDINET APIから取得
    - 分足データ → J-Quants API等から取得
    - VWAP → 分足データから計算
    - 景気動向指数 → e-STAT APIから取得
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.config import app_config
from src.core.logger import get_logger
from src.datastore.catalog import load_catalog, append_entry

log = get_logger(__name__)


class DataDiscovery:
    """データ自動探索エンジン."""

    def __init__(self) -> None:
        cfg = app_config.get("datastore", {})
        self.csv_dir = Path(cfg.get("local_csv_dir", "data/csv"))
        self.cache_dir = Path(cfg.get("cache_dir", "data/cache"))

    def check_availability(self, data_description: str) -> dict:
        """必要なデータがデータストアに存在するか確認する.

        Args:
            data_description: 必要なデータの説明
                例: "逆日歩（品貸料）の銘柄別ヒストリカルデータ"

        Returns:
            {"found": bool, "location": str | None, "layer": str | None}
        """
        catalog = load_catalog()
        log.info("discovery_check", description=data_description)
        # TODO: LLMにcatalog内容とdata_descriptionを渡して
        #       該当データの有無と所在を判定させる
        raise NotImplementedError

    async def discover_and_fetch(
        self,
        data_description: str,
        save_filename: str | None = None,
    ) -> Path | None:
        """データを探索し、取得してローカルに保存する.

        Args:
            data_description: 必要なデータの説明
            save_filename: 保存先ファイル名（省略時は自動命名）

        Returns:
            保存先パス。取得失敗時はNone。

        探索戦略（順に試行）:
            1. 既知のAPI（J-Quants, yfinance, e-STAT, EDINET）で取得可能か
            2. Webで公開データソースを検索
            3. 該当サイトからスクレイピング or ダウンロード
        """
        log.info("discovery_start", description=data_description)

        # Step 1: 既知APIで取得できるか判定
        # TODO: data_descriptionをLLMで解析し、適切なAPIクライアントを選択

        # Step 2: Web検索で公開ソースを探索
        # TODO: Web検索API等で公開データソースを特定

        # Step 3: データ取得（API/スクレイピング/ダウンロード）
        # TODO: 特定したソースからデータを取得

        # Step 4: ローカルに保存
        # TODO: data/csv/ または data/cache/ に保存

        # Step 5: data_catalog.md にエントリ追加
        # TODO: append_entry() でカタログ登録

        raise NotImplementedError

    def record_discovery_failure(
        self,
        data_description: str,
        attempted_sources: list[str],
        reason: str,
    ) -> None:
        """探索失敗を知見ファイルに記録する.

        何を探し、何を試み、なぜダメだったかを記録して
        将来の探索の参考にする。

        Args:
            data_description: 探索対象のデータ
            attempted_sources: 試みたソースのリスト
            reason: 失敗理由
        """
        log.warn(
            "discovery_failed",
            description=data_description,
            sources=attempted_sources,
            reason=reason,
        )
        # TODO: knowledge/analysis/ に失敗記録をmdファイルで保存
        raise NotImplementedError
