"""GCS月次開示パス再配置スクリプト（一回限りの移行用）.

旧パス → 新パスへファイルをコピーする。
コピー後に旧パスの削除は行わない（手動確認後に削除する）。

移行マッピング:
  monthlydata/company_list.json              → monthly/company_list.json
  monthlydata/{ticker}/structure.json        → monthly/meta/{ticker}/structure.json
  monthlydata/{ticker}/ir_url.json           → monthly/meta/{ticker}/ir_url.json
  monthlydata/{ticker}/adapter.json          → monthly/meta/{ticker}/adapter.json
  monthlydata/{ticker}/download_adapter.json → monthly/meta/{ticker}/download_adapter.json
  monthlydata/{ticker}/extract_adapter.json  → monthly/meta/{ticker}/extract_adapter.json
  monthlydata/{ticker}/monthly_records.json  → monthly/record/{ticker}/monthly_records.json
  monthlydata/{ticker}/{yyyy-mm}.json        → monthly/record/{ticker}/{yyyy-mm}.json
  monthlydata/_progress.json                 → monthly/meta/_progress.json
  monthlydata/_download_log/*.json           → monthly/log/*.json
  monthlydata/_run_logs/*.json               → monthly/log/*.json
  monthlyir/{ticker}/*                       → monthly/docs/{ticker}/*
  buffett_compare/*.csv                      → monthly/bcdata/*.csv

Usage:
    PYTHONUTF8=1 uv run python scripts/migrate_gcs_monthly_paths.py --dry-run
    PYTHONUTF8=1 uv run python scripts/migrate_gcs_monthly_paths.py
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

from google.cloud import storage
from google.oauth2 import service_account

GCS_BUCKET = "stock_data_1930932"
GCP_PROJECT = "gmailpj-357912"
KEY_FILE = "keys/gcp-service-account.json"
JST = timezone(timedelta(hours=9))

# メタデータファイル（monthlydata/{ticker}/ 直下）
META_FILES = {
    "structure.json",
    "ir_url.json",
    "adapter.json",
    "download_adapter.json",
    "extract_adapter.json",
}

# レコードファイル（monthlydata/{ticker}/ 直下）
RECORD_FILES = {"monthly_records.json"}

# {yyyy-mm}.json パターン（monthly_data_load.py が生成）
YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}\.json$")


def log(msg: str) -> None:
    """JST タイムスタンプ付きログ出力。"""
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def compute_new_path(old_path: str) -> str | None:
    """旧GCSパスから新GCSパスを算出する。対象外なら None を返す。"""
    parts = old_path.split("/")

    # --- monthlydata/ ---
    if parts[0] == "monthlydata":
        # monthlydata/company_list.json → monthly/company_list.json
        if len(parts) == 2 and parts[1] == "company_list.json":
            return "monthly/company_list.json"

        # monthlydata/_progress.json → monthly/meta/_progress.json
        if len(parts) == 2 and parts[1] == "_progress.json":
            return "monthly/meta/_progress.json"

        # monthlydata/_download_log/*.json → monthly/log/*.json
        if len(parts) == 3 and parts[1] == "_download_log":
            return f"monthly/log/{parts[2]}"

        # monthlydata/_run_logs/*.json → monthly/log/*.json
        if len(parts) == 3 and parts[1] == "_run_logs":
            return f"monthly/log/{parts[2]}"

        # monthlydata/{ticker}/{file} → 分類して振り分け
        if len(parts) == 3:
            ticker = parts[1]
            filename = parts[2]

            if filename in META_FILES:
                return f"monthly/meta/{ticker}/{filename}"
            if filename in RECORD_FILES:
                return f"monthly/record/{ticker}/{filename}"
            if YEAR_MONTH_RE.match(filename):
                return f"monthly/record/{ticker}/{filename}"

        return None

    # --- monthlyir/ ---
    if parts[0] == "monthlyir":
        # monthlyir/{ticker}/* → monthly/docs/{ticker}/*
        if len(parts) >= 3:
            ticker = parts[1]
            rest = "/".join(parts[2:])
            return f"monthly/docs/{ticker}/{rest}"
        return None

    # --- buffett_compare/ ---
    if parts[0] == "buffett_compare":
        if len(parts) == 2:
            return f"monthly/bcdata/{parts[1]}"
        return None

    return None


def migrate(dry_run: bool = True) -> None:
    """GCSファイルを旧パスから新パスへコピーする。"""
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    client = storage.Client(project=GCP_PROJECT, credentials=creds)
    bucket = client.bucket(GCS_BUCKET)

    prefixes = ["monthlydata/", "monthlyir/", "buffett_compare/"]
    total_copied = 0
    total_skipped = 0
    total_unmapped = 0

    for prefix in prefixes:
        log(f"スキャン中: gs://{GCS_BUCKET}/{prefix}")
        blobs = list(client.list_blobs(bucket, prefix=prefix))
        log(f"  {len(blobs)} オブジェクト検出")

        for blob in blobs:
            new_path = compute_new_path(blob.name)
            if new_path is None:
                total_unmapped += 1
                continue

            # コピー先に既に存在するかチェック
            dst_blob = bucket.blob(new_path)
            if dst_blob.exists():
                total_skipped += 1
                continue

            if dry_run:
                print(f"  [DRY] {blob.name} → {new_path}")
            else:
                bucket.copy_blob(blob, bucket, new_path)
                total_copied += 1
                if total_copied % 100 == 0:
                    log(f"  コピー済み: {total_copied}")

    log("=== 完了 ===")
    log(f"  コピー: {total_copied}")
    log(f"  スキップ（既存）: {total_skipped}")
    log(f"  対象外: {total_unmapped}")


def main() -> None:
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="GCS月次開示パス再配置")
    parser.add_argument("--dry-run", action="store_true", help="コピーせずに対象を表示")
    args = parser.parse_args()

    if not args.dry_run:
        log("⚠️  本番モード: GCSファイルをコピーします")
        ans = input("続行しますか？ (y/N): ").strip().lower()
        if ans != "y":
            log("中断しました")
            sys.exit(0)

    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
