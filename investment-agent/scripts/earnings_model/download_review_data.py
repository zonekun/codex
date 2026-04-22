"""決算答え合わせ反省会用: prediction + actual を一括DLしてローカル保存.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/download_review_data.py 20260410

    # 複数日まとめて
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/download_review_data.py 20260409 20260410

DL先: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json
反省会中は Claude がローカルファイルを読むだけで GCS アクセス不要になる。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog
from google.cloud import storage
from google.oauth2 import service_account

log = structlog.get_logger()

GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "earnings_model"
LOCAL_DIR = Path("C:/tmp/earnings_review")
KEY_PATH = Path("C:/gdrive/claude/investment-agent/keys/gcp-service-account.json")


def get_gcs_client() -> storage.Client:
    """GCS クライアントを構築する."""
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return storage.Client(credentials=creds, project="and-and-and")


def download_prediction(
    client: storage.Client, predict_date: str, local_name: str
) -> Path | None:
    """予測ファイルをDLする（命名規約: prediction_{PREDICT_DATE}_{HHMMSS}.json）."""
    bucket = client.bucket(GCS_BUCKET)
    search = f"{GCS_PREFIX}/predictions/prediction_{predict_date}"
    blobs = sorted(bucket.list_blobs(prefix=search), key=lambda b: b.name)
    if not blobs:
        log.warning("not_found", prefix=search)
        return None
    blob = blobs[-1]
    local_path = LOCAL_DIR / local_name
    blob.download_to_filename(str(local_path))
    log.info("downloaded", gcs=blob.name, local=str(local_path))
    return local_path


def download_actual(
    client: storage.Client, predict_date: str, local_name: str
) -> Path | None:
    """実績ファイルをDLする.

    新命名規約（2026-04-15〜）: actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json
    旧命名規約（〜2026-04-15）: actual_{PREDICT_DATE}_{HHMMSS}.json

    両方を探索し、作成時刻最新のファイルを採用する.
    """
    bucket = client.bucket(GCS_BUCKET)
    all_blobs = list(bucket.list_blobs(prefix=f"{GCS_PREFIX}/actuals/actual_"))

    suffix_new = f"_for_{predict_date}.json"
    prefix_old = f"{GCS_PREFIX}/actuals/actual_{predict_date}_"

    candidates = [
        b for b in all_blobs
        if b.name.endswith(suffix_new) or b.name.startswith(prefix_old)
    ]
    if not candidates:
        log.warning("not_found", predict_date=predict_date)
        return None

    blob = max(candidates, key=lambda b: b.time_created)
    local_path = LOCAL_DIR / local_name
    blob.download_to_filename(str(local_path))
    log.info("downloaded", gcs=blob.name, local=str(local_path))
    return local_path


def download_for_date(client: storage.Client, predict_date: str) -> None:
    """指定日の prediction + actual をDLする."""
    log.info("processing", date=predict_date)
    download_prediction(client, predict_date, f"prediction_{predict_date}.json")
    download_actual(client, predict_date, f"actual_{predict_date}.json")


def show_summary(predict_date: str) -> None:
    """DL済みデータのサマリーを表示する.

    Args:
        predict_date: 対象日 (YYYYMMDD).
    """
    actual_path = LOCAL_DIR / f"actual_{predict_date}.json"
    pred_path = LOCAL_DIR / f"prediction_{predict_date}.json"

    if not actual_path.exists():
        log.info("no_actual", date=predict_date)
        return

    with open(actual_path, encoding="utf-8") as f:
        actual = json.load(f)
    with open(pred_path, encoding="utf-8") as f:
        pred = json.load(f)

    count = actual.get("count", 0)
    acc = actual.get("direction_accuracy", 0)
    corr = actual.get("score_return_correlation", 0)
    pred_count = len(pred.get("predictions", []))

    log.info(
        "summary",
        date=predict_date,
        predictions=pred_count,
        actuals=count,
        direction_accuracy=f"{acc:.1%}",
        correlation=f"{corr:.3f}",
    )


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(description="決算反省会データ一括DL")
    parser.add_argument("dates", nargs="+", help="対象日 YYYYMMDD（複数可）")
    args = parser.parse_args()

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    client = get_gcs_client()

    for d in args.dates:
        download_for_date(client, d)
        show_summary(d)

    log.info("done", local_dir=str(LOCAL_DIR), dates=args.dates)


if __name__ == "__main__":
    main()
