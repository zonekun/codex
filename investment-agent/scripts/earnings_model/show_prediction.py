"""決算予測の事前予想要因を銘柄単位で表示する.

反省会時に「その銘柄がなぜ UP/DOWN/NEUTRAL 予想になったか」を
prediction JSON から引いて表示する。ローカル（download_review_data.py で DL 済み）
を優先し、無ければ GCS から直接読み込む。

Usage:
    # 指定日・指定銘柄（複数可）
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/show_prediction.py 20260413 9948

    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/show_prediction.py 20260413 9948 3168 6217

    # actual も併記（実績リターン・方向一致）
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/show_prediction.py 20260413 9948 --with-actual
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from google.cloud import storage
from google.oauth2 import service_account

GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "earnings_model"
LOCAL_DIR = Path("C:/tmp/earnings_review")
KEY_PATH = Path("C:/gdrive/claude/investment-agent/keys/gcp-service-account.json")


def get_gcs_client() -> storage.Client:
    """GCS クライアントを構築する."""
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    return storage.Client(credentials=creds, project="and-and-and")


def load_json(predict_date: str, kind: str) -> dict[str, Any] | None:
    """prediction または actual JSON をロードする.

    ローカル優先、無ければ GCS から直接DLせず stream 読み込み。

    Args:
        predict_date: 対象日 (YYYYMMDD).
        kind: "prediction" or "actual".

    Returns:
        JSON 辞書。見つからなければ None.
    """
    local_path = LOCAL_DIR / f"{kind}_{predict_date}.json"
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            return json.load(f)

    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    if kind == "actual":
        # 新旧命名規約を両方探索（新: actual_*_for_{PREDICT_DATE}.json）
        all_blobs = list(bucket.list_blobs(prefix=f"{GCS_PREFIX}/earnings_reaction_actuals/actual_"))
        suffix_new = f"_for_{predict_date}.json"
        prefix_old = f"{GCS_PREFIX}/earnings_reaction_actuals/actual_{predict_date}_"
        candidates = [
            b for b in all_blobs
            if b.name.endswith(suffix_new) or b.name.startswith(prefix_old)
        ]
        if not candidates:
            print(f"not found: kind={kind} date={predict_date}")
            return None
        blob = max(candidates, key=lambda b: b.time_created)
    else:
        prefix = f"{GCS_PREFIX}/earnings_reaction_{kind}s/{kind}_{predict_date}"
        blobs = sorted(bucket.list_blobs(prefix=prefix), key=lambda b: b.name)
        if not blobs:
            print(f"not found: kind={kind} date={predict_date}")
            return None
        blob = blobs[-1]
    text = blob.download_as_text()
    return json.loads(text)


def _fmt(v: Any) -> str:
    """値を表示用にフォーマットする."""
    if v is None:
        return "-"
    if isinstance(v, float):
        if math.isnan(v):
            return "-"
        return f"{v:.4f}"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v)


def _fmt_pct(v: Any) -> str:
    """小数をパーセント表記にする."""
    if v is None:
        return "-"
    if isinstance(v, float) and math.isnan(v):
        return "-"
    try:
        return f"{float(v) * 100:+.2f}%"
    except (TypeError, ValueError):
        return str(v)


FIELD_ORDER = [
    ("ticker", "ticker", _fmt),
    ("name", "銘柄名", _fmt),
    ("industry_33", "業種33", _fmt),
    ("quarter", "四半期", _fmt),
    ("is_intraday", "ザラバ", _fmt),
    ("disc_time", "開示時刻", _fmt),
    ("score", "SCORE", _fmt),
    ("prediction", "予測", _fmt),
    ("reasons", "スコア理由", _fmt),
    ("progress_op", "進捗率OP", _fmt_pct),
    ("yoy_op", "YoY OP", _fmt_pct),
    ("baseline_yoy_op", "ベースラインYoY OP", _fmt_pct),
    ("qoq_op", "QoQ OP", _fmt_pct),
    ("consensus_deviation", "コンセ乖離", _fmt_pct),
    ("has_guidance_revision", "事前業績修正", _fmt),
    ("guidance_op_change", "ガイダンス変化", _fmt_pct),
    ("next_year_op_change", "来期OP変化", _fmt_pct),
    ("has_special_dividend", "特別配当", _fmt),
    ("div_change", "配当変化", _fmt_pct),
    ("has_buyback", "自社株買い", _fmt),
    ("selloff_risk", "売り圧力リスク", _fmt),
    ("per", "PER", _fmt),
    ("prev_close", "発表日前日終値", _fmt),
    ("adj_close", "発表日終値", _fmt),
]


def show_one(
    pred_data: dict[str, Any],
    actual_data: dict[str, Any] | None,
    ticker: str,
) -> None:
    """1銘柄の予測要因を表示する.

    Args:
        pred_data: prediction JSON.
        actual_data: actual JSON（None なら省略）.
        ticker: 対象 ticker.
    """
    pred = next(
        (p for p in pred_data.get("predictions", []) if p.get("ticker") == ticker),
        None,
    )
    if pred is None:
        print(f"ticker not found: {ticker}")
        return

    print(f"\n=== {ticker} {pred.get('name', '')} ===")
    for key, label, formatter in FIELD_ORDER:
        if key not in pred:
            continue
        print(f"  {label:<22}: {formatter(pred[key])}")

    if actual_data is not None:
        a = next(
            (a for a in actual_data.get("actuals", []) if a.get("ticker") == ticker),
            None,
        )
        if a is not None:
            print("  --- 実績 ---")
            before = a.get("compare_before_close")
            after = a.get("compare_after_close")
            if before is not None:
                print(f"  {'比較前終値':<22}: {_fmt(before)}")
            if after is not None:
                print(f"  {'比較後終値':<22}: {_fmt(after)}")
            print(f"  {'騰落率':<22}: {_fmt_pct(a.get('actual_return'))}")
            print(f"  {'実績カテゴリ':<22}: {_fmt(a.get('actual_category'))}")
            print(f"  {'方向一致':<22}: {_fmt(a.get('direction_match'))}")


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(
        description="決算予測の事前予想要因を銘柄単位で表示"
    )
    parser.add_argument("predict_date", help="予測日 YYYYMMDD")
    parser.add_argument("tickers", nargs="+", help="対象 ticker（複数可）")
    parser.add_argument(
        "--with-actual", action="store_true", help="actual も併記"
    )
    args = parser.parse_args()

    pred_data = load_json(args.predict_date, "prediction")
    if pred_data is None:
        print(f"prediction not found: {args.predict_date}")
        sys.exit(1)

    actual_data = None
    if args.with_actual:
        actual_data = load_json(args.predict_date, "actual")

    for t in args.tickers:
        show_one(pred_data, actual_data, t)


if __name__ == "__main__":
    main()
