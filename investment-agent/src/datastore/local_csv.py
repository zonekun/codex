"""ローカルCSV アクセス層.

BQ/GCSのデータの一部をコピーして手元で使う。
分析時のメインデータソース。PBR等の頻用指標は事前クレンジング済みで保持。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)


class LocalCSVStore:
    """ローカルCSVファイルの読み書き."""

    def __init__(self) -> None:
        cfg = app_config.get("datastore", {})
        self.base_dir = Path(cfg.get("local_csv_dir", "data/csv"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def read(self, filename: str, **kwargs) -> pd.DataFrame:
        """CSVファイルを読み込む."""
        path = self.base_dir / filename
        log.info("csv_read", path=str(path))
        return pd.read_csv(path, **kwargs)

    def write(self, df: pd.DataFrame, filename: str, **kwargs) -> Path:
        """DataFrameをCSVとして保存する."""
        path = self.base_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, **kwargs)
        log.info("csv_write", path=str(path), rows=len(df))
        return path

    def list_files(self, pattern: str = "*.csv") -> list[Path]:
        """CSVファイル一覧を返す."""
        return sorted(self.base_dir.rglob(pattern))
