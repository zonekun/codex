"""
実験: HAS_NG 17社に対して Gemini Pro + PDF 直接送信で抽出し BC 突合。
Flash との精度比較用。

Usage:
    PYTHONUTF8=1 uv run python scripts/experiment_gemini_pro_pdf.py
    PYTHONUTF8=1 uv run python scripts/experiment_gemini_pro_pdf.py --tickers 7649 2750
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import unicodedata as _ud
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

# プロジェクト設定
GCP_PROJECT = "gmailpj-357912"
VERTEXAI_REGION = "us-central1"
KEY_FILE = Path("keys/gcp-service-account.json")
GCS_BUCKET = "stock_data_1930932"
BC_CSV = Path("data/csv/bc_monthly_kpi.csv")
JST = timezone(timedelta(hours=9))
TOLERANCE = 0.5

# HAS_NG 17社（デフォルト対象）
DEFAULT_TICKERS = [
    "3160", "3175", "3178", "3195", "3358", "3663", "3796", "3817",
    "3983", "6238", "7610", "7625", "7649", "8715", "9936", "9973", "9990",
]

_MONTHLY_KW = re.compile(
    r"月次|月度|店舗売上|monthly|売上速報|業績速報|月別売上", re.IGNORECASE
)


def get_credentials():
    return service_account.Credentials.from_service_account_file(str(KEY_FILE))


def _normalize(s: str) -> str:
    return _ud.normalize("NFKC", s).replace("\u3000", " ").strip()


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def extract_with_gemini_pro_pdf(
    pdf_bytes: bytes,
    adapter: dict,
    doc_title: str,
    submission_date: str,
) -> Optional[dict]:
    """Gemini Pro に PDF を直接送信してメトリクス値を抽出する。"""
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True, project=GCP_PROJECT, location=VERTEXAI_REGION, credentials=get_credentials(),
    )

    fields = adapter.get("fields", [])
    if not fields:
        return None

    # 年月推定
    sys.path.insert(0, "scripts")
    from extract_monthly_data import _parse_year_month

    ym_hint = _parse_year_month(adapter, doc_title, submission_date)
    ym_hint_str = f"{ym_hint[0]:04d}-{ym_hint[1]:02d}" if ym_hint else "不明"
    target_month_str = f"{ym_hint[1]}月" if ym_hint else "当該月"

    field_lines = [
        f'- {f["key"]}: {f.get("description", f["key"])} (type: {f.get("value_type", "number")})'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)

    prompt = (
        f"以下の月次開示PDF文書から、指定フィールドの **{target_month_str}** の値を抽出してください。\n\n"
        f"文書タイトル: {doc_title}\n"
        f"提出日: {submission_date}\n"
        f"対象年月: {ym_hint_str}\n\n"
        f"【抽出フィールド】\n{field_desc}\n\n"
        f"【重要: 対象月の列を正確に選ぶこと】\n"
        f"- この文書には複数月のデータが横並びで含まれている場合があります\n"
        f"- 必ず **{target_month_str}の列** の値だけを返してください\n"
        f"- 12月/1月/2月/... のように月が並んでいる場合、{target_month_str}の列を特定して"
        f"  その列の数値を返すこと。隣の月の値を返さないこと\n"
        f"- 前年同月比は表に記載されている値をそのまま返すこと（例: 107.8ならば107.8）\n"
        f"- year_month: {ym_hint_str} を返すこと\n"
        f"- 2020〜2030の範囲の年の数字を値として返さないこと（年度表記）\n"
        f"- 値が見つからない場合は null を返すこと"
    )

    response_schema = {
        "type": "object",
        "properties": {
            "year_month": {"type": "string"},
            **{
                f["key"]: {
                    "type": "number"
                    if f.get("value_type") in ("integer", "float", "percentage")
                    else "string",
                    "nullable": True,
                }
                for f in fields
            },
        },
        "required": ["year_month"],
    }

    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[pdf_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=0,
            ),
        )
        data = json.loads(resp.text)
    except Exception as e:
        log(f"  Gemini Pro エラー: {e}")
        return None

    # year_month 解析
    try:
        ym_str = data.get("year_month", "")
        parts = str(ym_str).split("-")
        report_year = int(parts[0])
        report_month = int(parts[1])
        if not (2010 <= report_year <= 2035 and 1 <= report_month <= 12):
            raise ValueError
        year_month = f"{report_year:04d}-{report_month:02d}"
    except (ValueError, IndexError, AttributeError):
        if ym_hint:
            report_year, report_month = ym_hint
            year_month = f"{report_year:04d}-{report_month:02d}"
        else:
            return None

    fields_data: dict = {}
    for f in fields:
        key = f["key"]
        val = data.get(key)
        if val is None:
            continue
        val_type = f.get("value_type", "float")
        try:
            num = float(val)
            if 2015 <= num <= 2035:
                continue
            if val_type == "integer":
                fields_data[key] = int(num)
            else:
                fields_data[key] = num
        except (ValueError, TypeError):
            pass

    if not fields_data:
        return None

    return {
        "year": report_year,
        "month": report_month,
        "year_month": year_month,
        "source": "gemini_pro_pdf",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini Pro PDF 直接送信 実験")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument(
        "--baseline", default="C:/tmp/gemini64_baseline.csv", help="ベースライン CSV"
    )
    args = parser.parse_args()

    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    creds = get_credentials()
    gcs = storage.Client(credentials=creds, project=GCP_PROJECT)
    bucket = gcs.bucket(GCS_BUCKET)

    # BC データ
    bc_data: dict = defaultdict(lambda: defaultdict(dict))
    with open(BC_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                bc_data[row["ticker"]][row["year_month"]][row["field"]] = float(
                    row["value"]
                )
            except ValueError:
                pass

    # ベースライン
    baseline: dict = {}
    if Path(args.baseline).exists():
        with open(args.baseline, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                baseline[row["ticker"]] = row

    log(f"=== Gemini Pro PDF 実験 ({len(args.tickers)} 社) ===")

    results = []
    for idx, ticker in enumerate(args.tickers, 1):
        if ticker not in bc_data:
            log(f"  [{idx}/{len(args.tickers)}] {ticker} BC データなし → スキップ")
            continue

        try:
            adapter = json.loads(
                bucket.blob(f"monthlydata/{ticker}/extract_adapter.json").download_as_text()
            )
        except Exception:
            log(f"  [{idx}/{len(args.tickers)}] {ticker} adapter なし → スキップ")
            continue

        # 最新3月発表PDF
        blobs = [
            b
            for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/")
            if b.name.endswith(".pdf") and _MONTHLY_KW.search(Path(b.name).name)
        ]
        march = [b for b in blobs if Path(b.name).name[:6] == "202603"]
        if not march:
            march = sorted(blobs, key=lambda b: b.name, reverse=True)[:1]
        if not march:
            log(f"  [{idx}/{len(args.tickers)}] {ticker} PDF なし → スキップ")
            continue

        latest = sorted(march, key=lambda b: b.name, reverse=True)[0]
        fname = Path(latest.name).stem
        sub_date = (
            f"{fname[:4]}-{fname[4:6]}-{fname[6:8]}"
            if len(fname) >= 8 and fname[:8].isdigit()
            else ""
        )

        try:
            pdf_bytes = latest.download_as_bytes()
        except Exception:
            continue

        rec = extract_with_gemini_pro_pdf(pdf_bytes, adapter, fname, sub_date)
        if not rec:
            log(f"  [{idx}/{len(args.tickers)}] {ticker} 抽出失敗")
            continue

        # BC 突合
        ym_str = rec["year_month"]
        fields = rec.get("fields", {})
        bc_month = bc_data.get(ticker, {}).get(ym_str, {})
        ticker_ok = 0
        ticker_ng = 0
        ng_details = []

        for our_key, our_val in fields.items():
            our_norm = _normalize(our_key)
            best_bc_val = None
            best_score = 0
            for bc_key, bc_val in bc_month.items():
                score = (
                    100
                    if our_key == bc_key
                    else (80 if _normalize(bc_key) == our_norm else 0)
                )
                if score > best_score:
                    best_score = score
                    best_bc_val = bc_val

            our_val_adj = our_val
            for f_def in adapter.get("fields", []):
                fk = f_def.get("key") or f_def.get("name", "")
                if fk == our_key:
                    if f_def.get("yoy_offset"):
                        our_val_adj = our_val_adj + f_def["yoy_offset"]
                    if f_def.get("unit_scale") and f_def["unit_scale"] != 1:
                        our_val_adj = our_val * f_def["unit_scale"]
                    break

            bc_val_adj = best_bc_val
            for f_def in adapter.get("fields", []):
                fk = f_def.get("key") or f_def.get("name", "")
                if fk == our_key and f_def.get("bc_floor") and best_bc_val is not None:
                    bc_val_adj = math.floor(best_bc_val)
                    break

            if bc_val_adj is not None:
                diff = abs(our_val_adj - bc_val_adj)
                if diff <= TOLERANCE:
                    ticker_ok += 1
                else:
                    ticker_ng += 1
                    ng_details.append(
                        f"{ym_str} {our_key} our={our_val} bc={bc_val_adj} diff={diff:.1f}"
                    )

        status = "ALL_OK" if ticker_ng == 0 else "HAS_NG"
        base = baseline.get(ticker, {})
        base_status = base.get("status", "N/A")

        results.append(
            {
                "ticker": ticker,
                "status": status,
                "ok": ticker_ok,
                "ng": ticker_ng,
                "base_status": base_status,
                "base_ok": base.get("ok", ""),
                "base_ng": base.get("ng", ""),
                "change": "IMPROVED"
                if base_status == "HAS_NG" and status == "ALL_OK"
                else (
                    "DEGRADED"
                    if base_status == "ALL_OK" and status == "HAS_NG"
                    else "SAME"
                ),
                "ng_details": ng_details,
            }
        )
        log(
            f"  [{idx}/{len(args.tickers)}] {ticker} {status} OK={ticker_ok} NG={ticker_ng} (base={base_status})"
        )

    # 結果保存
    out_csv = f"C:/tmp/gemini_pro_experiment_{ts}.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ticker",
                "status",
                "ok",
                "ng",
                "base_status",
                "base_ok",
                "base_ng",
                "change",
                "ng_details",
            ],
        )
        w.writeheader()
        for r in results:
            row = {**r, "ng_details": " | ".join(r["ng_details"][:5])}
            w.writerow(row)

    # サマリー
    from collections import Counter

    changes = Counter(r["change"] for r in results)
    log(f"\n=== Gemini Pro 実験サマリー ===")
    log(f"  ALL_OK: {sum(1 for r in results if r['status'] == 'ALL_OK')}")
    log(f"  HAS_NG: {sum(1 for r in results if r['status'] == 'HAS_NG')}")
    log(f"  改善: {changes.get('IMPROVED', 0)}")
    log(f"  デグレ: {changes.get('DEGRADED', 0)}")
    log(f"  同等: {changes.get('SAME', 0)}")
    log(f"  CSV: {out_csv}")


if __name__ == "__main__":
    main()
