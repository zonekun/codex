#!/usr/bin/env python3
"""8914 エリアリンク extract_adapter 再調整 v2.

ユーザー指示: overwrite_past_months を切る + 最新以外は履歴行を避ける regex 調整.

8914 の TDnet PDF は以下の構造:
  Table A (履歴): 稼働率(%) 2021.6 2021.12 2022.6 ... の半期時系列行
  Table B (直近): 稼働率(%) 41.59 42.87 44.15 の直近月行

Jan PDF (20260205) は Table A のみ、Feb/Mar PDF は両テーブルを含む.
現状 row_label_regex = `稼働率(%)\s+([\d\.]+)...` で Table A の行先頭 2021.6 を
誤マッチ (lookbehind 追加で 稼働率 field は回避、しかし 新規稼働率 は prefix なし → 直撃).

修正方針:
  1. overwrite_past_months: false を明示
  2. 各 稼働率 field に `(?!\s+20\d{2}\.)` negative lookahead を挿入.
     「ラベル直後が 20XX. (年.月形式) なら拒否」→ 履歴行をスキップし直近行にマッチ.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/8914.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> int:
    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adapter = json.load(f)

    # overwrite_past_months: 明示 false (各 PDF ごとに extract → 最新 PDF 以外は履歴値の上書きしない)
    adapter["overwrite_past_months"] = False

    # fields の regex 調整: 履歴行 (20XX.N 形式) を除外する negative lookahead を追加
    for f in adapter.get("fields", []):
        key = f.get("key", "")
        if key not in {"稼働率(%)", "既存稼働率(%)", "新規稼働率(%)"}:
            continue
        rlr = f.get("row_label_regex", "")
        # ラベル直後に (?!\s+20\d{2}\.) を挿入
        # 例: "稼働率\\(%\\)\\s+([\\d\\.]+)..." → "稼働率\\(%\\)(?!\\s+20\\d{2}\\.)\\s+([\\d\\.]+)..."
        if "(?!\\s+20\\d{2}\\.)" in rlr:
            continue  # 既に適用済み
        # ラベル部分と captureグループ部分の境界を探して挿入
        marker = r"\s+([\d\.]+)"
        if marker in rlr:
            rlr_new = rlr.replace(marker, r"(?!\s+20\d{2}\.)" + marker, 1)
            f["row_label_regex"] = rlr_new
            logger.info(f"  [{key}] regex 更新:")
            logger.info(f"    旧: {rlr}")
            logger.info(f"    新: {rlr_new}")

    adapter["regex_redesign_at"] = datetime.now(JST).isoformat()
    adapter["regex_redesign_note"] = (
        "v2: overwrite_past_months=false 明示 + 履歴行 (20XX.N 形式) を negative lookahead で除外."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ adapter 更新: {ADAPTER_PATH}")

    from google.cloud import storage  # noqa: E402
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    # GCS sync
    blob = bucket.blob("monthly/meta/8914/extract_adapter.json")
    blob.upload_from_filename(str(ADAPTER_PATH))
    logger.info("✅ GCS sync 完了")

    # 既存 records を削除 (履歴値の残留を防ぐ)
    logger.info("--- 既存 records 削除 ---")
    rec_blob = bucket.blob("monthly/record/8914/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("  削除完了")
    else:
        logger.info("  既存なし")

    logger.info("--- extract 実行 ---")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "8914", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
    )
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        logger.error("extract 失敗")
        return 1

    # records 確認
    rec_blob = bucket.blob("monthly/record/8914/monthly_records.json")
    records = json.loads(rec_blob.download_as_text())
    logger.info("--- records ---")
    for r_ in records:
        logger.info(f"  {r_['year_month']}: {r_['fields']}")

    logger.info("--- compare 実行 ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py",
         "--tickers", "8914"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
    )
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
