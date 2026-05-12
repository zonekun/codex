"""データカタログ管理モジュール.

data_catalog.md を読み書きし、全ストレージのデータ定義・所在を管理する。
データの種類を追加するたびにカタログを更新する。
"""
from __future__ import annotations

from pathlib import Path

from src.core.logger import get_logger

log = get_logger(__name__)

CATALOG_PATH = Path("data_catalog.md")


def load_catalog() -> str:
    """データカタログを読み込む."""
    if CATALOG_PATH.exists():
        return CATALOG_PATH.read_text(encoding="utf-8")
    log.warn("data_catalog_not_found")
    return ""


def append_entry(
    storage_type: str,
    location: str,
    description: str,
    schema_note: str = "",
) -> None:
    """データカタログにエントリを追加する.

    .. deprecated::
        data_catalog.md がインデックス化されたため、本関数でファイル末尾に
        追記するとサマリテーブル外に行が漏れる。使用禁止。
        新規テーブル追加は data_catalog.md サマリテーブルに1行 +
        docs/data_catalog/bq_xxx.md を手動作成すること。

    Args:
        storage_type: "bigquery" | "gcs" | "local_csv" | "api_cache"
        location: テーブル名, GCSパス, ローカルパス等
        description: データの説明
        schema_note: スキーマに関する備考
    """
    entry = f"\n| {storage_type} | `{location}` | {description} | {schema_note} |\n"
    with open(CATALOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
    log.info("catalog_entry_added", storage=storage_type, location=location)
