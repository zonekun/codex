"""
GCS の extract_adapter.json / structure.json をローカルに同期し git 管理する。

Usage:
    # GCS → ローカル（pull）
    PYTHONUTF8=1 uv run python scripts/sync_adapters.py pull

    # ローカル → GCS（push）※手動修正後にアップロード
    PYTHONUTF8=1 uv run python scripts/sync_adapters.py push

    # 特定ティッカーのみ
    PYTHONUTF8=1 uv run python scripts/sync_adapters.py pull --tickers 2750 3077 9936
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

GCP_PROJECT = "gmailpj-357912"
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
KEY_FILE = Path("keys/gcp-service-account.json")
LOCAL_DIR = Path("data/adapters")
JST = timezone(timedelta(hours=9))

# 同期対象ファイル
SYNC_FILES = ["extract_adapter.json", "structure.json"]


def get_gcs() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
    return storage.Client(project=GCP_PROJECT, credentials=creds)


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def pull(gcs: storage.Client, tickers: list[str] | None = None) -> None:
    """GCS → ローカル。"""
    bucket = gcs.bucket(GCS_BUCKET)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    # ティッカー一覧を取得
    if tickers:
        target_tickers = tickers
    else:
        target_tickers = set()
        for blob in bucket.list_blobs(prefix=f"{GCS_META}/"):
            if blob.name.endswith("/extract_adapter.json"):
                ticker = blob.name.split("/")[2]
                target_tickers.add(ticker)
        target_tickers = sorted(target_tickers)

    log(f"pull: {len(target_tickers)} 銘柄")
    count = 0
    for ticker in target_tickers:
        ticker_dir = LOCAL_DIR / ticker
        ticker_dir.mkdir(exist_ok=True)
        for fname in SYNC_FILES:
            blob = bucket.blob(f"{GCS_META}/{ticker}/{fname}")
            local_path = ticker_dir / fname
            try:
                data = blob.download_as_text()
                # JSON を整形して保存（git diff が見やすいように）
                parsed = json.loads(data)
                local_path.write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                count += 1
            except Exception:
                pass  # ファイルが存在しない場合はスキップ

    log(f"✅ {count} ファイル → {LOCAL_DIR}")


def push(gcs: storage.Client, tickers: list[str] | None = None) -> None:
    """ローカル → GCS。"""
    bucket = gcs.bucket(GCS_BUCKET)

    if tickers:
        ticker_dirs = [LOCAL_DIR / t for t in tickers if (LOCAL_DIR / t).exists()]
    else:
        ticker_dirs = sorted(d for d in LOCAL_DIR.iterdir() if d.is_dir())

    log(f"push: {len(ticker_dirs)} 銘柄")
    count = 0
    for ticker_dir in ticker_dirs:
        ticker = ticker_dir.name
        for fname in SYNC_FILES:
            local_path = ticker_dir / fname
            if not local_path.exists():
                continue
            data = local_path.read_text(encoding="utf-8")
            blob = bucket.blob(f"{GCS_META}/{ticker}/{fname}")
            blob.upload_from_string(data, content_type="application/json")
            count += 1

    log(f"✅ {count} ファイル → GCS")


def main() -> None:
    parser = argparse.ArgumentParser(description="GCS アダプター同期")
    parser.add_argument("action", choices=["pull", "push"], help="pull: GCS→ローカル, push: ローカル→GCS")
    parser.add_argument("--tickers", nargs="+", default=None, help="対象ティッカー（省略時は全銘柄）")
    args = parser.parse_args()

    gcs = get_gcs()
    if args.action == "pull":
        pull(gcs, args.tickers)
    elif args.action == "push":
        push(gcs, args.tickers)


if __name__ == "__main__":
    main()
