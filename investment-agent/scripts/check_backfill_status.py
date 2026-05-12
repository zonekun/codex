"""バックフィル AI_STATUS 集計ユーティリティ。

monitor_backfill.py の完了通知に自動組み込み + スタンドアロン実行の両方に対応。

使用例（スタンドアロン）:
    PYTHONUTF8=1 python scripts/check_backfill_status.py --date-from 20240101 --date-to 20241231
    PYTHONUTF8=1 python scripts/check_backfill_status.py --year 2024
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

PROJECT = "gmailpj-357912"
TABLE = f"{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
KEY_FILE = "keys/gcp-service-account.json"


def _bq_client() -> Any:
    from google.cloud import bigquery

    if Path(KEY_FILE).exists():
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=PROJECT, credentials=creds)
    return bigquery.Client(project=PROJECT)


def get_backfill_status(date_from: str, date_to: str) -> dict[str, int]:
    """BQ から指定日付範囲の AI_STATUS 件数を集計して返す。

    Args:
        date_from: YYYYMMDD 形式の開始日
        date_to: YYYYMMDD 形式の終了日

    Returns:
        {"completed": N, "pending": N, "pending_gemma": N,
         "pending_finalize": N, "total": N}
    """
    client = _bq_client()
    df_fmt = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    dt_fmt = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"
    sql = f"""
        SELECT
            IFNULL(AI_STATUS, 'pending') AS status,
            COUNT(*) AS cnt
        FROM `{TABLE}`
        WHERE SUBMISSION_DATE BETWEEN '{df_fmt}' AND '{dt_fmt}'
        GROUP BY status
        ORDER BY status
    """
    rows = list(client.query(sql).result())
    result: dict[str, int] = {
        "completed": 0,
        "pending": 0,
        "pending_gemma": 0,
        "pending_finalize": 0,
    }
    for row in rows:
        result[row.status] = row.cnt
    result["total"] = sum(result.values())
    return result


def format_status(status: dict[str, int], date_from: str, date_to: str) -> str:
    """集計結果を人間可読な文字列にフォーマットする。"""
    mark = "✅" if status["pending"] == 0 and status["pending_gemma"] == 0 and status["pending_finalize"] == 0 else "⚠️"
    parts = [f"{date_from}-{date_to}: {status['total']}docs"]
    for k in ("completed", "pending", "pending_gemma", "pending_finalize"):
        if status[k] > 0:
            parts.append(f"{k}={status[k]}")
    return f"{mark} {', '.join(parts)}"


def has_pending(status: dict[str, int]) -> bool:
    """未処理ドキュメントがあるか。"""
    return (status["pending"] + status["pending_gemma"] + status["pending_finalize"]) > 0


def main() -> None:
    ap = argparse.ArgumentParser(description="バックフィル AI_STATUS 集計")
    ap.add_argument("--date-from", help="開始日 YYYYMMDD")
    ap.add_argument("--date-to", help="終了日 YYYYMMDD")
    ap.add_argument("--year", type=int, help="年指定（Q1-Q4 を自動分割）")
    args = ap.parse_args()

    if args.year:
        y = args.year
        quarters = [
            ("Q1", f"{y}0101", f"{y}0331"),
            ("Q2", f"{y}0401", f"{y}0630"),
            ("Q3", f"{y}0701", f"{y}0930"),
            ("Q4", f"{y}1001", f"{y}1231"),
        ]
        print(f"=== {y} Backfill Status ===")
        for label, df, dt in quarters:
            st = get_backfill_status(df, dt)
            print(f"  {label}: {format_status(st, df, dt)}")
    elif args.date_from and args.date_to:
        st = get_backfill_status(args.date_from, args.date_to)
        print(format_status(st, args.date_from, args.date_to))
    else:
        ap.error("--year または --date-from/--date-to を指定してください")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
