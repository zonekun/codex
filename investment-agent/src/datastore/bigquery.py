"""Google BigQuery アクセス層.

株価日次データ、銘柄マスタ等の構造化データを格納。
収集モジュールは別途実行される前提。エージェントはクエリのみ行う。
"""
from __future__ import annotations

import pandas as pd

from src.core.config import app_config, settings
from src.core.logger import get_logger

log = get_logger(__name__)


class BigQueryClient:
    """BigQuery読み取りクライアント."""

    def __init__(self) -> None:
        cfg = app_config.get("datastore", {})
        self.project_id = cfg.get("gcp_project_id", settings.gcp_project_id)
        self.dataset = cfg.get("bigquery_dataset", "STOCK")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import bigquery
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                settings.google_application_credentials
            )
            self._client = bigquery.Client(project=self.project_id, credentials=creds)
        return self._client

    def query(self, sql: str) -> pd.DataFrame:
        """SQLを実行してDataFrameで返す."""
        log.info("bq_query", sql=sql[:100])
        client = self._get_client()
        return client.query(sql).to_dataframe()

    def read_table(self, table_name: str) -> pd.DataFrame:
        """テーブル全体を読み込む."""
        sql = f"SELECT * FROM `{self.project_id}.{self.dataset}.{table_name}`"
        return self.query(sql)
