"""決算反応モデル学習データ除外フラグ管理 CLI.

特殊イベント銘柄（子会社統合・M&A・上場廃止手続中・データ欠損等）を
線形モデル学習時に除外するためのレコード管理ツール。
レポート表示上は全件維持し、学習時のフィルタ情報として併記する。

管理単位: ``{ticker, predict_date}`` ペア。
蓄積先: ``gs://stock_data_1930932/earnings_model/earnings_reaction_exclusions/exclusions.json``

Usage:
    # 追加
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/exclusion_manager.py add 3387 20260414 "子会社統合（特殊イベント）"

    # 一覧（全件、または特定 predict_date でフィルタ）
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/exclusion_manager.py list
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/exclusion_manager.py list --predict-date 20260414

    # 削除（論理削除: removed_at を埋める）
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/exclusion_manager.py remove 3387 20260414
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from google.cloud import storage
from google.oauth2 import service_account

log = structlog.get_logger()

GCS_BUCKET = "stock_data_1930932"
GCS_BLOB_PATH = "earnings_model/earnings_reaction_exclusions/exclusions.json"
KEY_PATH = "C:/gdrive/claude/investment-agent/keys/gcp-service-account.json"
JST = timezone(timedelta(hours=9))


def get_gcs_client() -> storage.Client:
    """GCS クライアントを構築する."""
    creds = service_account.Credentials.from_service_account_file(KEY_PATH)
    return storage.Client(credentials=creds, project="and-and-and")


def load_exclusions(client: storage.Client) -> list[dict[str, Any]]:
    """GCS から exclusions.json をロードする（存在しなければ空リスト）."""
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(GCS_BLOB_PATH)
    if not blob.exists():
        log.info("exclusions_not_found", path=f"gs://{GCS_BUCKET}/{GCS_BLOB_PATH}")
        return []
    raw = blob.download_as_text(encoding="utf-8")
    if not raw.strip():
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"exclusions.json must be a JSON array, got {type(data).__name__}")
    return data


def save_exclusions(client: storage.Client, records: list[dict[str, Any]]) -> None:
    """GCS に exclusions.json を保存する."""
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(GCS_BLOB_PATH)
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    blob.upload_from_string(payload, content_type="application/json; charset=utf-8")
    log.info(
        "exclusions_saved",
        path=f"gs://{GCS_BUCKET}/{GCS_BLOB_PATH}",
        count=len(records),
    )


def _find_active(
    records: list[dict[str, Any]], ticker: str, predict_date: str
) -> dict[str, Any] | None:
    """有効な（removed_at=null）レコードを取得する."""
    for r in records:
        if (
            r.get("ticker") == ticker
            and r.get("predict_date") == predict_date
            and r.get("removed_at") is None
        ):
            return r
    return None


def cmd_add(args: argparse.Namespace) -> None:
    """除外レコードを追加する."""
    client = get_gcs_client()
    records = load_exclusions(client)

    existing = _find_active(records, args.ticker, args.predict_date)
    if existing is not None:
        log.warning(
            "already_exists",
            ticker=args.ticker,
            predict_date=args.predict_date,
            reason=existing.get("reason"),
        )
        return

    now = datetime.now(JST).isoformat(timespec="seconds")
    new_record = {
        "ticker": args.ticker,
        "predict_date": args.predict_date,
        "reason": args.reason,
        "added_at": now,
        "removed_at": None,
    }
    records.append(new_record)
    save_exclusions(client, records)
    log.info(
        "added",
        ticker=args.ticker,
        predict_date=args.predict_date,
        reason=args.reason,
    )


def cmd_list(args: argparse.Namespace) -> None:
    """除外レコード一覧を表示する."""
    client = get_gcs_client()
    records = load_exclusions(client)

    if args.predict_date:
        records = [r for r in records if r.get("predict_date") == args.predict_date]

    active = [r for r in records if r.get("removed_at") is None]
    removed = [r for r in records if r.get("removed_at") is not None]

    log.info("summary", total=len(records), active=len(active), removed=len(removed))
    for r in records:
        status = "active" if r.get("removed_at") is None else "removed"
        log.info(
            "record",
            status=status,
            ticker=r.get("ticker"),
            predict_date=r.get("predict_date"),
            reason=r.get("reason"),
            added_at=r.get("added_at"),
            removed_at=r.get("removed_at"),
        )


def get_active_exclusion_set(predict_date: str | None = None) -> set[tuple[str, str]]:
    """学習コード等から import して使う: アクティブな除外の {(ticker, predict_date)} セットを返す.

    Args:
        predict_date: 指定時はその日の除外のみ返す. None時は全 predict_date のアクティブ除外を返す.

    Returns:
        Set of (ticker, predict_date) tuples where removed_at is null.
    """
    client = get_gcs_client()
    records = load_exclusions(client)
    result: set[tuple[str, str]] = set()
    for r in records:
        if r.get("removed_at") is not None:
            continue
        if predict_date is not None and r.get("predict_date") != predict_date:
            continue
        result.add((r.get("ticker", ""), r.get("predict_date", "")))
    return result


def cmd_remove(args: argparse.Namespace) -> None:
    """除外レコードを論理削除する（removed_at を埋める）."""
    client = get_gcs_client()
    records = load_exclusions(client)

    target = _find_active(records, args.ticker, args.predict_date)
    if target is None:
        log.warning(
            "not_found_active",
            ticker=args.ticker,
            predict_date=args.predict_date,
        )
        return

    target["removed_at"] = datetime.now(JST).isoformat(timespec="seconds")
    save_exclusions(client, records)
    log.info("removed", ticker=args.ticker, predict_date=args.predict_date)


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(
        description="決算反応モデル学習データ除外フラグ管理CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="除外レコードを追加")
    p_add.add_argument("ticker", help="対象ticker")
    p_add.add_argument("predict_date", help="予測日 YYYYMMDD")
    p_add.add_argument("reason", help="除外理由")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="除外レコード一覧")
    p_list.add_argument(
        "--predict-date",
        dest="predict_date",
        default=None,
        help="特定 predict_date でフィルタ（YYYYMMDD）",
    )
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove", help="除外レコードを論理削除")
    p_remove.add_argument("ticker", help="対象ticker")
    p_remove.add_argument("predict_date", help="予測日 YYYYMMDD")
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
