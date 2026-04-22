#!/usr/bin/env python3
"""3546 アレンザHD 手動再設計 + 会計年度履歴 BQ マスタ + 時点ベース year 補正実装.

1. BQ `STOCK.fin_summary` から ticker × (fy_start_date, fy_end_date) 履歴を CSV 化
2. extract_monthly_data.py の _parse_year_month に時点ベース fy_end_month lookup + year 補正を追加
3. 3546 adapter を regex ベースで手動再設計（13 列、月列固定）
4. 再 extract + compare 検証
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
from google.cloud import bigquery, storage  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%H:%M:%S}] {msg}", flush=True)


# =============================================================
# Step A: BQ から ticker × 会計年度履歴を CSV に出力
# =============================================================
def build_fiscal_year_history() -> Path:
    log("=== Step A: BQ fin_summary から会計年度履歴取得 ===")
    bq = bigquery.Client(project="gmailpj-357912")
    sql = """
    SELECT DISTINCT
      LOCAL_CODE AS ticker,
      CURRENT_FISCAL_YEAR_START_DATE AS fy_start_date,
      CURRENT_FISCAL_YEAR_END_DATE   AS fy_end_date
    FROM `gmailpj-357912.STOCK.fin_summary`
    WHERE CURRENT_FISCAL_YEAR_START_DATE IS NOT NULL
      AND CURRENT_FISCAL_YEAR_END_DATE IS NOT NULL
    ORDER BY ticker, fy_start_date
    """
    try:
        df = bq.query(sql).to_dataframe()
        log(f"  BQ result rows: {len(df)}")
    except Exception as e:
        # LOCAL_CODE カラムが違うかも → TICKER で再試行
        sql2 = sql.replace("LOCAL_CODE", "TICKER")
        try:
            df = bq.query(sql2).to_dataframe()
            log(f"  BQ result rows (TICKER 版): {len(df)}")
        except Exception as e2:
            log(f"  BQ query 失敗: {e} / {e2}")
            raise

    out_path = Path("data/master/ticker_fiscal_year_history.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    log(f"  出力: {out_path} ({len(df)} 行)")
    return out_path


# =============================================================
# Step B: extract_monthly_data.py の _parse_year_month に
#         時点ベース fy_end_month lookup + year 補正追加
# =============================================================
def patch_extract_monthly_data() -> None:
    log("=== Step B: extract_monthly_data.py 改修 ===")
    path = Path("scripts/extract_monthly_data.py")
    text = path.read_text(encoding="utf-8")

    if "_FY_HISTORY_CACHE" in text:
        log("  既にパッチ適用済 → スキップ")
        return

    # _parse_year_month の最初に year 補正ロジックを挟む
    # adapter に 'use_fy_history_correction': True がある場合のみ動作
    patch = '''
# ==============================================================
# 会計年度履歴 lookup（時点ベース fy_end_month 取得）
# ==============================================================
_FY_HISTORY_CACHE: Optional[dict[str, list[dict]]] = None


def _load_fy_history() -> dict[str, list[dict]]:
    """data/master/ticker_fiscal_year_history.csv を読み込む (singleton)."""
    global _FY_HISTORY_CACHE
    if _FY_HISTORY_CACHE is not None:
        return _FY_HISTORY_CACHE
    import csv as _csv
    p = Path("data/master/ticker_fiscal_year_history.csv")
    result: dict[str, list[dict]] = {}
    if not p.exists():
        _FY_HISTORY_CACHE = result
        return result
    with open(p, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            t = row.get("ticker", "").strip()
            try:
                fss = row.get("fy_start_date", "").strip()
                fse = row.get("fy_end_date", "").strip()
                if not fss or not fse:
                    continue
                from datetime import date as _date
                start = _date.fromisoformat(fss[:10])
                end = _date.fromisoformat(fse[:10])
            except Exception:
                continue
            result.setdefault(t, []).append({"start": start, "end": end})
    # 各 ticker のエントリを start でソート
    for t in result:
        result[t].sort(key=lambda r: r["start"])
    _FY_HISTORY_CACHE = result
    return result


def _lookup_fy_end_month(ticker: str, submission_date: str) -> Optional[int]:
    """ticker + submission_date → その時点での会計年度終了月を返す."""
    history = _load_fy_history()
    entries = history.get(ticker, [])
    if not entries:
        return None
    try:
        from datetime import date as _date
        sub = _date.fromisoformat(str(submission_date)[:10])
    except Exception:
        return None
    # 範囲内マッチ
    for e in entries:
        if e["start"] <= sub <= e["end"]:
            return e["end"].month
    # 過去のうち最新
    past = [e for e in entries if e["end"] < sub]
    if past:
        return max(past, key=lambda x: x["end"])["end"].month
    # 未来のうち最古（submission が履歴前）
    future = [e for e in entries if e["start"] > sub]
    if future:
        return min(future, key=lambda x: x["start"])["end"].month
    return None


'''
    # _parse_year_month 定義の直前に挿入
    marker = "def _parse_year_month(adapter: dict, doc_title: str, submission_date: str) -> Optional[tuple[int, int]]:"
    if marker not in text:
        log("  marker not found → 中断")
        raise RuntimeError("cannot find _parse_year_month marker")
    text = text.replace(marker, patch + marker, 1)

    # _parse_year_month 内の補正ロジック挿入（return year, month の直前に分岐）
    # adapter.use_fy_history_correction=True + ticker 判定 → year 補正
    old_return = """            if _is_default_re and re.search(r"\\d{4}年\\d{1,2}月期", doc_title):
                pass  # fall through → 提出日ヒューリスティック
            else:
                return year, month"""

    new_return = """            if _is_default_re and re.search(r"\\d{4}年\\d{1,2}月期", doc_title):
                pass  # fall through → 提出日ヒューリスティック
            else:
                # 会計年度補正: adapter.use_fy_history_correction=True かつ
                # BQ マスタに ticker 情報があれば、submission_date 時点の
                # fy_end_month で year を補正する。
                # doc_title 中「YYYY年M月期」の YYYY は fy_end_year (決算期終了年)
                # target_month > fy_end_month なら実 year = fy_end_year - 1
                if adapter.get("use_fy_history_correction"):
                    _ticker = str(adapter.get("ticker", "")).strip()
                    _fy_end_month = _lookup_fy_end_month(_ticker, submission_date)
                    if _fy_end_month and month > _fy_end_month:
                        return year - 1, month
                return year, month"""

    if old_return in text:
        text = text.replace(old_return, new_return, 1)
    else:
        log(f"  ⚠️ _parse_year_month 内 old_return パターンが見つからない")

    path.write_text(text, encoding="utf-8")
    log("  extract_monthly_data.py 改修完了")


# =============================================================
# Step C: 3546 adapter を regex で手動再設計
# =============================================================
def write_3546_adapter() -> None:
    log("=== Step C: 3546 adapter 再設計 ===")
    adapter = {
        "source": "tdnet",
        "format": "text",
        "extraction_method": "regex",
        "fiscal_year_start_month": 3,
        "doc_title_pattern": "月次売上状況について",
        "year_from_title_regex": r"(\d{4})年\d{1,2}月期",
        "month_from_title_regex": r"（(\d{1,2})月度）",
        "use_fy_history_correction": True,   # ← 新フラグ
        "column_map": {
            "3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6,
            "9": 7, "10": 8, "11": 9, "12": 10, "1": 11, "2": 12,
        },
        "fields": [
            {
                "key": "全店 売上（前年同月比）",
                "bc_key": "全店 売上（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"全店\s*売上高((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
            {
                "key": "全店 客数（前年同月比）",
                "bc_key": "全店 客数（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"全店\s*客数((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
            {
                "key": "全店 客単価（前年同月比）",
                "bc_key": "全店 客単価（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"全店\s*客単価((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
            {
                "key": "既存店 売上（前年同月比）",
                "bc_key": "既存店 売上（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"既存店\s*売上高((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
            {
                "key": "既存店 客数（前年同月比）",
                "bc_key": "既存店 客数（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"既存店\s*客数((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
            {
                "key": "既存店 客単価（前年同月比）",
                "bc_key": "既存店 客単価（前年同月比）",
                "value_type": "percentage",
                "row_label_regex": r"既存店\s*客単価((?:\s+[\d\.]+){1,13})",
                "group": "{col_idx}",
                "match_occurrence": 1,
            },
        ],
        "ticker": "3546",
        "company_name": "3546",
        "manual_override": True,
        "regex_redesign_at": datetime.now(JST).isoformat(),
        "regex_redesign_note": "gemini + year_from_title_regex が fy_end_year を拾うバグ → BQ fin_summary 時点ベース補正 + regex 再設計。",
    }
    path = Path("data/monthly_adapters/3546.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(adapter, f, ensure_ascii=False, indent=2)
    log(f"  local saved: {path}")
    gcs = storage.Client(project="stock-data-1930932")
    gcs.bucket("stock_data_1930932").blob("monthly/meta/3546/extract_adapter.json").upload_from_filename(
        str(path), content_type="application/json",
    )
    log("  GCS synced")


# =============================================================
# Step D: 再 extract + compare
# =============================================================
def reextract_and_verify() -> None:
    log("=== Step D: 3546 再 extract + compare ===")
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/extract_monthly_data.py",
         "--tickers", "3546", "--since", "2024"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines()[-12:]:
        log(line)

    # records 確認
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
    gcs = storage.Client(project="stock-data-1930932")
    d = json.loads(gcs.bucket("stock_data_1930932").blob("monthly/record/3546/monthly_records.json").download_as_text())
    log("records:")
    for rec in sorted(d.get("records", []), key=lambda r: r["year_month"])[-6:]:
        log(f"  {rec['year_month']}: {rec.get('fields')}")

    # compare
    r = subprocess.run(
        ["C:/venvs/investment-agent/Scripts/python.exe", "scripts/compare_monthly_buffett.py",
         "--offline", "--tickers", "3546"],
        capture_output=True, text=True, encoding="utf-8",
    )
    for line in r.stdout.strip().splitlines():
        if "2026-" in line or "一致率" in line or "不一致" in line:
            log(line)


def main() -> None:
    build_fiscal_year_history()
    patch_extract_monthly_data()
    write_3546_adapter()
    reextract_and_verify()


if __name__ == "__main__":
    main()
