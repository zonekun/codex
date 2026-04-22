#!/usr/bin/env python3
"""8218 コメリ year_month 誤抽出バグ修正.

問題:
  - 現 adapter: year_from_title_regex=r'^(\\d{4})\\d{2}_' で提出年 YYYY を採用.
  - cross-year (202601_12月度) で year=2026 × month=12 → 2026-12 (誤. 正 2025-12).
  - 最新.pdf は月不明で submission_date 不良.

ユーザー指示:
  A) year_from_title_regex を無効化
  B) year_month として「submission_date - 1ヶ月」を採用
  C) PDF 右上に提出日があるので併せて確認

Phase 1: PDF 構造調査 (upper right 提出日 dump)
Phase 2: 8218 adapter 修正 + extract_monthly_data.py に
         `year_month_from_submission_minus_1: true` フラグサポート追加
Phase 3: records 削除 → 再抽出 → compare
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = ROOT / "data/monthly_adapters/8218.json"
EXTRACT_PY = ROOT / "scripts/extract_monthly_data.py"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def phase1_investigate(bucket) -> None:
    """Phase 1: サンプル PDF 構造を調査 (upper right 提出日の形式確認)."""
    logger.info("=== Phase 1: PDF 構造調査 ===")
    import pdfplumber

    samples = [
        "monthly/docs/8218/202505_8218_4月度の月次情報詳細はこちら[PDF_116KB]_bf70f864.pdf",
        "monthly/docs/8218/202601_8218_12月度の月次情報詳細はこちら[PDF_85KB]_fdb1c011.pdf",
        "monthly/docs/8218/202602_8218_1月度の月次情報詳細はこちら[PDF_116KB]_bdd83aa1.pdf",
        "monthly/docs/8218/202603_8218_最新の月次情報詳細はこちら[PDF_116KB]_ae2dae62.pdf",
        "monthly/docs/8218/202604_8218_最新の月次情報詳細はこちら[PDF_118KB]_7773a4d1.pdf",
    ]
    for s in samples:
        blob = bucket.blob(s)
        if not blob.exists():
            logger.warning(f"  {s}: 存在せず")
            continue
        try:
            pdf_bytes = blob.download_as_bytes()
        except Exception as e:
            logger.warning(f"  {s}: DL失敗 {e}")
            continue
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                page = pdf.pages[0]
                text = page.extract_text() or ""
                # 先頭 400文字 dump
                logger.info(f"\n--- {Path(s).name} (size={len(pdf_bytes)}) ---")
                logger.info(f"text head (400 chars):")
                logger.info(f"  {text[:400]}")
                # 日付らしきパターンを全検索
                date_patterns = [
                    r"(\d{4})年(\d{1,2})月(\d{1,2})日",
                    r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
                    r"(\d{4})年(\d{1,2})月",
                    r"令和\d+年\d+月\d+日",
                    r"令和\d+年\d+月",
                ]
                logger.info(f"  日付候補 (matches):")
                for p in date_patterns:
                    for m in re.finditer(p, text[:1500]):
                        ctx_start = max(0, m.start() - 20)
                        ctx_end = min(len(text), m.end() + 20)
                        logger.info(f"    [{p}] {text[ctx_start:ctx_end]!r}")
                        break  # 1件のみ per pattern
        except Exception as e:
            logger.warning(f"  parse 失敗: {e}")


def phase2_patch_extract_py() -> None:
    """Phase 2a: extract_monthly_data.py に year_month_from_submission_minus_1 サポート追加."""
    logger.info("=== Phase 2a: extract_monthly_data.py パッチ ===")
    content = EXTRACT_PY.read_text(encoding="utf-8")

    # 既にパッチ済みか確認
    if "year_month_from_submission_minus_1" in content:
        logger.info("  既にパッチ適用済み")
        return

    # _parse_year_month の先頭に submission_date - 1 month ショートカット追加
    # 挿入位置: `def _parse_year_month(...)` 定義直後の docstring の次
    marker = '''def _parse_year_month(adapter: dict, doc_title: str, submission_date: str) -> Optional[tuple[int, int]]:
    """アダプターのregexでタイトルから年月を抽出。失敗したら提出日から推定。

    解決順序:
      1. adapter の year_from_title_regex（アダプター指定）
      2. 元号→西暦変換（令和/平成/昭和）
      3. 直接西暦年「YYYY年」（決算期形式でない場合のみ）
      4. 提出日ヒューリスティック（day≤15 → 前月）
    """'''

    replacement = '''def _parse_year_month(adapter: dict, doc_title: str, submission_date: str) -> Optional[tuple[int, int]]:
    """アダプターのregexでタイトルから年月を抽出。失敗したら提出日から推定。

    解決順序:
      0. adapter.year_month_from_submission_minus_1=True → submission_date - 1 月を返す
      1. adapter の year_from_title_regex（アダプター指定）
      2. 元号→西暦変換（令和/平成/昭和）
      3. 直接西暦年「YYYY年」（決算期形式でない場合のみ）
      4. 提出日ヒューリスティック（day≤15 → 前月）
    """
    # Step 0: submission_date - 1 month ショートカット
    # (8218 コメリ等、filename YYYYMM プレフィックスが提出年月で、報告月はその前月の銘柄用)
    if adapter.get("year_month_from_submission_minus_1") and submission_date:
        try:
            dt = pd.to_datetime(submission_date)
            if not pd.isna(dt):
                y, m = int(dt.year), int(dt.month)
                if m == 1:
                    return y - 1, 12
                return y, m - 1
        except Exception:
            pass'''

    if marker not in content:
        logger.error("  パッチ挿入位置が見つからない")
        raise RuntimeError("marker not found")
    new_content = content.replace(marker, replacement, 1)
    EXTRACT_PY.write_text(new_content, encoding="utf-8")
    logger.info("  ✅ パッチ適用")


def phase2_patch_adapter() -> None:
    """Phase 2b: 8218 adapter 更新."""
    logger.info("=== Phase 2b: 8218 adapter 更新 ===")
    with ADAPTER_PATH.open(encoding="utf-8") as f:
        adapter = json.load(f)

    adapter["year_month_from_submission_minus_1"] = True
    # year_from_title_regex / month_from_title_regex を無効化 (誤抽出の原因)
    adapter.pop("year_from_title_regex", None)
    adapter.pop("month_from_title_regex", None)
    adapter["regex_redesign_at"] = datetime.now(JST).isoformat()
    adapter["regex_redesign_note"] = (
        "year_from_title_regex / month_from_title_regex を廃止. "
        "ファイル名 YYYYMM プレフィックスから submission_date を derive し、"
        "year_month_from_submission_minus_1=true で前月を year_month として採用."
    )

    with ADAPTER_PATH.open("w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    logger.info(f"  ✅ {ADAPTER_PATH} 更新")


def phase3_rerun(bucket) -> int:
    """Phase 3: GCS 同期 + 既存 records 削除 + 再抽出 + compare."""
    logger.info("=== Phase 3: 再抽出 ===")

    # GCS sync
    bucket.blob("monthly/meta/8218/extract_adapter.json").upload_from_filename(str(ADAPTER_PATH))
    logger.info("  ✅ adapter GCS sync")

    # records 削除
    rec_blob = bucket.blob("monthly/record/8218/monthly_records.json")
    if rec_blob.exists():
        rec_blob.delete()
        logger.info("  ✅ 既存 records 削除")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [sys.executable, "scripts/extract_monthly_data.py",
         "--tickers", "8218", "--since", "2024"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=900,
    )
    sys.stdout.write(r.stdout[-3000:])
    sys.stderr.write(r.stderr[-1500:])

    if rec_blob.exists():
        data = json.loads(rec_blob.download_as_text())
        records = data.get("records") if isinstance(data, dict) else data
        logger.info(f"\n--- records ({len(records)} 件) ---")
        for rec in records:
            logger.info(f"  {rec.get('year_month')}: {rec.get('fields', {})}")

    logger.info("\n--- compare ---")
    r = subprocess.run(
        [sys.executable, "scripts/compare_monthly_buffett.py", "--tickers", "8218"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
        timeout=300,
    )
    sys.stdout.write(r.stdout[-3000:])
    return 0


def main() -> int:
    from google.cloud import storage
    client = storage.Client(project="gmailpj-357912")
    bucket = client.bucket("stock_data_1930932")

    phase1_investigate(bucket)
    phase2_patch_extract_py()
    phase2_patch_adapter()
    return phase3_rerun(bucket)


if __name__ == "__main__":
    sys.exit(main())
